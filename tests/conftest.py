"""
Shared pytest fixtures and configuration for all tests.

This file is automatically discovered by pytest and provides
fixtures that can be used across all test files.
"""

import pytest
from pathlib import Path


@pytest.fixture(scope="session")
def project_root():
    """Provide the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def data_dir(project_root):
    """Provide the data directory."""
    return project_root / "data"


@pytest.fixture(scope="session")
def test_data_dir(tmp_path_factory):
    """Provide a temporary directory for test data."""
    return tmp_path_factory.mktemp("test_data")


# Configure pytest markers
def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (require real resources)",
    )
    config.addinivalue_line(
        "markers", "manual: marks tests as manual (not run in automated CI)"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
