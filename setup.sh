#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# setup.sh — One-command environment setup for the PBT tuning project
#
# Usage:
#   ./setup.sh              # Create .venv and install everything
#   ./setup.sh --dev        # Also install dev/test dependencies
#   ./setup.sh --clean      # Remove .venv and start fresh
#
# What it does:
#   1. Verifies Python >=3.11,<3.14 is available (pyrfr won't compile on 3.14+)
#   2. Checks that system packages 'swig' and 'g++' are installed
#   3. Creates a virtual environment in .venv/
#   4. Downloads, patches, and installs pyrfr (works around a rapidjson
#      const-assignment bug triggered by modern GCC)
#   5. Installs all remaining Python dependencies
#   6. Runs a quick smoke test to verify the installation
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv"
REQUIRED_PYTHON_MIN="3.11"
REQUIRED_PYTHON_MAX_EXCL="3.14"   # exclusive upper bound

# Colours (disabled when stdout is not a terminal)
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi

info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; }
die()   { error "$@"; exit 1; }

# Handle --clean
if [[ "${1:-}" == "--clean" ]]; then
    info "Removing existing virtual environment..."
    rm -rf "$VENV_DIR"
    info "Cleaned. Re-run without --clean to set up."
    exit 0
fi

DEV_MODE=false
if [[ "${1:-}" == "--dev" ]]; then
    DEV_MODE=true
fi

# 1. Find a compatible Python interpreter
find_python() {
    # Try common names in order of preference
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" &>/dev/null; then
            local ver
            ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
            if python3 -c "
import sys
parts_min = [int(x) for x in '$REQUIRED_PYTHON_MIN'.split('.')]
parts_max = [int(x) for x in '$REQUIRED_PYTHON_MAX_EXCL'.split('.')]
parts_cur = [int(x) for x in '$ver'.split('.')]
sys.exit(0 if parts_min <= parts_cur < parts_max else 1)
" 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON_CMD=$(find_python) || die \
    "No compatible Python found (need >=${REQUIRED_PYTHON_MIN}, <${REQUIRED_PYTHON_MAX_EXCL}).

On Arch Linux:  yay -S python313
On Ubuntu:      sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.13
On macOS:       brew install python@3.13
With pyenv:     pyenv install 3.13"

PYTHON_VER="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
info "Using ${BOLD}$PYTHON_CMD${NC} (Python $PYTHON_VER)"

# 2. Check system dependencies
missing=()
command -v swig &>/dev/null || missing+=("swig")
command -v g++  &>/dev/null || missing+=("g++ (gcc/gcc-c++)")

if [[ ${#missing[@]} -gt 0 ]]; then
    die "Missing system packages: ${missing[*]}

Install them first:
  Arch Linux:   sudo pacman -S swig gcc
  Ubuntu/Debian: sudo apt install swig g++
  Fedora/RHEL:  sudo dnf install swig gcc-c++
  macOS:        brew install swig gcc"
fi
info "System dependencies OK (swig, g++)"

# 3. Create virtual environment
if [[ -d "$VENV_DIR" ]]; then
    EXISTING_VER="$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "unknown")"
    WANTED_VER="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if [[ "$EXISTING_VER" != "$WANTED_VER" ]]; then
        warn "Existing .venv uses Python $EXISTING_VER, but $WANTED_VER is preferred."
        warn "Removing old .venv and recreating..."
        rm -rf "$VENV_DIR"
    else
        info "Virtual environment already exists (Python $EXISTING_VER)"
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment with $PYTHON_CMD..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "Virtual environment activated"

# Upgrade pip
pip install --upgrade pip --quiet

# 4. Install pyrfr (with patch for modern GCC)
if python -c "import pyrfr" 2>/dev/null; then
    info "pyrfr already installed, skipping"
else
    info "Installing pyrfr (patching rapidjson for modern GCC)..."

    PYRFR_BUILD_DIR=$(mktemp -d)
    trap 'rm -rf "$PYRFR_BUILD_DIR"' EXIT

    # Download source
    pip download pyrfr --no-binary :all: --no-deps -d "$PYRFR_BUILD_DIR" --quiet

    # Extract
    tar -xzf "$PYRFR_BUILD_DIR"/pyrfr-*.tar.gz -C "$PYRFR_BUILD_DIR"
    PYRFR_SRC="$(find "$PYRFR_BUILD_DIR" -maxdepth 1 -type d -name 'pyrfr-*')"

    # Patch: rapidjson's GenericStringRef::operator= tries to assign to a
    # const member, which GCC 14+ (with -Wtemplate-body) treats as an error.
    # The operator is never actually called, so deleting it is safe.
    DOCUMENT_H="$PYRFR_SRC/include/cereal/external/rapidjson/document.h"
    if grep -q 'operator=(const GenericStringRef& rhs) { s = rhs.s; length = rhs.length; }' "$DOCUMENT_H"; then
        sed -i 's|GenericStringRef& operator=(const GenericStringRef& rhs) { s = rhs.s; length = rhs.length; }|GenericStringRef\& operator=(const GenericStringRef\& rhs) = delete;|' "$DOCUMENT_H"
        info "Patched rapidjson document.h (const-assignment fix)"
    else
        info "rapidjson document.h already patched or different version"
    fi

    # Build and install
    pip install "$PYRFR_SRC" --quiet
    info "pyrfr installed successfully"
fi

# 5. Install Python dependencies
info "Installing Python dependencies..."
if [[ "$DEV_MODE" == true ]]; then
    pip install -r requirements-dev.txt --quiet
    info "Installed production + dev dependencies"
else
    pip install -r requirements.txt --quiet
    info "Installed production dependencies"
fi

# 6. Smoke test
info "Running import smoke test..."
python -c "
import pyrfr, fanova, smac, jenkspy, shap
import sklearn, pandas, numpy, scipy, psutil, yaml, docker
print('All key packages imported successfully')
" || die "Smoke test failed — some packages could not be imported"

echo ""
info "${BOLD}Setup complete!${NC}"
echo ""
echo "  Activate the environment:  source .venv/bin/activate"
echo "  Run tests:                 make test"
echo "  Quick tuning run:          python -m src.tuner.main --tier minimal --config rapid"
echo ""
