#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build_dbgen.sh
# Compiles the TPC-H reference implementation (dbgen) from the official
# TPC-H Tools repository and installs it to /opt/tpch/.
#
# Called during `docker build` — do not run interactively.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

TPCH_DIR="/opt/tpch"
REPO_URL="https://github.com/electrum/tpch-dbgen.git"

echo "[build_dbgen] Cloning TPC-H dbgen repository..."
git clone --depth=1 "${REPO_URL}" "${TPCH_DIR}"

echo "[build_dbgen] Compiling dbgen..."
cd "${TPCH_DIR}"

# Build only dbgen. The upstream make default also builds qgen, which is
# not required by this project and currently fails for POSTGRESQL defines.
make DATABASE=POSTGRESQL MACHINE=LINUX WORKLOAD=TPCH dbgen

echo "[build_dbgen] dbgen compiled successfully."
echo "[build_dbgen] Binary: ${TPCH_DIR}/dbgen"

# Verify the binary works
"${TPCH_DIR}/dbgen" -h 2>&1 | head -3 || true

echo "[build_dbgen] Done."
