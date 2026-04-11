"""
Environments Subpackage
=======================

Provides polymorphic database environment abstractions for managing
isolated PostgreSQL instances across Docker and Bare-Metal backends.
"""

from src.utils.environments.base import InstanceConfig, DatabaseEnvironment
from src.utils.environments.docker import DockerEnvironment
from src.utils.environments.bare_metal import BareMetalEnvironment
from src.utils.environments.factory import EnvironmentFactory

__all__ = [
    "InstanceConfig",
    "DatabaseEnvironment",
    "DockerEnvironment",
    "BareMetalEnvironment",
    "EnvironmentFactory",
]
