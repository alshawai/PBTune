"""
Notebook Setup Utilities
========================

Helper functions for Jupyter notebooks to configure Python path
and working directory.

Usage in notebooks:
-------------------
```python
from notebook_setup import setup_notebook
setup_notebook()
```

This will:
1. Add project root to sys.path
2. Change working directory to project root (so relative paths work)
3. Verify data directories exist
4. Print helpful status information

Why change working directory?
------------------------------
By changing to the project root, all relative paths in the codebase
work correctly (e.g., "data/tuner_knobs/minimal_knobs.csv") without
needing to modify core modules to use absolute paths.
"""

import sys
import os
from pathlib import Path


def get_project_root() -> Path:
    """
    Get the project root directory.

    Assumes notebooks are in project_root/notebooks/
    """
    return Path(__file__).parent.parent


def setup_notebook(verbose: bool = True):
    """
    Setup notebook environment for importing from src/.
    
    This function:
    1. Adds project root to sys.path (for imports)
    2. Changes working directory to project root (for relative paths)    
    """
    project_root = get_project_root()

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    original_cwd = Path.cwd()
    os.chdir(project_root)

    if verbose:
        print("Notebook Environment Setup\n")
        print(f"  🟢 Project root: {project_root}")
        print(f"  🟢 Changed working directory: {original_cwd} → {Path.cwd()}")
        print(f"  🟢 Added to sys.path: {project_root}")
