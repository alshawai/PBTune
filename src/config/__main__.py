"""Run the database configuration test."""

if __name__ == "__main__":
    from src.config.database import get_db_config

    print("=" * 28)
    print("Database Configuration Test")
    print("=" * 28)

    try:
        CONFIG = get_db_config()

        print("\n🟢 Configuration loaded successfully!")
        print(f"\n{CONFIG}")

        print("\nConnection Details:")
        print(f"  User: {CONFIG.user}")
        print(f"  Host: {CONFIG.host}")
        print(f"  Port: {CONFIG.port}")
        print(f"  Database: {CONFIG.name}")

        print("\nConnection String (safe):")
        print(f"  {CONFIG.get_connection_string()}")

    except ValueError as e:
        print(f"\n🔴 Configuration Error: {e}")
        print("\nPlease ensure:")
        print("  1. You have created a .env file in the project root")
        print("  2. DB_PASSWORD is set in the .env file")
        print("  3. The .env file is in the project root directory")
        print("\nSee docs/ENVIRONMENT_SETUP.md for detailed instructions.")

    print("=" * 60)
