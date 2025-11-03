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
from src.database import (
    create_database,
    reset_database,
    load_products_dataset,
    load_leads_dataset,
)
from src.config.database import get_db_config


def setup_fresh_database():
    """Create and populate the database from scratch."""
    print("=" * 60)
    print("Database Setup - Fresh Install")
    print("=" * 30)

    config = get_db_config()
    print(f"\nTarget database: {config.name}")
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
    print(f"\n🟡 WARNING: This will destroy all data in '{config.name}'!")
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
        else:
            print(f"🔴 Unknown command: {command}")
            print("\nAvailable commands:")
            print("  setup  - Create database and load data")
            print("  reset  - Reset database (DESTRUCTIVE)")
    else:
        print("Select an option:")
        print("  1. Fresh setup (create database and load data)")
        print("  2. Reset database (DESTRUCTIVE - deletes all data)")
        print("  3. Exit")
        print()

        choice = input("Enter your choice (1-3): ")

        if choice == "1":
            setup_fresh_database()
        elif choice == "2":
            reset_existing_database()
        elif choice == "3":
            print("Exiting...")
        else:
            print("🔴 Invalid choice.")


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
