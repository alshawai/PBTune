import docker, time, sys
from pathlib import Path

client = docker.from_env()

# 1. Start worker 1 container with empty dir (simulating setup_instances)
test_dir = Path("/tmp/pbt_test_restore_pgdata")
if test_dir.exists():
    client.containers.run("postgres:18", entrypoint=["rm", "-rf", "/host/pbt_test_restore_pgdata"], volumes={"/tmp": {"bind": "/host", "mode": "rw"}}, remove=True)
test_dir.mkdir(parents=True, exist_ok=True)
test_dir.chmod(0o777)

print("Starting initial container...")
c1 = client.containers.run("postgres:18", detach=True, name="test-worker-1", environment={"POSTGRES_PASSWORD": "123", "PGDATA": "/pgdata/data"}, volumes={str(test_dir): {"bind": "/pgdata/data", "mode": "rw"}})

time.sleep(5)
c1.remove(force=True)
print("Removed initial container")

# 2. Re-create directory
client.containers.run("postgres:18", entrypoint=["rm", "-rf", "/host/pbt_test_restore_pgdata"], volumes={"/tmp": {"bind": "/host", "mode": "rw"}}, remove=True)
test_dir.mkdir(parents=True, exist_ok=True)
test_dir.chmod(0o777)

# 3. Copy snapshot
snapshot_dir = Path("/home/eima40x4c/Projects/Population-Based Training for Automatic Database Parameter Tuning/.instances/.snapshots/pg-snapshot-baseline-900932e1605c/pgdata")
print("Copying snapshot...")
client.containers.run("postgres:18", entrypoint=["bash", "-lc"], command="set -euo pipefail; cp -a /source/. /dest/", volumes={str(snapshot_dir): {"bind": "/source", "mode": "ro"}, str(test_dir): {"bind": "/dest", "mode": "rw"}}, remove=True)

# 4. Start second container
print("Starting second container...")
c2 = client.containers.run("postgres:18", detach=True, name="test-worker-2", environment={"POSTGRES_PASSWORD": "123", "PGDATA": "/pgdata/data"}, volumes={str(test_dir): {"bind": "/pgdata/data", "mode": "rw"}})

time.sleep(5)
print(c2.logs().decode("utf-8"))
c2.remove(force=True)

