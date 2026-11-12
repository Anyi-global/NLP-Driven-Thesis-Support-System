import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

INSECURE_SECRETS = {"dev-only-change-this-secret", "replace-with-a-long-random-secret", ""}


def _flag(name, default="0"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-this-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'thesis_support.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "data" / "uploads"))
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH_MB", "20")) * 1024 * 1024
    SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
    DEBUG = _flag("FLASK_DEBUG")
    JSON_SORT_KEYS = False

    # --- LLM (set GEMINI_MODEL / EMBEDDING_MODEL to models that are currently available) ---
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "")
    LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "300000"))

    # --- optional language engines (see app/nlp/linguistics.py) ---
    LANGUAGETOOL_ENABLED = _flag("LANGUAGETOOL_ENABLED")          # needs Java; downloads LanguageTool on first use
    LANGUAGETOOL_URL = os.getenv("LANGUAGETOOL_URL", "")          # or point at a self-hosted LanguageTool server
    LANGUAGETOOL_MAX_CHARS = int(os.getenv("LANGUAGETOOL_MAX_CHARS", "100000"))

    # --- background analysis ---
    ANALYSIS_ASYNC = _flag("ANALYSIS_ASYNC", "1")
    ANALYSIS_WORKERS = int(os.getenv("ANALYSIS_WORKERS", "2"))
    STALE_JOB_MINUTES = int(os.getenv("STALE_JOB_MINUTES", "20"))

    # --- web security ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")  # use "None" (+ SECURE) for a cross-site frontend
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE")                 # set to 1 behind HTTPS
    CSRF_ENABLED = _flag("CSRF_ENABLED", "1")
    CORS_ORIGINS = [o for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
    RATE_LIMIT_ENABLED = _flag("RATE_LIMIT_ENABLED", "1")
    TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))       # >0 when behind nginx etc.
