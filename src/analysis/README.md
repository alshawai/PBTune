# PBT Analysis & fANOVA Variance Decomposition

This directory contains the core modules for evaluating the tuning results from the Population-Based Training (PBT) optimization pipeline.

The most critical component here is **fANOVA** (Functional Analysis of Variance), which mathematically calculates the precise marginal importance and pairwise interactions of each PostgreSQL tuning knob based on the results obtained during tuning.

---

## ⚠️ fANOVA Setup & Strict Dependencies

`fANOVA` internally relies on `pyrfr`, which is a Python C++ wrapper around a highly optimized native Random Forest model. Due to strict SWIG/C-API restrictions, **you MUST use Python 3.9** and build the dependencies locally.

### 1. Install OS Dependencies (C++ Compiler & SWIG)
Before installing any Python packages, your OS must have the utilities required to compile `pyrfr` from source.

**Ubuntu / Debian / WSL:**
```bash
sudo apt-get update
sudo apt-get install build-essential swig
```

**macOS:**
```bash
brew check
brew install swig
```

*(Note: Windows is heavily discouraged due to MSVC compilation complexities. Stick to WSL or Linux if possible).*

### 2. Configure Python 3.9 Environment
Create a dedicated environment explicitly locked to Python 3.9, as `pyrfr`'s SWIG bindings will crash during runtime on Python 3.10 and newer.

```bash
# Using Conda
conda create -n pbt-env python=3.9
conda activate pbt-env
```

### 3. Install Python Dependencies
Once the dependencies are loaded, install the required packages:

```bash
# Navigate to the repository root
pip install -r requirements.txt
```

---


### Understanding the Pipeline
- **`data_loader.py`**: Merges multiple distributed JSON node results, parses hardware capabilities, dynamically calculates integer/byte configuration boundaries, and normalizes execution latency/throughput metrics onto an objective scoring plane.
- **`importance.py`**: Interfaces directly with the C++ `fANOVA` wrapper to map the loaded data onto the true `ConfigSpace` and produce interaction vectors.
