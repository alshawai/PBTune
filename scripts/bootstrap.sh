#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# bootstrap.sh — Unified system and environment setup for PBTune
#
# Usage:
#   ./scripts/bootstrap.sh               # Full install
#   ./scripts/bootstrap.sh --dev         # Also install dev dependencies
#   ./scripts/bootstrap.sh --clean       # Remove .venv and start fresh
#   ./scripts/bootstrap.sh --skip-system # Skip system package installation
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

VENV_DIR=".venv"
REQUIRED_PYTHON_MIN="3.11"
REQUIRED_PYTHON_MAX_EXCL="3.14"

# Colors
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; BOLD=''; NC=''
fi

info()  { printf "${GREEN}[INFO]${NC}  %b\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %b\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %b\n" "$*" >&2; }
die()   { error "$@"; exit 1; }

# Parse flags
DEV_MODE=false
SKIP_SYSTEM=false
for arg in "$@"; do
    case "$arg" in
        --clean)
            info "Removing existing virtual environment..."
            rm -rf "$VENV_DIR"
            info "Cleaned. Re-run without --clean to set up."
            exit 0
            ;;
        --dev) DEV_MODE=true ;;
        --skip-system) SKIP_SYSTEM=true ;;
    esac
done

detect_pkg_manager() {
    if command -v apt-get &>/dev/null; then echo "apt"
    elif command -v dnf &>/dev/null; then echo "dnf"
    elif command -v yum &>/dev/null; then echo "yum"
    elif command -v pacman &>/dev/null; then echo "pacman"
    elif command -v zypper &>/dev/null; then echo "zypper"
    else die "No supported package manager found"; fi
}

install_system_packages() {
    local pm
    pm=$(detect_pkg_manager)
    info "● Detected package manager: $pm"
    
    info "Installing system dependencies..."
    case "$pm" in
        apt)
            sudo apt-get update
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
                build-essential libpq-dev libreadline-dev zlib1g-dev \
                libxml2-dev libssl-dev libffi-dev swig g++ curl
            ;;
        dnf|yum)
            sudo $pm install -y \
                gcc gcc-c++ make postgresql-devel readline-devel zlib-devel \
                libxml2-devel openssl-devel libffi-devel swig curl
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed \
                base-devel postgresql-libs readline zlib \
                libxml2 openssl libffi swig curl
            ;;
        zypper)
            sudo zypper install -y \
                gcc gcc-c++ make postgresql-devel readline-devel zlib-devel \
                libxml2-devel libopenssl-devel libffi-devel swig curl
            ;;
    esac
}

install_python() {
    if command -v python3.11 &>/dev/null || command -v python3.12 &>/dev/null || command -v python3.13 &>/dev/null; then
        info "Compatible Python already installed."
        return
    fi
    local pm
    pm=$(detect_pkg_manager)
    info "Installing Python 3.12..."
    case "$pm" in
        apt)
            sudo apt-get install -y software-properties-common
            sudo add-apt-repository -y ppa:deadsnakes/ppa
            sudo apt-get update
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3.12 python3.12-venv python3.12-dev
            ;;
        dnf|yum)
            sudo $pm install -y python3.12
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed python
            ;;
        zypper)
            sudo zypper install -y python312
            ;;
    esac
}

install_docker() {
    if command -v docker &>/dev/null; then
        info "● Docker already installed."
    else
        info "Installing Docker..."
        curl -fsSL https://get.docker.com -o get-docker.sh
        sudo sh get-docker.sh
        rm get-docker.sh
        sudo systemctl start docker
        sudo systemctl enable docker
    fi
    
    # Check if we can run docker without sudo
    if ! docker info &>/dev/null; then
        warn "Adding current user to docker group..."
        sudo usermod -aG docker "$USER"
        warn "You may need to log out and back in (or run 'newgrp docker') for group changes to take effect."
    fi
}

install_postgres_client() {
    if command -v psql &>/dev/null && command -v pg_isready &>/dev/null; then
        info "● PostgreSQL client tools already installed."
        return
    fi
    local pm
    pm=$(detect_pkg_manager)
    info "Installing PostgreSQL 16 client tools..."
    case "$pm" in
        apt)
            sudo DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-client-16
            ;;
        dnf|yum)
            sudo $pm install -y postgresql
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed postgresql-libs postgresql
            ;;
        zypper)
            sudo zypper install -y postgresql16
            ;;
    esac
}

