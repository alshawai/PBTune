"""
Tests for SnapshotManager functionality.

Tests cover:
- Platform detection
- rsync snapshot creation and restore
- Config file preservation during restore
- Parallel restore functionality
"""

import shutil
import pytest

from src.tuner.utils.snapshot_manager import (
    SnapshotManager,
    SnapshotConfig,
    SnapshotMethod,
    detect_best_snapshot_method,
    RsyncStrategy,
    ShutilStrategy,
)


@pytest.fixture
def temp_snapshot_dir(tmp_path):
    """Create temporary directories for snapshot testing."""
    source = tmp_path / "source"
    baseline = tmp_path / "baseline"
    target = tmp_path / "target"

    # Create source with test data
    source.mkdir()
    (source / "base").mkdir()
    (source / "base" / "data.txt").write_text("original data")
    (source / "pg_wal").mkdir()
    (source / "pg_wal" / "wal_001").write_text("wal content")
    (source / "postgresql.conf").write_text("shared_buffers = 128MB")
    (source / "postgresql.auto.conf").write_text("port = 5432")

    return {
        "source": source,
        "baseline": baseline,
        "target": target,
        "tmp_path": tmp_path,
    }


class TestPlatformDetection:
    """Test platform and method detection."""

    def test_detect_method_returns_valid_method(self):
        """Detection should return a valid SnapshotMethod."""
        method = detect_best_snapshot_method()
        assert isinstance(method, SnapshotMethod)

    def test_rsync_available_on_linux(self):
        """rsync should be available on Linux with rsync installed."""
        strategy = RsyncStrategy()
        if shutil.which("rsync"):
            assert strategy.is_available()

    def test_shutil_always_available(self):
        """shutil strategy should always be available."""
        strategy = ShutilStrategy()
        assert strategy.is_available()


class TestRsyncStrategy:
    """Test rsync snapshot strategy."""

    def test_create_snapshot(self, temp_snapshot_dir):
        """Test snapshot creation."""
        if not shutil.which("rsync"):
            pytest.skip("rsync not installed")

        strategy = RsyncStrategy()
        success = strategy.create_snapshot(
            source_path=temp_snapshot_dir["source"],
            snapshot_path=temp_snapshot_dir["baseline"],
            excluded_files=[]
        )

        assert success
        assert (temp_snapshot_dir["baseline"] / "base" / "data.txt").exists()
        assert (temp_snapshot_dir["baseline"] / "postgresql.conf").exists()

    def test_restore_preserves_config(self, temp_snapshot_dir):
        """Test that restore preserves excluded config files."""
        if not shutil.which("rsync"):
            pytest.skip("rsync not installed")
        
        source = temp_snapshot_dir["source"]
        baseline = temp_snapshot_dir["baseline"]
        target = temp_snapshot_dir["target"]
        
        strategy = RsyncStrategy()
        
        # Create baseline
        strategy.create_snapshot(source, baseline, [])
        
        # Create target with modified files
        shutil.copytree(source, target)
        (target / "base" / "data.txt").write_text("modified data")
        (target / "postgresql.conf").write_text("port = 5440")  # Modified config
        (target / "new_file.txt").write_text("should be deleted")
        
        # Restore with config excluded
        success = strategy.restore_snapshot(
            snapshot_path=baseline,
            target_path=target,
            excluded_files=["postgresql.conf", "postgresql.auto.conf"]
        )
        
        assert success
        # Data should be restored to original
        assert (target / "base" / "data.txt").read_text() == "original data"
        # Config should be preserved (not restored)
        assert "5440" in (target / "postgresql.conf").read_text()
        # New file should be deleted
        assert not (target / "new_file.txt").exists()


class TestSnapshotManager:
    """Test SnapshotManager high-level operations."""
    
    def test_create_and_restore(self, temp_snapshot_dir):
        """Test full create and restore cycle."""
        source = temp_snapshot_dir["source"]
        baseline = temp_snapshot_dir["baseline"]
        target = temp_snapshot_dir["target"]
        
        config = SnapshotConfig(baseline_path=baseline)
        manager = SnapshotManager(config)
        
        # Create baseline
        assert manager.create_baseline(source)
        assert manager.baseline_created
        
        # Setup modified target
        shutil.copytree(source, target)
        (target / "base" / "data.txt").write_text("modified")
        (target / "postgresql.conf").write_text("port = 5440")
        
        # Restore
        assert manager.restore_worker(target)
        
        # Verify
        assert (target / "base" / "data.txt").read_text() == "original data"
        assert "5440" in (target / "postgresql.conf").read_text()
    
    def test_parallel_restore(self, temp_snapshot_dir):
        """Test parallel restore of multiple workers."""
        source = temp_snapshot_dir["source"]
        baseline = temp_snapshot_dir["baseline"]
        tmp_path = temp_snapshot_dir["tmp_path"]
        
        # Create multiple worker directories
        workers = []
        for i in range(4):
            worker = tmp_path / f"worker_{i}"
            shutil.copytree(source, worker)
            (worker / "base" / "data.txt").write_text(f"modified_{i}")
            (worker / "postgresql.conf").write_text(f"port = 544{i}")
            workers.append(worker)
        
        config = SnapshotConfig(
            baseline_path=baseline,
            parallel_restore=True,
            max_parallel_workers=4
        )
        manager = SnapshotManager(config)
        
        # Create baseline
        assert manager.create_baseline(source)
        
        # Restore all workers
        results = manager.restore_all_workers(workers)
        
        # All should succeed
        assert all(results.values())
        
        # Verify all workers
        for i, worker in enumerate(workers):
            assert (worker / "base" / "data.txt").read_text() == "original data"
            assert f"544{i}" in (worker / "postgresql.conf").read_text()
    
    def test_should_restore(self, temp_snapshot_dir):
        """Test restore interval logic."""
        config = SnapshotConfig(
            baseline_path=temp_snapshot_dir["baseline"],
            restore_interval=5
        )
        manager = SnapshotManager(config)
        manager.create_baseline(temp_snapshot_dir["source"])
        
        # Generation 1 should always restore
        assert manager.should_restore(1)
        
        # Generations 2-5 should not restore (interval=5)
        assert not manager.should_restore(2)
        assert not manager.should_restore(5)
        
        # Generation 6 should restore (5 gens after gen 1)
        assert manager.should_restore(6)
        
        # Generation 11 should restore
        assert manager.should_restore(11)
    
    def test_get_status(self, temp_snapshot_dir):
        """Test status reporting."""
        config = SnapshotConfig(baseline_path=temp_snapshot_dir["baseline"])
        manager = SnapshotManager(config)
        
        status = manager.get_status()
        
        assert "method" in status
        assert "baseline_path" in status
        assert "baseline_exists" in status
        assert status["method"] in [m.value for m in SnapshotMethod]
