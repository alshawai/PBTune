"""Unit tests for the distributed fleet inventory parser."""

import pytest

from src.tuners.distributed.inventory import (
    DeviceSpec,
    InventoryError,
    parse_inventory,
)


def _minimal():
    return {
        "fleet": {"agent_port": 8770, "ssh_user": "pbt", "data_dir": "/srv/pbt"},
        "devices": [
            {"host": "10.0.0.11"},
            {"host": "10.0.0.12", "agent_port": 8771},
            {"host": "10.0.0.13", "ssh_user": "ubuntu"},
        ],
    }


def test_parse_assigns_worker_ids_by_order():
    inv = parse_inventory(_minimal())
    assert [d.worker_id for d in inv.devices] == [0, 1, 2]
    assert inv.size == 3


def test_fleet_defaults_and_per_device_overrides():
    inv = parse_inventory(_minimal())
    d0, d1, d2 = inv.devices
    # Fleet defaults propagate.
    assert d0.agent_port == 8770
    assert d0.ssh_user == "pbt"
    assert d0.data_dir == "/srv/pbt"
    # Per-device overrides win.
    assert d1.agent_port == 8771
    assert d2.ssh_user == "ubuntu"
    # Untouched keys still fall back to fleet defaults.
    assert d2.agent_port == 8770


def test_agent_base_url_and_display_name():
    d = DeviceSpec(worker_id=0, host="host-a", agent_port=9000, label="alpha")
    assert d.agent_base_url == "http://host-a:9000"
    assert d.display_name == "alpha"
    assert DeviceSpec(worker_id=1, host="host-b").display_name == "host-b"


def test_device_for_worker_and_bounds():
    inv = parse_inventory(_minimal())
    assert inv.device_for_worker(1).host == "10.0.0.12"
    with pytest.raises(InventoryError):
        inv.device_for_worker(3)


def test_validate_for_population():
    inv = parse_inventory(_minimal())
    inv.validate_for_population(3)  # exact fit ok
    inv.validate_for_population(2)  # fleet larger than population ok
    with pytest.raises(InventoryError):
        inv.validate_for_population(4)  # more workers than devices


def test_missing_devices_section_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"fleet": {}})


def test_empty_devices_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"devices": []})


def test_device_missing_host_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"devices": [{"agent_port": 8770}]})


def test_duplicate_endpoint_raises():
    raw = {"devices": [{"host": "h1", "agent_port": 8770}, {"host": "h1"}]}
    with pytest.raises(InventoryError):
        parse_inventory(raw)


def test_unknown_device_key_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"devices": [{"host": "h1", "bogus": 1}]})


def test_unknown_fleet_key_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"fleet": {"nope": 1}, "devices": [{"host": "h1"}]})


def test_invalid_port_raises():
    with pytest.raises(InventoryError):
        parse_inventory({"devices": [{"host": "h1", "agent_port": 99999}]})


def test_ssh_key_is_expanded(monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    inv = parse_inventory(
        {"fleet": {"ssh_key": "~/.ssh/id_rsa"}, "devices": [{"host": "h1"}]}
    )
    assert inv.devices[0].ssh_key == "/home/tester/.ssh/id_rsa"


def test_roundtrip_to_dict():
    inv = parse_inventory(_minimal())
    assert isinstance(inv.to_dict()["devices"], list)
    assert inv.to_dict()["devices"][0]["host"] == "10.0.0.11"
