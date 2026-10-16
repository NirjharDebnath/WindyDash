"""
WindyDash - Configuration & Environment
Centralized configuration with safe fallback defaults.
Includes built-in zero-dependency .env loader so it runs cleanly anywhere.
"""
import os
from pathlib import Path

# Base directory anchored to the project root
BASE_DIR = Path(__file__).resolve().parent

# Attempt to load .env using python-dotenv or fallback to built-in parser
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass

# Paths
DATABASE_FILE = BASE_DIR / "windydash.db"
TARGETS_FILE = BASE_DIR / "targets.json"

# Server configuration with safe defaults
POLL_SECONDS = float(os.getenv("SYSMON_INTERVAL", "1.0"))
HOST = os.getenv("SYSMON_HOST", "127.0.0.1")
PORT = int(os.getenv("SYSMON_PORT", "4040"))
AUTH_TOKEN = os.getenv("DASH_PASSWORD", "")

# Rate limiting settings (Anti-DDoS)
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "25"))       # Max requests per window
RATE_WINDOW = float(os.getenv("RATE_WINDOW", "5.0"))   # Sliding window in seconds
