"""Django settings for harness-time project."""
import os
import sys
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Make the in-repo `harness_usage_status` package importable when the backend
# runs from the repo checkout. It is a first-class sibling project using a
# src-layout; its import root is one level under the project dir. Guarded so a
# missing checkout degrades cleanly (the consumers catch ImportError).
_HUS_SRC = BASE_DIR.parent.parent / "harness_usage_status" / "src"
if _HUS_SRC.is_dir() and str(_HUS_SRC) not in sys.path:
    sys.path.insert(0, str(_HUS_SRC))

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
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "channels",
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

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = [
    item.strip()
    for item in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:9200").split(",")
    if item.strip()
]

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

# SQLite lock resilience (task #253): WAL journal mode (set on every
# connection via the connection_created signal in tasks.sqlite_pragmas)
# lets readers and a writer coexist on the single file DB. busy_timeout
# is how long a blocked writer waits before failing — the many-readers-
# few-writers tuning. A dedicated retry_on_locked backstop covers the
# rare case where even this is exceeded under heavy concurrent writes.
SQLITE_BUSY_TIMEOUT_MS = int(
    os.environ.get("SQLITE_BUSY_TIMEOUT_MS", "5000") or "5000"
)

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

# Forced provider mode
FORCED_BASE_PROVIDER = (os.environ.get("FORCED_BASE_PROVIDER") or "").strip().lower() or None
FORCED_BASE_MODEL = (os.environ.get("FORCED_BASE_MODEL") or "").strip() or None

# Odin execution strategy
# Set to "local" to trigger `odin exec` when a task moves to IN_PROGRESS
# Set to "celery_dag" for DAG-aware execution via Celery Beat polling
ODIN_EXECUTION_STRATEGY = os.environ.get("ODIN_EXECUTION_STRATEGY", "celery_dag")
ODIN_CLI_PATH = os.environ.get("ODIN_CLI_PATH", "odin")
ODIN_WORKING_DIR = os.environ.get("ODIN_WORKING_DIR", None)

# Planning integration
TASKIT_INTERNAL_URL = os.environ.get("TASKIT_INTERNAL_URL", "http://localhost:8000")

# Channel layers — InMemory for dev (SQLite), Redis for production
if USE_SQLITE:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [os.environ.get("REDIS_URL", "redis://localhost:6379/1")]},
        }
    }

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
CELERY_BEAT_SCHEDULE["schedule-release-poll"] = {
    "task": "tasks.schedule_executor.release_due_schedules",
    "schedule": int(os.environ.get("SCHEDULE_RELEASE_POLL_INTERVAL", "30")),
}
# W5.14 (task #211) — single periodic reconciler over TaskRun leases.
# Registered unconditionally (unlike dag-executor-poll above): TaskRun rows
# are created by every execution strategy (local + celery_dag), so a run
# started under ODIN_EXECUTION_STRATEGY=local must still be supervised even
# though poll_and_execute itself only runs under celery_dag.
CELERY_BEAT_SCHEDULE["task-run-reconciler"] = {
    "task": "tasks.dag_executor.reconcile_task_runs",
    "schedule": int(os.environ.get("TASK_RUN_RECONCILE_INTERVAL_SECONDS", "20")),
}
# W3.22 (task #171) — merge watchdog: scan every 60s for stalled
# post-reflection dispatches and retry/escalate. Tunables come from env so
# operators can adjust cadence without code changes.
# W4 (task #196): the idle window default must EXCEED the observed ~20 min
# merge latency (merges starve behind long executions on a shared worker
# pool). A window shorter than real latency guarantees false alarms.
CELERY_BEAT_SCHEDULE["merge-watchdog-scan"] = {
    "task": "tasks.dag_executor.scan_pending_merges",
    "schedule": int(os.environ.get("MERGE_WATCHDOG_SCAN_INTERVAL_SECONDS", "60")),
}
MERGE_WATCHDOG_MINUTES_IDLE = int(
    os.environ.get("MERGE_WATCHDOG_MINUTES_IDLE", "30")
)
MERGE_WATCHDOG_MAX_ATTEMPTS = int(
    os.environ.get("MERGE_WATCHDOG_MAX_ATTEMPTS", "1")
)
# Optional dedicated celery queue for merge_task_on_reflection so long
# executions cannot starve merges (task #196). Empty (default) = default
# queue, unchanged behavior. When set, also start a worker on that queue:
#   celery -A config worker -Q merges --pool=threads --concurrency=2
MERGE_QUEUE_NAME = os.environ.get("MERGE_QUEUE_NAME", "").strip() or None
# Reflection watchdog (task #221): scan every 60s for REVIEW tasks whose
# reflection was lost (host slept, worker died, broker dropped the message)
# and retry/escalate. Mirrors the merge watchdog exactly.
CELERY_BEAT_SCHEDULE["reflection-watchdog-scan"] = {
    "task": "tasks.dag_executor.scan_pending_reflections",
    "schedule": int(os.environ.get("REFLECTION_WATCHDOG_SCAN_INTERVAL_SECONDS", "60")),
}
# W11.11 (task 335): one-time reminder for tasks stuck in FAILED for 30+ minutes.
CELERY_BEAT_SCHEDULE["failed-task-reminder-scan"] = {
    "task": "tasks.failed_reminder.scan_for_failed_reminders",
    "schedule": int(os.environ.get("FAILED_REMINDER_SCAN_INTERVAL_SECONDS", "60")),
}
REFLECTION_WATCHDOG_MINUTES_IDLE = int(
    os.environ.get("REFLECTION_WATCHDOG_MINUTES_IDLE", "35")
)
REFLECTION_WATCHDOG_MAX_ATTEMPTS = int(
    os.environ.get("REFLECTION_WATCHDOG_MAX_ATTEMPTS", "2")
)
# Consecutive watchdog scans with no resolvable reviewer before the
# watchdog posts a QUESTION comment + ErrorEvent. Without this guard a
# stuck review whose board reflection_model names a model no active
# agent advertises would skip silently forever (task #246).
REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION = int(
    os.environ.get("REFLECTION_WATCHDOG_NO_REVIEWER_SKIPS_BEFORE_ESCALATION", "3")
)
# Optional dedicated celery queue for execute_reflection so reviews never
# wait behind the execution pool — a reflection is part of a flow, not a
# competing task (task #221). Empty (default) = default queue.
#   celery -A config worker -Q reflections --pool=threads --concurrency=2
REFLECTION_QUEUE_NAME = os.environ.get("REFLECTION_QUEUE_NAME", "").strip() or None


