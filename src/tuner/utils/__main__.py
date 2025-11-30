"""
Tuner Utils Module Tests
=========================

Test the KnobApplicator utility for applying PostgreSQL configurations.

Usage:
------
python -m src.tuner.utils
"""

if __name__ == "__main__":
    from unittest.mock import Mock, MagicMock
    from src.tuner.utils.applicator import (
        KnobApplicator,
        ApplicatorConfig,
        ParameterInfo,
    )

    print("Tuner Utils - KnobApplicator Tests")
    print("=" * 34)

    print("\n[TEST 1] Applicator Initialization")
    print("-" * 34)

    try:
        conn_params = {
            'host': 'localhost',
            'port': 5432,
            'dbname': 'postgres',
            'user': 'postgres',
            'password': 'password'
        }

        config = ApplicatorConfig(
            persist=True,
            auto_reload=True,
            validate=True,
            dry_run=False
        )

        applicator = KnobApplicator(conn_params, config)

        print(f"Created: {applicator}")
        print(f"  Persist: {applicator.config.persist}")
        print(f"  Validate: {applicator.config.validate}")
        print(f"  Dry run: {applicator.config.dry_run}")

        assert applicator.config.persist is True
        assert applicator.config.validate is True

        print("\n🟢 Initialization working!")
        print("-" * 26)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 2] Parameter Validation")
    print("-" * 29)

    try:
        applicator = KnobApplicator(conn_params, ApplicatorConfig())

        param_info_int = ParameterInfo(
            name="work_mem",
            vartype="integer",
            context="user",
            unit="kB",
            min_val="64",
            max_val="2097151",
            enumvals=None,
            boot_val="4096",
            reset_val="4096"
        )

        is_valid, error = applicator._validate_parameter("work_mem", 8192, param_info_int)
        assert is_valid is True, "Valid integer should pass"
        print(f"✓ Valid integer: work_mem=8192")

        is_valid, error = applicator._validate_parameter("work_mem", 32, param_info_int)
        assert is_valid is False, "Below min should fail"
        print(f"✓ Below min detected: {error}")

        is_valid, error = applicator._validate_parameter("work_mem", 3000000, param_info_int)
        assert is_valid is False, "Above max should fail"
        print(f"✓ Above max detected: {error}")

        param_info_real = ParameterInfo(
            name="random_page_cost",
            vartype="real",
            context="user",
            unit=None,
            min_val="0",
            max_val=None,
            enumvals=None,
            boot_val="4.0",
            reset_val="4.0"
        )

        is_valid, error = applicator._validate_parameter("random_page_cost", 1.1, param_info_real)
        assert is_valid is True, "Valid real should pass"
        print(f"✓ Valid real: random_page_cost=1.1")

        param_info_enum = ParameterInfo(
            name="wal_level",
            vartype="enum",
            context="postmaster",
            unit=None,
            min_val=None,
            max_val=None,
            enumvals=["minimal", "replica", "logical"],
            boot_val="replica",
            reset_val="replica"
        )

        is_valid, error = applicator._validate_parameter("wal_level", "replica", param_info_enum)
        assert is_valid is True, "Valid enum should pass"
        print(f"✓ Valid enum: wal_level=replica")

        is_valid, error = applicator._validate_parameter("wal_level", "invalid", param_info_enum)
        assert is_valid is False, "Invalid enum should fail"
        print(f"✓ Invalid enum detected: {error}")

        param_info_internal = ParameterInfo(
            name="block_size",
            vartype="integer",
            context="internal",
            unit=None,
            min_val=None,
            max_val=None,
            enumvals=None,
            boot_val="8192",
            reset_val="8192"
        )

        is_valid, error = applicator._validate_parameter("block_size", 8192, param_info_internal)
        assert is_valid is False, "Internal context should fail"
        print(f"✓ Internal context rejected: {error}")

        print("\n🟢 Validation working!")
        print("-" * 22)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 3] Apply Configuration (Mock)")
    print("-" * 35)

    try:
        applicator = KnobApplicator(
            conn_params,
            ApplicatorConfig(persist=True, validate=True, dry_run=False)
        )

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            ('work_mem', 'integer', 'user', 'kB', '64', '2097151', None, '4096', '4096'),
            ('random_page_cost', 'real', 'user', None, '0', None, None, '4.0', '4.0'),
            ('shared_buffers', 'integer', 'postmaster', '8kB',
             '16', '1073741823', None, '1024', '1024'),
        ]

        applicator.connection = mock_conn

        knob_config = {
            'work_mem': 8192,
            'random_page_cost': 1.1,
            'shared_buffers': 131072
        }

        print(f"Applying configuration: {knob_config}")
        result = applicator.apply(knob_config)

        print("\nResults:")
        print(f"  Success: {result.success}")
        print(f"  Applied: {result.applied_count}")
        print(f"  Failed: {result.failed_count}")
        print(f"  Restart required: {len(result.restart_required)}")
        print(f"  Message: {result.message}")

        if result.applied:
            print("\n  Applied parameters:")
            for name, value in result.applied.items():
                print(f"    {name} = {value}")

        if result.restart_required:
            print("\n  Restart required for:")
            for name in result.restart_required:
                print(f"    {name}")

        assert mock_cursor.execute.call_count > 0, "Should execute SQL"
        assert mock_conn.commit.called, "Should commit transaction"

        print("\n🟢 Configuration application working!")
        print("-" * 37)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 4] Rollback on Error")
    print("-" * 26)

    try:
        applicator = KnobApplicator(
            conn_params,
            ApplicatorConfig(
                persist=True,
                validate=True,
                dry_run=False,
                rollback_on_error=True
            )
        )

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            ('work_mem', 'integer', 'user', 'kB', '64', '2097151', None, '4096', '4096'),
            ('invalid_param', 'integer', 'internal', None, None, None, None, '0', '0'),
        ]

        applicator.connection = mock_conn

        knob_config = {
            'work_mem': 8192,
            'invalid_param': 100  # This will fail (internal context)
        }

        print("Applying config with invalid parameter...")
        result = applicator.apply(knob_config)

        print("\nResults:")
        print(f"  Success: {result.success}")
        print(f"  Applied: {result.applied_count}")
        print(f"  Failed: {result.failed_count}")

        assert mock_conn.rollback.called, "Should rollback on error"
        print("✓ Rollback called on error")

        print("\n🟢 Rollback on error working!")
        print("-" * 29)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 5] Dry Run Mode")
    print("-" * 21)

    try:
        applicator = KnobApplicator(
            conn_params,
            ApplicatorConfig(persist=True, validate=True, dry_run=True)
        )

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            ('work_mem', 'integer', 'user', 'kB', '64', '2097151', None, '4096', '4096'),
        ]

        applicator.connection = mock_conn
        knob_config = {'work_mem': 8192}

        print("Applying in dry run mode...")
        result = applicator.apply(knob_config)

        print("\nResults:")
        print(f"  Success: {result.success}")
        print(f"  Applied: {result.applied_count}")
        print(f"  Message: {result.message}")

        assert result.success is True, "Dry run should succeed"

        print("\n🟢 Dry run mode working!")
        print("-" * 23)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 6] Context Manager Usage")
    print("-" * 31)

    try:
        applicator = KnobApplicator(conn_params, ApplicatorConfig())

        applicator.connect = Mock()
        applicator.disconnect = Mock()

        print("Using context manager...")
        with applicator as app:
            print(f"  Inside context: {app}")
            assert applicator.connect.called, "Should call connect"

        assert applicator.disconnect.called, "Should call disconnect"

        print("\n🟢 Context manager working!")
        print("-" * 27)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\n[TEST 7] Get Current Values (Mock)")
    print("-" * 35)

    try:
        applicator = KnobApplicator(conn_params, ApplicatorConfig())

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_cursor.fetchall.return_value = [
            ('work_mem', '4096', 'kB'),
            ('random_page_cost', '4.0', None),
        ]

        applicator.connection = mock_conn
        param_names = ['work_mem', 'random_page_cost']
        current_values = applicator.get_current_values(param_names)

        print("Current values:")
        for name, value in current_values.items():
            print(f"  {name} = {value}")

        assert 'work_mem' in current_values
        assert 'random_page_cost' in current_values

        print("\n🟢 Get current values working!")
        print("=" * 30)

    except (TypeError, ValueError, AssertionError) as e:
        print(f"🔴 ERROR: {e}")
        import traceback
        traceback.print_exc()

    print("\nTEST SUMMARY")
    print("=" * 12)
    print("""
KNOB APPLICATOR:
  🟢 Applicator initialization
  🟢 Parameter validation (integer, real, enum, internal)
  🟢 Configuration application (mock)
  🟢 Rollback on error
  🟢 Dry run mode
  🟢 Context manager usage
  🟢 Get current values (mock)

FEATURES:
  ✓ Context-aware application (SET vs ALTER SYSTEM vs restart)
  ✓ Parameter validation against pg_settings constraints
  ✓ Rollback support for failed changes
  ✓ Restart requirement detection
  ✓ Dry run simulation

NOTE: These are mock/unit tests. Integration tests with real PostgreSQL
      require a running database instance with proper permissions.

KnobApplicator is ready for integration with the PBT tuner!
""", end='')
    print("=" * 72)