install_sysbench() {
    if command -v sysbench &>/dev/null; then
        info "● Sysbench already installed."
        return
    fi
    info "Installing Sysbench..."
    local pm
    pm=$(detect_pkg_manager)
    case "$pm" in
        apt)
            curl -s https://packagecloud.io/install/repositories/akopytov/sysbench/script.deb.sh | sudo bash
            sudo DEBIAN_FRONTEND=noninteractive apt -y install sysbench
            ;;
        dnf|yum)
            curl -s https://packagecloud.io/install/repositories/akopytov/sysbench/script.rpm.sh | sudo bash
            sudo $pm -y install sysbench
            ;;
        pacman)
            sudo pacman -Sy --noconfirm --needed sysbench
            ;;
        zypper)
            warn "● Automatic Sysbench install not implemented for zypper. Please install from source."
            ;;
    esac
}

# --- 1. System Setup ---
if [[ "$SKIP_SYSTEM" == false ]]; then
    install_system_packages
    install_python
    install_docker
    install_postgres_client
    install_sysbench
else
    info "Skipping system package installation (--skip-system)"
fi

# Pull postgres docker image
info "Pulling postgres:16 docker image..."
sudo docker pull postgres:16 || docker pull postgres:16

# --- 2. Python Venv Setup ---

find_python() {
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

PYTHON_CMD=$(find_python) || die "No compatible Python found (need >=${REQUIRED_PYTHON_MIN}, <${REQUIRED_PYTHON_MAX_EXCL})."
PYTHON_VER="$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')"
info "Using ${BOLD}$PYTHON_CMD${NC} (Python $PYTHON_VER)"

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment with $PYTHON_CMD..."
    "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "● Virtual environment activated"

pip install --upgrade pip setuptools wheel --quiet

# --- 3. pyrfr patch for modern GCC ---
if python -c "import pyrfr" 2>/dev/null; then
    info "● pyrfr already installed, skipping"
else
    info "Installing pyrfr (patching rapidjson for modern GCC)..."
    PYRFR_BUILD_DIR=$(mktemp -d)
    trap 'rm -rf "$PYRFR_BUILD_DIR"' EXIT

    pip download pyrfr --no-binary :all: --no-deps -d "$PYRFR_BUILD_DIR" --quiet
    tar -xzf "$PYRFR_BUILD_DIR"/pyrfr-*.tar.gz -C "$PYRFR_BUILD_DIR"
    PYRFR_SRC="$(find "$PYRFR_BUILD_DIR" -maxdepth 1 -type d -name 'pyrfr-*')"

    DOCUMENT_H="$PYRFR_SRC/include/cereal/external/rapidjson/document.h"
    if grep -q 'operator=(const GenericStringRef& rhs) { s = rhs.s; length = rhs.length; }' "$DOCUMENT_H"; then
        sed -i 's|GenericStringRef& operator=(const GenericStringRef& rhs) { s = rhs.s; length = rhs.length; }|GenericStringRef\& operator=(const GenericStringRef\& rhs) = delete;|' "$DOCUMENT_H"
        info "● Patched rapidjson document.h (const-assignment fix)"
    fi

    info "Installing swig<4.0 to fix pyrfr bindings..."
    pip install "swig<4.0" --quiet
    pip install "$PYRFR_SRC" --no-cache-dir --no-build-isolation --quiet
    info "● pyrfr installed successfully"
fi

# --- 4. Install remaining dependencies ---
info "Installing Python dependencies..."
if [[ "$DEV_MODE" == true ]]; then
    pip install -r requirements-dev.txt --quiet
    info "Installed production + dev dependencies"
else
    pip install -r requirements.txt --quiet
    info "Installed production dependencies"
fi

# --- 5. Smoke Tests ---
info "Running smoke tests..."
python -c "import pyrfr, fanova, smac" && info "● Python packages imported" || die "Python smoke test failed"
sysbench --version >/dev/null && info "● sysbench is available" || warn "sysbench test failed"
pg_isready --version >/dev/null && info "● pg_isready is available" || warn "pg_isready test failed"

echo ""
info "${BOLD}Bootstrap complete!${NC}"
echo ""
echo "  Activate the environment:  source .venv/bin/activate"
echo "  Quick tuning run:          python -m src.tuner.main --tier minimal --config rapid"
echo ""