def _build_celery_task_routes(merge_queue=None, reflection_queue=None):
    """Compose ``CELERY_TASK_ROUTES`` from the merge + reflection queue names.

    Extracted so tests can exercise the routing logic with arbitrary values
    without restarting the worker — the module-level constant below still
    uses the env values resolved at Django startup.

    Both queues are independent; an operator may enable either, both, or
    neither. Task #196 added ``merge_task_on_reflection`` routing; task
    #232 added ``resume_merge_with_guidance`` because the human-reply
    dispatch in ``tasks.signals`` uses ``.delay()`` and would otherwise
    land on the default pool and wait behind executions (8-min pickup lag
    observed live). The route lookup by task name covers both dispatch
    styles (``delay`` and explicit ``apply_async(queue=...)`` callers).

    Empty / whitespace-only inputs are treated as unset so a stray
    ``MERGE_QUEUE_NAME=`` (common in misconfigured env files) doesn't
    silently break routing to a queue named ``""``.
    """
    def _norm(value):
        if value is None:
            return None
        s = str(value).strip()
        return s or None

    merge_queue = _norm(merge_queue)
    reflection_queue = _norm(reflection_queue)

    routes = {}
    if merge_queue:
        routes["tasks.dag_executor.merge_task_on_reflection"] = {"queue": merge_queue}
        routes["tasks.dag_executor.resume_merge_with_guidance"] = {"queue": merge_queue}
    if reflection_queue:
        routes["tasks.dag_executor.execute_reflection"] = {"queue": reflection_queue}
    return routes


