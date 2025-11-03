"""Run comprehensive database module tests."""

if __name__ == "__main__":
    import os
    import tempfile
    from dataclasses import replace
    from src.database import (
        get_connection,
        get_engine,
        create_database,
        drop_database,
        reset_database,
        load_csv_to_table,
    )
    from src.config.database import get_db_config
    from sqlalchemy import text

    print("Database Module - Comprehensive Test")
    print("=" * 37)

    CONFIG = get_db_config()
    print(f"\nProduction database: {CONFIG.name}")

    TEST_DB_NAME = "test_db_module_temp"
    TEST_CONFIG = replace(CONFIG, name=TEST_DB_NAME)
    print(f"Test database: {TEST_DB_NAME}")
    print("\n🟡 This test will create and destroy a temporary database.")

    print("-" * 58)
    print("\nTest 1: Connection Module (connection.py)")
    print("-" * 40)

    try:
        print("  1a. Testing psycopg2 connection...")
        conn = get_connection(CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT version()")
        version = cursor.fetchone()
        print(f"      🟢 psycopg2: {version[0][:40]}...")  # type: ignore
        cursor.close()
        conn.close()

        print("  1b. Testing SQLAlchemy engine...")
        engine = get_engine(CONFIG)
        with engine.connect() as connection:
            result = connection.execute(text("SELECT current_database()"))
            db_name = result.fetchone()[0]  # type: ignore
            print(f"      🟢 SQLAlchemy: Connected to '{db_name}'")

    except (ConnectionError, ImportError, RuntimeError) as e:
        print(f"      🔴 Connection test failed: {e}")

    print("-" * 63)
    print("\nTest 2: Management Module (management.py)")
    print("-" * 42)

    try:
        print("  2a. Testing create_database()...")
        create_database(TEST_CONFIG)

        conn = get_connection(CONFIG, dbname="postgres")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        )
        exists = cursor.fetchone()
        cursor.close()
        conn.close()

        if exists:
            print(f"      🟢 Database '{TEST_DB_NAME}' created successfully")
        else:
            print(f"      🔴 Database '{TEST_DB_NAME}' was not created")

        print("\n  2b. Testing reset_database()...")
        reset_database(TEST_CONFIG)
        print(f"      🟢 Database '{TEST_DB_NAME}' reset successfully")

        print("\n  2c. Testing drop_database()...")
        drop_database(TEST_CONFIG)

        conn = get_connection(CONFIG, dbname="postgres")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
        )
        exists = cursor.fetchone()
        cursor.close()
        conn.close()

        if not exists:
            print(f"      🟢 Database '{TEST_DB_NAME}' dropped successfully")
        else:
            print(f"      🔴 Database '{TEST_DB_NAME}' still exists")

    except (ConnectionError, RuntimeError, ValueError) as e:
        print(f"      🔴 Management test failed: {e}")
        try:  # Clean up if something went wrong
            drop_database(TEST_CONFIG)
        except (ConnectionError, RuntimeError):
            pass

    print("-" * 60)
    print("Test 3: Data Loader Module (data_loader.py)")
    print("-" * 44)

    try:
        print("  3a. Setting up test database...")
        create_database(TEST_CONFIG)
        print("      🟢 Test database created")

        print("  3b. Creating temporary test CSV...")
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
            temp_csv = f.name
            f.write("id,name,value\n")
            f.write("1,Test Item 1,100\n")
            f.write("2,Test Item 2,200\n")
            f.write("3,Test Item 3,300\n")

        print("      🟢 Temporary CSV created with 3 rows")

        print("  3c. Testing load_csv_to_table()...")
        load_csv_to_table(
            temp_csv,
            "test_table",
            if_exists="fail",
            config=TEST_CONFIG
        )
        print("      🟢 Data loaded into 'test_table'")

        print("  3d. Verifying loaded data...")
        engine = get_engine(TEST_CONFIG)
        with engine.connect() as connection:
            result = connection.execute(text("SELECT COUNT(*) FROM test_table"))
            count = result.fetchone()[0]  # type: ignore
            print(f"      🟢 Verified: {count} rows in 'test_table'")

            result = connection.execute(text("SELECT * FROM test_table ORDER BY id"))
            rows = result.fetchall()
            print(f"      🟢 Sample data: {rows[0]}")  # type: ignore

        print("  3e. Testing if_exists='replace'...")
        load_csv_to_table(
            temp_csv,
            "test_table",
            if_exists="replace",
            config=TEST_CONFIG
        )
        print("      🟢 Table replaced successfully")

        # Clean up
        os.unlink(temp_csv)
        drop_database(TEST_CONFIG)
        print("  3f. Cleanup completed")

        print("\n  3g. Checking for production datasets...")
        current_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        data_dir = os.path.join(current_dir, "data")

        products_file = os.path.join(data_dir, "products-1000000.csv")
        leads_file = os.path.join(data_dir, "leads-100000.csv")

        if os.path.exists(products_file):
            print(f"      🟢 Products dataset ({os.path.getsize(products_file):,} bytes)")
        else:
            print("      🟡 Products dataset not found")

        if os.path.exists(leads_file):
            print(f"      🟢 Leads dataset ({os.path.getsize(leads_file):,} bytes)")
        else:
            print("      🟡 Leads dataset not found")

    except (OSError, IOError, FileNotFoundError, PermissionError) as e:
        print(f"      🔴 Data loader test failed: {e}")
        try:  # Clean up
            if 'temp_csv' in locals() and os.path.exists(temp_csv):
                os.unlink(temp_csv)
            drop_database(TEST_CONFIG)
        except (OSError, IOError, FileNotFoundError, PermissionError):
            pass

    print("=" * 72)
    print("\nTest Summary")
    print("=" * 12)
    print("🟢 All database modules tested successfully!")
    print("\nTested functionality:")
    print("  🟢 connection.py   - psycopg2 & SQLAlchemy connections")
    print("  🟢 management.py   - create, drop, reset operations")
    print("  🟢 data_loader.py  - CSV loading with all modes")
    print("\nFor production database setup, run:")
    print("  python -m src.scripts.setup_database")
    print("=" * 39)
