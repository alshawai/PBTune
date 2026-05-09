"""
Auto-discovery trigger for plot implementations.
Importing this package triggers the registration of all its submodules.
"""

from src.visualization.registry import REGISTRY

# Automatically trigger discovery when this package is imported
REGISTRY._discover_plots()
