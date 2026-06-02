"""
Environment Factory
===================

Handles environment instantiation with graceful fallback.
Defaults to DockerEnvironment for strict isolation, with a fallback
to BareMetalEnvironment if Docker is unavailable or explicitly disabled
via the `--no-docker` flag.
"""

import os
import re
from typing import Optional, Any
from pathlib import Path

import docker

from src.utils.logger import get_logger, get_color_context, get_isolation_warning_banner
from src.utils.environments.base import DatabaseEnvironment
from src.utils.environments.docker import DockerEnvironment
from src.utils.environments.bare_metal import BareMetalEnvironment
from src.benchmarks.executor import BenchmarkExecutor
from src.config.database import DatabaseConfig
from src.utils.hardware_info import detect_pg_version

LOGGER = get_logger("EnvironmentFactory")
COLORS = get_color_context()


class EnvironmentFactory:
    """Factory for creating execution environments."""

    @staticmethod
    def _extract_pg_major(version_output: str) -> Optional[str]:
        """Extract major PostgreSQL version from version command output."""
        match = re.search(r"(\d+)(?:\.\d+)?", version_output)
        if not match:
            return None
        return match.group(1)

    @staticmethod
    def _resolve_docker_image(image_name: Optional[str]) -> str:
        """Resolve Docker image in priority order: CLI arg, env var, host version, fallback."""
        if image_name:
            return image_name

        env_image = os.getenv("PBT_POSTGRES_IMAGE")
        if env_image:
            return env_image

        detected_version = detect_pg_version()
        major = EnvironmentFactory._extract_pg_major(detected_version)
        if major:
            resolved = f"postgres:{major}"
            LOGGER.debug(
                "➤ Resolved Docker PostgreSQL image '%s' from host version '%s'",
                resolved,
                detected_version,
            )
            return resolved

        fallback = "postgres:18"
        LOGGER.warning(
            "Could not detect host PostgreSQL version (detected='%s'); using fallback image '%s'",
            detected_version,
            fallback,
        )
        return fallback

    @staticmethod
    def create(
        schema_provider: BenchmarkExecutor,
        use_docker: bool = True,
        base_dir: Path = Path("./.instances"),
        base_port: int = 5440,
        db_config: Optional[DatabaseConfig] = None,
        worker_resources: Optional[Any] = None,
        run_id: str = "tuner-run",
        container_prefix: str = "pbt-worker",
        image_name: Optional[str] = None,
        force_recreate_baseline: bool = False,
    ) -> DatabaseEnvironment:
        """Create the appropriate environment backend."""
        cpu_cores = worker_resources.cpu_cores if worker_resources else 0.0
        ram_bytes = worker_resources.ram_bytes if worker_resources else 0
        db_config = db_config or DatabaseConfig.from_env()

        if use_docker:
            try:
                LOGGER.info(
                    "Attempting to create Docker environment with image '%s'...",
                    image_name or "auto-resolve",
                )
                # Test connectivity
                docker.from_env().ping()
                resolved_image_name = EnvironmentFactory._resolve_docker_image(
                    image_name
                )
                return DockerEnvironment(
                    run_id=run_id,
                    db_config=db_config,
                    schema_provider=schema_provider,
                    cpu_cores=cpu_cores,
                    ram_bytes=ram_bytes,
                    worker_resources=worker_resources,
                    image_name=resolved_image_name,
                    base_port=base_port,
                    base_dir=base_dir,
                    container_prefix=container_prefix,
                    force_recreate_baseline=force_recreate_baseline,
                )
            except (
                ImportError,
                docker.errors.DockerException,
                OSError,
                RuntimeError,
                ValueError,
            ) as e:
                LOGGER.warning(get_isolation_warning_banner())
                LOGGER.warning(
                    "%sDocker unavailable (%s), falling back to Bare Metal%s",
                    COLORS.warning,
                    e,
                    COLORS.reset,
                )

        # If no_docker or docker failed
        if not use_docker:
            LOGGER.warning(get_isolation_warning_banner())

        return BareMetalEnvironment(
            run_id=run_id,
            db_config=db_config,
            schema_provider=schema_provider,
            base_port=base_port,
            base_dir=base_dir,
            ram_bytes=ram_bytes,
            force_recreate_baseline=force_recreate_baseline,
        )
