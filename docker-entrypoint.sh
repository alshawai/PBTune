#!/bin/bash
set -e

echo "Starting PBT Docker Tuner Setup..."

# Ensure we can talk to the Docker daemon
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    echo "Is the docker daemon running ? "
    echo "Make sure to map the docker socket in docker-compose.yml: - /var/run/docker.sock:/var/run/docker.sock"
    exit 1
fi

echo "Docker daemon accessible."
export PBT_IN_DOCKER=1

# Execute the main command
exec "$@"
