"""
Analysis Module
===============

Tools and data structures for analyzing Population Based Training (PBT) outputs,
normalizing workload metrics, and encoding state for machine learning workflows.
"""

from .data_loader import LoadedData, load_pbt_results

__all__ = ["load_pbt_results", "LoadedData"]
