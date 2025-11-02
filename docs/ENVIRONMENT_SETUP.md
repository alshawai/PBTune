# Environment Setup Guide

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

Install the required Python packages:

```bash
pip install -r requirements.txt
```

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
