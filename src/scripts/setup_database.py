"""
Database Setup Script
=====================

Sets up the PostgreSQL database for the project, including:
- Creating the database
- Loading sample datasets
- Verifying the setup

Run this script once before using other parts of the project.
"""

import sys
import random
from src.database import (
    create_database,
    reset_database,
    load_products_dataset,
    load_leads_dataset,
    get_connection,
)
from src.config.database import get_db_config


def setup_fresh_database():
    """Create and populate the database from scratch."""
    print("=" * 60)
    print("Database Setup - Fresh Install")
    print("=" * 30)

    config = get_db_config()
    print(f"\nTarget database: {config.dbname}")
    print(f"Host: {config.host}:{config.port}")
    print(f"User: {config.user}")

    print("-" * 30)
    print("\nStep 1: Creating database...")
    print("-" * 30)
    create_database(config)

    print("-" * 42)
    print("\nStep 2: Loading products dataset...")
    print("-" * 35)
    try:
        load_products_dataset(config)
    except (ConnectionError, FileNotFoundError, ValueError) as e:
        print(f"🟡 Warning: {e}")

    print("-" * 63)
    print("\nStep 3: Loading leads dataset...")
    print("-" * 32)
    try:
        load_leads_dataset(config)
    except (ConnectionError, FileNotFoundError, ValueError) as e:
        print(f"🟡 Warning: {e}")
    print("=" * 63)
    print("🟢 Database setup complete!")
    print("=" * 27)


def reset_existing_database():
    """Reset the database (WARNING: Destroys all data)."""
    print("=" * 60)
    print("Database Reset - DESTRUCTIVE OPERATION")
    print("=" * 38)

    config = get_db_config()
    print(f"\n🟡 WARNING: This will destroy all data in '{config.dbname}'!")
    response = input("Are you sure you want to continue? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("🔴 Operation cancelled.")
        return

    print("-" * 57)
    print("\nResetting database...")
    print("-" * 22)
    reset_database(config)

    print("-" * 48)
    print("\nLoading products dataset...")
    print("-" * 28)
    try:
        load_products_dataset(config)
    except (ConnectionError, FileNotFoundError, ValueError) as e:
        print(f"🟡 Warning: {e}")

    print("-" * 46)
    print("\nLoading leads dataset...")
    print("-" * 25)
    try:
        load_leads_dataset(config)
    except (ConnectionError, FileNotFoundError, ValueError) as e:
        print(f"🟡 Warning: {e}")

    print("=" * 60)
    print("\n🟢 Database reset complete!")
    print("=" * 27)


def main():
    """Main entry point for database setup."""
    print()
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 18 + "Database Setup Utility" + " " * 18 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "reset":
            reset_existing_database()
        elif command == "setup":
            setup_fresh_database()
        elif command == "sysbench" or command == "--sysbench":
            setup_sysbench_table()
        else:
            print(f"🔴 Unknown command: {command}")
            print("\nAvailable commands:")
            print("  setup     - Create database and load data")
            print("  reset     - Reset database (DESTRUCTIVE)")
            print("  sysbench  - Create sbtest1 table for OLTP workloads")
    else:
        print("Select an option:")
        print("  1. Fresh setup (create database and load data)")
        print("  2. Reset database (DESTRUCTIVE - deletes all data)")
        print("  3. Setup SYSBENCH table (sbtest1)")
        print("  4. Exit")
        print()

        choice = input("Enter your choice (1-4): ")

        if choice == "1":
            setup_fresh_database()
        elif choice == "2":
            reset_existing_database()
        elif choice == "3":
            setup_sysbench_table()
        elif choice == "4":
            print("Exiting...")
        else:
            print("🔴 Invalid choice.")


def setup_sysbench_table():
    """Create the sbtest1 table required for OLTP workloads."""
    print("=" * 60)
    print("Setting up SYSBENCH table (sbtest1)")
    print("=" * 60)

    config = get_db_config()
    print(f"\nTarget database: {config.dbname}")

    try:
        conn = get_connection(config)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT EXISTS (SELECT FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'sbtest1')"
        )
        exists = cursor.fetchone()[0]  # type: ignore

        if exists:
            print("\n🟡 Table 'sbtest1' already exists.")
            response = input("Drop and recreate? (yes/no): ")
            if response.lower() not in ["yes", "y"]:
                print("🔴 Operation cancelled.")
                cursor.close()
                conn.close()
                return

            cursor.execute("DROP TABLE sbtest1")
            print("🗑️  Dropped existing table")

        # Create sbtest1 table (SYSBENCH-compatible schema)
        print("\n📋 Creating sbtest1 table...")
        cursor.execute("""
            CREATE TABLE sbtest1 (
                id SERIAL PRIMARY KEY,
                k INTEGER NOT NULL DEFAULT 0,
                c CHAR(120) NOT NULL DEFAULT '',
                pad CHAR(60) NOT NULL DEFAULT ''
            )
        """)

        print("📝 Inserting 10,000 sample rows...")
        batch_size = 1000
        for batch_start in range(0, 10000, batch_size):
            values = []
            for _ in range(batch_start, min(batch_start + batch_size, 10000)):
                k = random.randint(1, 100000)
                c = f"{'x' * random.randint(50, 120):120}"
                pad = f"{'y' * random.randint(30, 60):60}"
                values.append(f"({k}, '{c}', '{pad}')")

            cursor.execute(f"INSERT INTO sbtest1 (k, c, pad) VALUES {','.join(values)}")
            print(
                f"  Inserted {min(batch_start + batch_size, 10000)}/10000 rows",
                end="\r",
            )

        print("\n")

        print("🔍 Creating indexes...")
        cursor.execute("CREATE INDEX k_1 ON sbtest1(k)")

        print("📊 Analyzing table...")
        cursor.execute("ANALYZE sbtest1")

        conn.commit()
        cursor.close()
        conn.close()

        print("\n✅ SYSBENCH table setup complete!")
        print("   Table: sbtest1")
        print("   Rows:  10,000")
        print("   Index: k_1 (k)")

    except Exception as e:
        print(f"🔴 Error setting up SYSBENCH table: {e}")
        raise


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🔴 Operation cancelled by user.")
        sys.exit(1)
    except (ConnectionError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"\n🔴 Error: {e}")
        print("\nPlease ensure:")
        print("  1. PostgreSQL is running")
        print("  2. Database credentials are correct in .env")
        print("  3. You have necessary permissions")
        print("\nSee docs/ENVIRONMENT_SETUP.md for help.")
        sys.exit(1)
