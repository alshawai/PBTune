"""
Tests for PostgresInstanceManager.

Validates instance creation, lifecycle management, and configuration
of multiple PostgreSQL instances for parallel PBT optimization.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from src.tuner.utils import PostgresInstanceManager, InstanceConfig
from src.config.database import DatabaseConfig


@pytest.fixture
def temp_base_dir(tmp_path):
    """Provide temporary base directory for instances."""
    return tmp_path / "pg_instances"


@pytest.fixture
def template_db_config():
    """Provide template database configuration for testing."""
    return DatabaseConfig(
        host='localhost',
        port=5432,
        dbname='postgres',
        user='postgres',
        password='postgres'
    )


@pytest.fixture
def instance_manager(temp_base_dir, template_db_config):
    """Provide instance manager with test configuration."""
    manager = PostgresInstanceManager(
        base_dir=str(temp_base_dir),
        base_port=15432,
        template_db_config=template_db_config
    )
    yield manager
    # Cleanup after test
    try:
        manager.stop_all()
        manager.cleanup(remove_data=True)
    except:
        pass  # Ignore cleanup errors in tests


class TestPostgresInstanceManager:
    """Tests for PostgresInstanceManager."""
    
    class TestSetup:
        """Tests for instance setup operations."""
        
        def test_setup_instances_creates_correct_count(self, instance_manager):
            """Test that setup_instances creates requested number of instances."""
            configs = instance_manager.setup_instances(num_workers=4)
            assert len(configs) == 4
        
        def test_setup_instances_assigns_sequential_ports(self, instance_manager):
            """Test that instances get sequential ports starting from base_port."""
            configs = instance_manager.setup_instances(num_workers=3)
            ports = [c.port for c in configs]
            assert ports == [15432, 15433, 15434]
        
        def test_setup_instances_creates_data_directories(self, instance_manager, temp_base_dir):
            """Test that setup_instances creates data directory for each worker."""
            instance_manager.setup_instances(num_workers=2)
            
            worker_0_dir = temp_base_dir / "worker_0"
            worker_1_dir = temp_base_dir / "worker_1"
            
            assert worker_0_dir.exists()
            assert worker_1_dir.exists()
        
        def test_setup_instances_creates_instance_configs(self, instance_manager):
            """Test that setup_instances returns InstanceConfig objects."""
            configs = instance_manager.setup_instances(num_workers=2)
            
            assert all(isinstance(c, InstanceConfig) for c in configs)
            assert configs[0].worker_id == 0
            assert configs[1].worker_id == 1
        
        @pytest.mark.parametrize("num_workers", [0, -1, -10])
        def test_setup_instances_with_invalid_count_raises_error(self, instance_manager, num_workers):
            """Test that setup_instances raises ValueError for invalid worker count."""
            with pytest.raises(ValueError, match="at least 1 worker"):
                instance_manager.setup_instances(num_workers)
        
        def test_setup_instances_with_force_recreate(self, instance_manager, temp_base_dir):
            """Test that force_recreate removes existing instances."""
            # Create initial instances
            instance_manager.setup_instances(num_workers=2)
            
            # Create marker file in first instance
            marker_file = temp_base_dir / "worker_0" / "test_marker.txt"
            marker_file.parent.mkdir(parents=True, exist_ok=True)
            marker_file.write_text("test")
            
            # Recreate with force
            instance_manager.setup_instances(num_workers=2, force_recreate=True)
            
            # Marker file should be gone
            assert not marker_file.exists()
    
    class TestLifecycle:
        """Tests for instance lifecycle operations."""
        
        @pytest.mark.integration
        def test_start_instance_starts_postgres(self, instance_manager):
            """Test that start_instance successfully starts PostgreSQL instance."""
            instance_manager.setup_instances(num_workers=1)
            
            result = instance_manager.start_instance(worker_id=0)
            
            assert result is True
        
        @pytest.mark.integration
        def test_stop_instance_stops_postgres(self, instance_manager):
            """Test that stop_instance successfully stops PostgreSQL instance."""
            instance_manager.setup_instances(num_workers=1)
            instance_manager.start_instance(worker_id=0)
            
            result = instance_manager.stop_instance(worker_id=0)
            
            assert result is True
        
        @pytest.mark.integration
        def test_start_all_starts_multiple_instances(self, instance_manager):
            """Test that start_all starts all configured instances."""
            instance_manager.setup_instances(num_workers=3)
            
            success_count = instance_manager.start_all()
            
            assert success_count == 3
        
        @pytest.mark.integration
        def test_stop_all_stops_multiple_instances(self, instance_manager):
            """Test that stop_all stops all running instances."""
            instance_manager.setup_instances(num_workers=2)
            instance_manager.start_all()
            
            success_count = instance_manager.stop_all()
            
            assert success_count == 2
        
        def test_start_instance_with_invalid_id_raises_error(self, instance_manager):
            """Test that start_instance raises KeyError for non-existent worker."""
            instance_manager.setup_instances(num_workers=2)
            
            with pytest.raises(KeyError):
                instance_manager.start_instance(worker_id=999)
        
        def test_stop_instance_with_invalid_id_raises_error(self, instance_manager):
            """Test that stop_instance raises KeyError for non-existent worker."""
            instance_manager.setup_instances(num_workers=2)
            
            with pytest.raises(KeyError):
                instance_manager.stop_instance(worker_id=999)
    
    class TestVerification:
        """Tests for instance verification operations."""
        
        @pytest.mark.integration
        def test_verify_instances_returns_true_when_all_running(self, instance_manager):
            """Test that verify_instances returns True when all instances respond."""
            instance_manager.setup_instances(num_workers=2)
            instance_manager.start_all()
            
            results = instance_manager.verify_instances()
            
            assert all(results.values())
            assert len(results) == 2
        
        @pytest.mark.integration
        def test_verify_instances_returns_false_when_stopped(self, instance_manager):
            """Test that verify_instances returns False for stopped instances."""
            instance_manager.setup_instances(num_workers=1)
            # Don't start instances
            
            results = instance_manager.verify_instances()
            
            assert not results[0]
    
    class TestCleanup:
        """Tests for cleanup operations."""
        
        def test_cleanup_without_removing_data(self, instance_manager, temp_base_dir):
            """Test that cleanup without remove_data keeps directories."""
            instance_manager.setup_instances(num_workers=2)
            
            instance_manager.cleanup(remove_data=False)
            
            assert (temp_base_dir / "worker_0").exists()
            assert (temp_base_dir / "worker_1").exists()
        
        def test_cleanup_with_removing_data(self, instance_manager, temp_base_dir):
            """Test that cleanup with remove_data removes directories."""
            instance_manager.setup_instances(num_workers=2)
            
            instance_manager.cleanup(remove_data=True)
            
            # Either directories don't exist or base_dir is empty
            if temp_base_dir.exists():
                assert len(list(temp_base_dir.iterdir())) == 0


class TestInstanceConfig:
    """Tests for InstanceConfig dataclass."""
    
    def test_instance_config_creation(self):
        """Test that InstanceConfig can be created with required fields."""
        config = InstanceConfig(
            worker_id=0,
            port=5440,
            data_dir="/path/to/data",
            db_config=DatabaseConfig(
                host="localhost",
                port=5440,
                dbname="test_db",
                user="test_user",
                password="test_pass"
            )
        )
        
        assert config.worker_id == 0
        assert config.port == 5440
        assert config.data_dir == "/path/to/data"
        assert config.db_config.port == 5440


# Integration test that can be run manually
@pytest.mark.manual
@pytest.mark.integration
def test_full_instance_lifecycle():
    """
    Full integration test for instance manager lifecycle.
    
    This test creates real PostgreSQL instances, starts them, verifies
    connectivity, and cleans up. Marked as 'manual' to avoid running
    in automated test suites.
    
    Run with: pytest tests/tuner/utils/test_instance_manager.py -m manual -v
    """
    template_config = DatabaseConfig(
        host='localhost',
        port=5432,
        dbname='postgres',
        user='postgres',
        password='postgres'
    )
    
    manager = PostgresInstanceManager(
        base_dir='./pg_instances_test',
        base_port=15440,
        template_db_config=template_config
    )
    
    try:
        # Setup
        print("\n1. Setting up instances...")
        instances = manager.setup_instances(num_workers=4)
        assert len(instances) == 4
        print(f"✓ Created {len(instances)} instances")
        
        # Start
        print("\n2. Starting instances...")
        started = manager.start_all()
        assert started == 4
        print(f"✓ Started {started} instances")
        
        # Verify
        print("\n3. Verifying connectivity...")
        results = manager.verify_instances()
        assert all(results.values())
        print("✓ All instances accessible")
        
        # Stop
        print("\n4. Stopping instances...")
        stopped = manager.stop_all()
        assert stopped == 4
        print(f"✓ Stopped {stopped} instances")
        
        print("\n✓ Full lifecycle test passed!")
        
    finally:
        # Cleanup
        print("\n5. Cleaning up...")
        manager.cleanup(remove_data=True)
        print("✓ Cleanup complete")


if __name__ == "__main__":
    # Allow running tests directly
    pytest.main([__file__, "-v"])
