from pathlib import Path
import psycopg2
import docker

client = docker.from_env()

snapshot_dir = Path("/home/eima40x4c/Projects/Population-Based Training for Automatic Database Parameter Tuning/.instances/.snapshots/pg-snapshot-baseline-900932e1605c/pgdata")

print("Checking snapshot directory contents for base/")
res = client.containers.run("postgres:18", entrypoint=["ls", "-la", "/dest/base"], volumes={str(snapshot_dir): {"bind": "/dest", "mode": "ro"}}, remove=True)
print(res.decode())

# Check size of the database
res = client.containers.run("postgres:18", entrypoint=["du", "-sh", "/dest"], volumes={str(snapshot_dir): {"bind": "/dest", "mode": "ro"}}, remove=True)
print("Total size:", res.decode())
