"""Unit tests for the SSH bootstrap command builders (no network I/O)."""

from src.tuners.distributed.bootstrap import (
    RemoteLayout,
    install_deps_command,
    launch_agent_command,
    rsync_command,
    ssh_command,
    stop_agent_command,
)
from src.tuners.distributed.inventory import DeviceSpec


def _device(**kw):
    base = dict(
        worker_id=2,
        host="10.0.0.13",
        agent_port=8772,
        ssh_user="pbt",
        ssh_key="/home/u/.ssh/id_rsa",
        data_dir="/var/lib/pbt",
        python="python3",
    )
    base.update(kw)
    return DeviceSpec(**base)


def test_remote_layout_paths():
    layout = RemoteLayout.for_device(_device())
    assert layout.root == "/var/lib/pbt"
    assert layout.code_dir == "/var/lib/pbt/code"
    assert layout.instances_dir == "/var/lib/pbt/instances"
    assert layout.pid_file.endswith("agent-worker-2.pid")
    assert layout.log_file.endswith("agent-worker-2.log")


def test_ssh_command_includes_key_and_target():
    argv = ssh_command(_device(), "echo hi")
    assert argv[0] == "ssh"
    assert "-i" in argv and "/home/u/.ssh/id_rsa" in argv
    assert "pbt@10.0.0.13" in argv
    assert argv[-1] == "echo hi"
    assert "BatchMode=yes" in argv


def test_ssh_command_without_user_or_key():
    argv = ssh_command(_device(ssh_user=None, ssh_key=None), "ls")
    assert "10.0.0.13" in argv  # bare host, no user@
    assert "-i" not in argv


def test_rsync_command_has_excludes_and_trailing_slashes():
    argv = rsync_command(_device(), "/local/repo", "/var/lib/pbt/code")
    assert argv[0] == "rsync"
    assert "--delete" in argv
    # exclude patterns present
    assert "--exclude" in argv and ".git" in argv and ".venv" in argv
    # source has trailing slash; dest is host:dir/
    assert argv[-2] == "/local/repo/"
    assert argv[-1] == "pbt@10.0.0.13:/var/lib/pbt/code/"
    # -e ssh string carries the key
    e_idx = argv.index("-e")
    assert "id_rsa" in argv[e_idx + 1]


def test_launch_agent_command_wires_worker_and_port():
    layout = RemoteLayout.for_device(_device())
    cmd = launch_agent_command(_device(), layout, knob_tier="core")
    assert "-m src.tuners.distributed.device_agent" in cmd
    assert "--worker-id 2" in cmd
    assert "--port 8772" in cmd
    assert "--knob-tier core" in cmd
    assert "--base-dir" in cmd and "/var/lib/pbt/instances" in cmd
    assert "nohup" in cmd
    assert layout.pid_file in cmd
    assert layout.log_file in cmd


def test_launch_agent_command_env_exports():
    layout = RemoteLayout.for_device(_device())
    cmd = launch_agent_command(
        _device(), layout, knob_tier="core", env_exports={"DB_PASSWORD": "s3cret"}
    )
    assert "DB_PASSWORD=s3cret" in cmd


def test_install_deps_command():
    layout = RemoteLayout.for_device(_device())
    cmd = install_deps_command(layout, _device())
    assert "pip install" in cmd and "requirements.txt" in cmd
    assert "/var/lib/pbt/code" in cmd


def test_stop_agent_command_uses_pidfile():
    layout = RemoteLayout.for_device(_device())
    cmd = stop_agent_command(layout)
    assert "kill" in cmd
    assert layout.pid_file in cmd
