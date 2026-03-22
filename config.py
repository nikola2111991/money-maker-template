"""
config.py - Central configuration for Money Maker.
All paths and env vars in one place.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _env_path(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default)).expanduser()

# --- Directories ---
LEADS_DIR = _env_path("MM_LEADS_DIR", "~/Documents/money-maker/leads/")
DEPLOY_REPO = _env_path("MM_DEPLOY_REPO", "~/Documents/mm-demos/")
PLAYBOOK_DIR = PROJECT_DIR / "playbooks"
TEMPLATES_DIR = PROJECT_DIR

# --- URLs ---
DEPLOY_BASE_URL = _env("MM_DEPLOY_URL", "")
CAL_COM_URL = _env("MM_CAL_URL", "")
SENDER_EMAIL = _env("MM_SENDER_EMAIL", "")

# --- API Keys (read from env, never hardcoded) ---
GOOGLE_API_KEY = _env("GOOGLE_API_KEY", "") or _env("GOOGLE_PLACES_API_KEY", "")
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")
SERPAPI_KEY = _env("SERPAPI_KEY", "")
GOOGLE_SEARCH_API_KEY = _env("GOOGLE_SEARCH_API_KEY", "")
GOOGLE_SEARCH_CX = _env("GOOGLE_SEARCH_CX", "")

# --- Lead Categories ---
CATEGORIES: list[str] = ["HOT", "WARM", "COOL"]
