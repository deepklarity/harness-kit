"""Django settings for harness-time project."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-dev-key-change-in-production",
)

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = ["*"]

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("true", "1", "yes", "on")


_legacy_firebase_enabled = _env_bool("FIREBASE_AUTH_ENABLED", False)
AUTH_ENABLED = _env_bool("AUTH_ENABLED", _legacy_firebase_enabled)
AUTH_LEGACY_FIREBASE_FLAG_COMPAT = _env_bool("AUTH_LEGACY_FIREBASE_FLAG_COMPAT", True)

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "tasks.apps.TasksConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "tasks.middleware.TaskitAuthMiddleware",
]

if AUTH_ENABLED:
    CORS_ALLOW_ALL_ORIGINS = False
    CORS_ALLOW_CREDENTIALS = True
    CORS_ALLOWED_ORIGINS = [
        item.strip()
        for item in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
        if item.strip()
    ]
else:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOW_CREDENTIALS = False

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"

USE_SQLITE = os.environ.get("USE_SQLITE", "True").lower() in ("true", "1", "yes")

if USE_SQLITE:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "taskit"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Media files (uploaded screenshots, etc.)
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

USE_TZ = True
TIME_ZONE = "UTC"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": (
        ["tasks.authentication.TaskitJWTAuthentication"] if AUTH_ENABLED else []
    ),
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# Backward compatibility alias during migration window.
FIREBASE_AUTH_ENABLED = AUTH_ENABLED if AUTH_LEGACY_FIREBASE_FLAG_COMPAT else False

JWT_ACCESS_SECONDS = int(os.environ.get("JWT_ACCESS_SECONDS", "900"))
JWT_REFRESH_SECONDS = int(os.environ.get("JWT_REFRESH_SECONDS", "604800"))

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(seconds=JWT_ACCESS_SECONDS),
    "REFRESH_TOKEN_LIFETIME": timedelta(seconds=JWT_REFRESH_SECONDS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
}

AUTH_COOKIE_NAME = os.environ.get("AUTH_COOKIE_NAME", "taskit_refresh")
AUTH_COOKIE_SECURE = _env_bool("AUTH_COOKIE_SECURE", False)
AUTH_COOKIE_SAMESITE = os.environ.get("AUTH_COOKIE_SAMESITE", "Lax")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN") or None
AUTH_COOKIE_PATH = os.environ.get("AUTH_COOKIE_PATH", "/auth/")

# Odin execution strategy
# Set to "local" to trigger `odin exec` when a task moves to IN_PROGRESS
# Set to "celery_dag" for DAG-aware execution via Celery Beat polling
ODIN_EXECUTION_STRATEGY = os.environ.get("ODIN_EXECUTION_STRATEGY", "celery_dag")
ODIN_CLI_PATH = os.environ.get("ODIN_CLI_PATH", "odin")
ODIN_WORKING_DIR = os.environ.get("ODIN_WORKING_DIR", None)

# Celery configuration (required for celery_dag execution strategy)
USE_FILESYSTEM_BROKER = os.environ.get("USE_FILESYSTEM_BROKER", "True").lower() in ("true", "1", "yes")

if USE_FILESYSTEM_BROKER:
    _celery_data_dir = BASE_DIR / ".celery" / "out"
    _celery_processed_dir = BASE_DIR / ".celery" / "processed"
    _celery_data_dir.mkdir(parents=True, exist_ok=True)
    _celery_processed_dir.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / ".celery" / "results").mkdir(parents=True, exist_ok=True)
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "filesystem://")
    CELERY_BROKER_TRANSPORT_OPTIONS = {
        "data_folder_in": str(_celery_data_dir),
        "data_folder_out": str(_celery_data_dir),
        "data_folder_processed": str(_celery_processed_dir),
    }
    CELERY_RESULT_BACKEND = os.environ.get(
        "CELERY_RESULT_BACKEND",
        f"file://{BASE_DIR / '.celery' / 'results'}",
    )
else:
    CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
    CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

CELERY_BEAT_SCHEDULE = {}
if ODIN_EXECUTION_STRATEGY == "celery_dag":
    CELERY_BEAT_SCHEDULE["dag-executor-poll"] = {
        "task": "tasks.dag_executor.poll_and_execute",
        "schedule": int(os.environ.get("DAG_EXECUTOR_POLL_INTERVAL", "5")),
    }
DAG_EXECUTOR_MAX_CONCURRENCY = int(os.environ.get("DAG_EXECUTOR_MAX_CONCURRENCY", "3"))

# Logging — route Django request logs through the taskit detail logger
_LOG_DIR = BASE_DIR / "logs"
_LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "detail": {
            "format": "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "detail_file": {
            "level": "DEBUG",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(_LOG_DIR / "taskit_detail.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "detail",
        },
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "detail",
        },
    },
    "loggers": {
        "django.request": {
            "handlers": ["detail_file", "console"],
            "level": "DEBUG",
            "propagate": True,
        },
        "django.server": {
            "handlers": ["detail_file", "console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