CELERY_TASK_ROUTES = _build_celery_task_routes(
    merge_queue=MERGE_QUEUE_NAME,
    reflection_queue=REFLECTION_QUEUE_NAME,
)
# Concurrency default sized for a ~16-19 GB host: each confined run is a ~4 GB
# microVM and the host also carries backend + celery + UI. Raise via env only on
# a bigger host (the old default of 10 allowed 40 GB of VMs).
DAG_EXECUTOR_MAX_CONCURRENCY = int(os.environ.get("DAG_EXECUTOR_MAX_CONCURRENCY", "3"))
# Global memory budget for microVM spawns (execution + reflection share it).
# 0 (default) = derive from host RAM (total − SANDBOX_OS_RESERVE_MIB); set
# explicitly on hosts where auto-detection is wrong or RAM can't be read.
SANDBOX_MEMORY_BUDGET_MIB = int(os.environ.get("SANDBOX_MEMORY_BUDGET_MIB", "0"))
# MiB reserved per spawn — mirrors odin's microsandbox_mem_size_mib (4 GB).
SANDBOX_DEFAULT_VM_MEM_MIB = int(os.environ.get("SANDBOX_DEFAULT_VM_MEM_MIB", "4096"))
# RAM to leave for OS + backend + celery + UI when deriving the budget.
SANDBOX_OS_RESERVE_MIB = int(os.environ.get("SANDBOX_OS_RESERVE_MIB", "6144"))
# How long a budget-blocked reflection waits before re-trying.
SANDBOX_REFLECTION_RETRY_SECONDS = int(os.environ.get("SANDBOX_REFLECTION_RETRY_SECONDS", "10"))
# Must exceed the microVM's own timeout (odin microsandbox_timeout_secs, 2700)
# plus shutdown grace, so the guest terminates gracefully before the executor
# hard-kills the run and discards uncommitted work.
DAG_EXECUTOR_TASK_TIMEOUT_SECONDS = int(os.environ.get("DAG_EXECUTOR_TASK_TIMEOUT_SECONDS", "3000"))
DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS = int(os.environ.get("DAG_EXECUTOR_REFLECTION_TIMEOUT_SECONDS", "1800"))
# No-pid (still queued) recovery deadline: must exceed worst-case celery queue
# wait at full concurrency (tasks run up to 30 min; small worker pool) — 120s
# killed healthy queued dispatches (F53).
DAG_EXECUTOR_QUEUED_STALE_SECONDS = int(os.environ.get("DAG_EXECUTOR_QUEUED_STALE_SECONDS", "3000"))
ODIN_SPEC_PLAN_TIMEOUT_SECONDS = int(os.environ.get("ODIN_SPEC_PLAN_TIMEOUT_SECONDS", "1800"))
# W5.14 (task #211) — TaskRun lease window: a RUNNING run whose heartbeat is
# older than this is presumed supervisor-crashed (the process that should be
# heartbeating it — the subprocess-monitoring loop — is gone). Default ~3 min
# comfortably exceeds the heartbeat write interval (10s) with margin for a
# slow DB write, while staying far below DAG_EXECUTOR_TASK_TIMEOUT_SECONDS so
# a crashed worker is detected long before a legitimate long-running task's
# own deadline would fire.
TASK_RUN_LEASE_SECONDS = int(os.environ.get("TASK_RUN_LEASE_SECONDS", "180") or 180)
# W6 (task #235) — progress-liveness window: a RUNNING run whose harness
# trace file has been idle longer than this is a zombie — the sandbox process
# is alive (and heartbeating) but the agent inside has stopped producing
# output (host sleep / hung agent). Distinct from TASK_RUN_LEASE_SECONDS:
# the lease catches a *dead supervisor* (stale heartbeat); this catches a
# *live supervisor supervising a dead agent* (fresh heartbeat, idle trace).
# Only fires when a trace file exists — a run with no trace falls back to the
# lease/pid path. Default ~10 min so an agent thinking between writes is never
# mistaken for a zombie.
TASK_RUN_PROGRESS_WINDOW_SECONDS = int(
    os.environ.get("TASK_RUN_PROGRESS_WINDOW_SECONDS", "600") or 600
)
# W7 (task #262) — error-loop detection. A RUNNING run whose trace tail
# is dominated by repeated errors of the same W6.3 signature is looping:
# the CLI retries a failing provider call forever, the trace keeps
# growing (so the progress check above sees fresh *writes* and passes),
# but no actual *work* happens. These tunables control the detector:
#   THRESHOLD  — min share of same-signature error lines (default 0.8).
#   WINDOW     — how many tail lines to examine (default 50).
#   MIN_LINES  — need this many non-blank lines before judging (default 10).
#   PROVIDER_BACKOFF — seconds to wait before requeuing a provider
#                stream/quota loop (default 600 = 10 min; "wait out the
#                window"). Non-provider loops retry without this delay.
TASK_RUN_ERROR_LOOP_THRESHOLD = float(
    os.environ.get("TASK_RUN_ERROR_LOOP_THRESHOLD", "0.8") or 0.8
)
TASK_RUN_ERROR_LOOP_WINDOW = int(
    os.environ.get("TASK_RUN_ERROR_LOOP_WINDOW", "50") or 50
)
TASK_RUN_ERROR_LOOP_MIN_LINES = int(
    os.environ.get("TASK_RUN_ERROR_LOOP_MIN_LINES", "10") or 10
)
TASK_RUN_ERROR_LOOP_PROVIDER_BACKOFF = int(
    os.environ.get("TASK_RUN_ERROR_LOOP_PROVIDER_BACKOFF", "600") or 600
)
# Routing awareness (task #262 scope 3): when True, a provider-loop
# detected repeatedly for the same fingerprint reassigns to the
# next-cheapest viable agent instead of requeuing the same one. OFF by
# default — reassignment is a stronger action than backoff-requeue and
# should be opted into once the league data is trusted on a given host.
TASK_RUN_ERROR_LOOP_REASSIGN = (
    os.environ.get("TASK_RUN_ERROR_LOOP_REASSIGN", "").lower() in ("1", "true", "yes")
)

# W4.4 failure-policy overrides.  The defaults live in
# ``tasks.failure_policy.DEFAULT_POLICY_TABLE`` (sane bounds per class);
# operators tune per-host retry counts / backoffs here without code
# changes.  Format: {failure_class: {"max_retries": int, "backoff_seconds": float}}.
# Unknown keys are ignored; unrecognised classes fall through to the
# default policy (human for unknown).
DAG_EXECUTOR_FAILURE_POLICY_OVERRIDES = {}

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
