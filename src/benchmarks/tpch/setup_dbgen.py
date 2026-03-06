"""
TPC-H dbgen Setup
=================

Automatically locates or compiles the TPC-H data generator (dbgen).
Uses the trusted `electrum/tpch-dbgen` mirror — the standard
lightweight fork used in academic database tuning research.

Prerequisites: gcc, make (apt install build-essential)
"""

import logging
import shutil
import subprocess
from pathlib import Path
import re

logger = logging.getLogger(__name__)

TPCH_DIR = Path(__file__).parent
DBGEN_REPO = "https://github.com/electrum/tpch-dbgen.git"
DBGEN_SRC_DIR = TPCH_DIR / "tpch-dbgen"


def find_or_build_dbgen() -> Path:
    """
    Locate an existing dbgen binary or compile from source.

    Search order:
    1. Pre-compiled binary at src/benchmarks/tpch/tpch-dbgen/dbgen
    2. System PATH
    3. Auto-clone and compile from GitHub

    Returns
    -------
    Path
        Absolute path to the dbgen executable.

    Raises
    ------
    RuntimeError
        If compilation fails (missing gcc/make).
    """
    local_bin = DBGEN_SRC_DIR / "dbgen"
    if local_bin.exists() and local_bin.is_file():
        logger.debug("Found local dbgen: %s", local_bin)
        return local_bin.resolve()

    system_bin = shutil.which("dbgen")
    if system_bin:
        logger.debug("Found dbgen in PATH: %s", system_bin)
        return Path(system_bin).resolve()

    logger.info("dbgen not found — compiling from source...")
    return _compile_dbgen()


def _compile_dbgen() -> Path:
    """Clone electrum/tpch-dbgen and compile with make."""

    if not shutil.which("gcc"):
        raise RuntimeError(
            "gcc not found. Install build tools:\n"
            "  Ubuntu/Debian: sudo apt install build-essential\n"
            "  macOS: xcode-select --install"
        )

    if not shutil.which("make"):
        raise RuntimeError(
            "make not found. Install build tools:\n"
            "  Ubuntu/Debian: sudo apt install build-essential"
        )

    # Clone if source directory doesn't exist
    if not DBGEN_SRC_DIR.exists():
        logger.info("Cloning %s ...", DBGEN_REPO)
        subprocess.run(
            ["git", "clone", "--depth", "1", DBGEN_REPO, str(DBGEN_SRC_DIR)],
            check=True,
            capture_output=True,
        )

    # Patch the makefile for compilation on modern Linux.
    #
    # DATABASE note: tpcd.h only supports DB2, INFORMIX, ORACLE,
    # SQLSERVER, SYBASE, TDAT — there is no POSTGRESQL option.
    # We use ORACLE (simplest defines) since we only run dbgen for
    # .tbl data generation, not qgen. Our own SQL files handle
    # PostgreSQL query syntax.
    makefile_path = DBGEN_SRC_DIR / "makefile"
    if makefile_path.exists():
        content = makefile_path.read_text()
        patched = re.sub(r'^DATABASE\s*=\s*.*$', 'DATABASE= ORACLE', content, flags=re.MULTILINE)
        patched = re.sub(r'^MACHINE\s*=\s*.*$', 'MACHINE = LINUX', patched, flags=re.MULTILINE)
        patched = re.sub(r'^WORKLOAD\s*=\s*.*$', 'WORKLOAD = TPCH', patched, flags=re.MULTILINE)

        # Fix compilation on GCC 14+: the legacy C code uses K&R-style
        # function pointers (empty parens = unspecified args) which
        # modern C23 treats as zero-argument declarations.
        if '-std=gnu89' not in patched:
            patched = re.sub(
                r'^(CFLAGS\s*=\s*)',
                r'\1-std=gnu89 ',
                patched,
                flags=re.MULTILINE,
            )
        if patched != content:
            makefile_path.write_text(patched)
            logger.debug("Patched makefile: DATABASE=ORACLE, MACHINE=LINUX, CFLAGS+=-std=gnu89")

    # Clean previous failed build artifacts
    subprocess.run(
        ["make", "clean"],
        cwd=str(DBGEN_SRC_DIR),
        capture_output=True,
        check=False,
    )

    # Build with relaxed warnings for legacy C code.
    # GCC 14+ treats -Wincompatible-pointer-types as error by default,
    # but the old tpch-dbgen codebase relies on implicit pointer casts.
    env = {**subprocess.os.environ, "CFLAGS": "-O2 -Wno-error=incompatible-pointer-types"}
    logger.info("Compiling dbgen...")
    result = subprocess.run(
        ["make", "-j4"],
        cwd=str(DBGEN_SRC_DIR),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"dbgen compilation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    dbgen_path = DBGEN_SRC_DIR / "dbgen"
    if not dbgen_path.exists():
        raise RuntimeError(
            f"Compilation succeeded but dbgen binary not found at {dbgen_path}.\n"
            f"Build output: {result.stdout}"
        )

    logger.info("✓ dbgen compiled successfully: %s", dbgen_path)
    return dbgen_path.resolve()


def generate_data(dbgen_path: Path, scale_factor: float = 1.0) -> Path:
    """
    Generate TPC-H .tbl data files using dbgen.

    Parameters
    ----------
    dbgen_path : Path
        Path to the dbgen executable.
    scale_factor : float
        TPC-H scale factor (1.0 = ~1GB, 0.1 = ~100MB).

    Returns
    -------
    Path
        Directory containing the generated .tbl files.
    """
    output_dir = DBGEN_SRC_DIR
    marker = output_dir / f".generated_sf{scale_factor}"

    if marker.exists():
        logger.debug("TPC-H data already generated for SF=%.1f", scale_factor)
        return output_dir

    logger.info("Generating TPC-H data (SF=%.1f)...", scale_factor)

    # Clean old .tbl files and stale markers to avoid dbgen's
    # interactive overwrite prompts or "Open failed" errors.
    for old_marker in output_dir.glob(".generated_sf*"):
        old_marker.unlink()
    for old_tbl in output_dir.glob("*.tbl"):
        old_tbl.unlink()

    result = subprocess.run(
        [str(dbgen_path), "-vf", "-s", str(scale_factor)],
        cwd=str(output_dir),
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"dbgen data generation failed:\n{result.stderr}")

    # Verify key files exist
    expected_files = [
        "lineitem.tbl", "orders.tbl", "customer.tbl", "part.tbl",
        "partsupp.tbl", "supplier.tbl", "nation.tbl", "region.tbl",
    ]
    for fname in expected_files:
        if not (output_dir / fname).exists():
            raise RuntimeError(f"Expected data file missing: {fname}")

    # Write marker so we don't regenerate
    marker.write_text(f"SF={scale_factor}\n")

    logger.info("✓ TPC-H data generated (SF=%.1f) in %s", scale_factor, output_dir)
    return output_dir
