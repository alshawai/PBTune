"""ASCII banners for the application."""

from typing import Any

def print_bo_startup_banner(logger: Any = None) -> None:
    """
    Print a colorful ASCII art banner for the Bayesian Optimization runner.
    If a logger is provided, it can be bypassed to use raw stdout to avoid 
    timestamp clutter.
    """
    banner = r"""
    ____  ____       ______                     
   / __ )/ __ \     /_  __/_  ______  ___  _____
  / __  / / / /      / / / / / / __ \/ _ \/ ___/
 / /_/ / /_/ /      / / / /_/ / / / /  __/ /    
/_____/\____/      /_/  \__,_/_/ /_/\___/_/     
                                                
   Bayesian Optimization (SMAC) Baseline Runner
==================================================
"""
    # Use standard print to bypass logging formats (timestamps, etc.)
    # to maintain clean ASCII output just like the PBT banner.
    print(banner)
