"""
Environment Factory
===================

Handles environment instantiation with graceful fallback.
Defaults to DockerEnvironment for strict isolation, with a fallback
to BareMetalEnvironment if Docker is unavailable or explicitly disabled
via the `--no-docker` flag.
"""

from typing import Optional, Any
from pathlib import Path

import docker

from src.utils.logger import get_logger, get_isolation_warning_banner
from src.utils.environments.base import DatabaseEnvironment
from src.utils.environments.docker import DockerEnvironment
from src.utils.environments.bare_metal import BareMetalEnvironment
from src.tuner.evaluator.executor import BenchmarkExecutor
from src.config.database import DatabaseConfig

logger = get_logger(__name__)


class EnvironmentFactory:
    """Factory for creating execution environments."""

    @staticmethod
    def create(
        schema_provider: BenchmarkExecutor,
        use_docker: bool = True,
        base_dir: Path = Path("./pg_instances"),
        base_port: int = 5440,
        db_config: Optional[DatabaseConfig] = None,
        worker_resources: Optional[Any] = None,
        run_id: str = "tuner-run",
        container_prefix: str = "pbt-worker",
    ) -> DatabaseEnvironment:
        """Create the appropriate environment backend."""
        cpu_cores = worker_resources.cpu_cores if worker_resources else 0.0
        ram_bytes = worker_resources.ram_bytes if worker_resources else 0
        db_config = db_config or DatabaseConfig.from_env()

        if use_docker:
            try:
                # Test connectivity
                docker.from_env().ping()
                return DockerEnvironment(
                    run_id=run_id,
                    db_config=db_config,
                    schema_provider=schema_provider,
                    cpu_cores=cpu_cores,
                    ram_bytes=ram_bytes,
                    base_port=base_port,
                    base_dir=base_dir,
                    container_prefix=container_prefix,
                )
            except (ImportError, Exception) as e:
                logger.warning(get_isolation_warning_banner())
                logger.warning("Docker unavailable (%s), falling back to Bare Metal", e)

        # If no_docker or docker failed
        if not use_docker:
            logger.warning(get_isolation_warning_banner())

        return BareMetalEnvironment(
            run_id=run_id,
            db_config=db_config,
            schema_provider=schema_provider,
            base_port=base_port,
            base_dir=base_dir,
        )
