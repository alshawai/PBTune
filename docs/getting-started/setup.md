# Environment Setup Guide

> Last reviewed: 2026-03-13

See also: [Documentation Index](../README.md)

## Database Configuration

This project uses environment variables to manage database credentials securely. Follow these steps to set up your environment:

### 1. Create a `.env` file

Copy the `.env.example` file to create your own `.env` file:

```bash
cp .env.example .env
```

### 2. Configure your database credentials

Edit the `.env` file with your PostgreSQL database credentials:

```env
DB_USER=postgres
DB_PASSWORD=your_secure_password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=test_dataset
```

### 3. Install dependencies

#### Python packages

Install the required Python packages:

```bash
pip install -r requirements.txt
```

**Key Dependencies**:
- **psycopg2-binary**: PostgreSQL database adapter
- **psutil**: System and process monitoring (critical for accurate performance metrics)
- **numpy**: Numerical operations for PBT sampling and scoring
- **pandas**: Data processing for knob retrieval
- **python-dotenv**: Environment variable management

**Note**: `psutil` is essential for the Performance Evaluation System to collect accurate CPU, memory, and I/O metrics. See [Performance Evaluation Documentation](../architecture/performance-evaluation.md#system-monitoring-with-psutil) for details.

#### Sysbench (required for OLTP benchmarking)

The tuner uses sysbench's native `--warmup-time` flag, which was introduced in **sysbench 1.1.0**. The prepackaged system version (typically 1.0.20) is **not sufficient** — you must build 1.1.0 from source.

```bash
# Clone and build sysbench 1.1.0 from source
git clone --depth 1 https://github.com/akopytov/sysbench.git /tmp/sysbench-build
cd /tmp/sysbench-build
./autogen.sh
./configure --with-pgsql --without-mysql --prefix=/usr
make -j$(nproc)
sudo make install

# Verify
sysbench --version  # should print: sysbench 1.1.0-...
```

> **Platform notes:**
> - **Arch/Manjaro**: Remove the packaged version first: `sudo pacman -R sysbench`
> - **Ubuntu/Debian**: No packaged 1.1.0 yet — build from source as above. You may need `sudo apt install automake libtool libpq-dev` before running `./autogen.sh`.
> - **Fedora/RHEL**: `sudo dnf remove sysbench`, then build from source. May need `sudo dnf install automake libtool postgresql-devel`.
> - **macOS (Homebrew)**: `brew install automake libtool libpq`, then build from source with `./configure --with-pgsql --without-mysql --prefix=/usr/local`.
> - **Windows**: sysbench has no native Windows build. Use **WSL2** (Windows Subsystem for Linux) and follow the Ubuntu/Debian instructions above inside your WSL2 environment. See [Microsoft's WSL2 setup guide](https://learn.microsoft.com/en-us/windows/wsl/install) if you haven't installed it yet.

### 4. Important Security Notes

- **Never commit the `.env` file** to version control. It's already included in `.gitignore`.
- Keep your database credentials secure and don't share them publicly.
- Use the `.env.example` file as a template for team members.

## Environment Variables Reference

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DB_USER` | PostgreSQL username | `postgres` | No |
| `DB_PASSWORD` | PostgreSQL password | None | **Yes** |
| `DB_HOST` | Database host address | `localhost` | No |
| `DB_PORT` | Database port | `5432` | No |
| `DB_NAME` | Database name | `test_dataset` | No |

## Usage

Once configured, all Python scripts will automatically load credentials from the `.env` file:

```python
from dotenv import load_dotenv
import os

load_dotenv()

db_user = os.getenv("DB_USER")
db_password = os.getenv("DB_PASSWORD")
```

## Troubleshooting

### "DB_PASSWORD environment variable is required" error

Make sure you have:
1. Created a `.env` file in the project root
2. Set the `DB_PASSWORD` variable in the `.env` file
3. Saved the file

### Changes to `.env` not taking effect

If you're running a Python script and changes to `.env` aren't being picked up:
1. Restart your Python interpreter/terminal
2. Make sure the `.env` file is in the project root directory
3. Check that `load_dotenv()` is called at the beginning of your script

## Next Steps

After setting up your environment, explore the system documentation:

### Core System Documentation
- **[PostgreSQL Connection and Knobs](../architecture/postgresql-connection-and-knobs.md)**: Database connection management and knob retrieval system
- **[PBT Core Components](../architecture/pbt-core.md)**: Worker, Evolution, and Population classes for population-based training
- **[Performance Evaluation](../architecture/performance-evaluation.md)**: Evaluator, metrics collection, and scoring system
- **[Configuration Management](../architecture/configuration-management.md)**: KnobSpace and KnobApplicator for safe configuration handling

### Quick Start
1. Set up environment (this guide)
2. Understand database connection  [PostgreSQL Connection](../architecture/postgresql-connection-and-knobs.md)
3. Learn PBT algorithm  [PBT Core Components](../architecture/pbt-core.md)
4. Run end-to-end tuning
