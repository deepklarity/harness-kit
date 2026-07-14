"""Data-operations registry (task #247).

Schema migrations have Django; *data* operations shipped by agents had
hope — they ran in a sandbox that can't touch the prod DB, and nothing
re-ran them after merge. This module closes that gap.

Each idempotent data operation registers itself via the
:func:`register_dataop` decorator. A ``post_migrate`` signal hook (wired
in ``tasks.apps.TasksConfig.ready``) calls :func:`run_pending_dataops`,
which runs every op that has no :class:`~tasks.models.DataOpMarker` row.
Because ``migrate`` runs at service start, a restart is all a shipped
data op needs — the same lifecycle as migrations.

Contract — idempotency is the guard, the marker is bookkeeping:
    Every registered op MUST be safe to re-run. A wiped marker (or a
    fresh DB) redoes work but never corrupts data. The marker only skips
    redundant work, so a restart after a successful op is a no-op.

Adding a new data op:
    @register_dataop("my_op_name")
    def my_op():
        ...  # idempotent; safe to call N times

Future loaders go through the registry, never a manual runbook.
"""
import logging

from .models import DataOpMarker

logger = logging.getLogger("taskit.dataops")

# name -> callable, populated at import time by the decorator.
_REGISTRY: dict = {}


def register_dataop(name: str):
    """Register ``fn`` as an idempotent data operation under ``name``.

    The name is the done-marker key — it must be unique across the
    registry and stable across releases (renaming an op makes it
    re-run once, which is safe because the op is idempotent).
    """
    def _decorator(fn):
        if name in _REGISTRY:
            raise ValueError(
                f"dataop name {name!r} is already registered — "
                f"each op needs a unique name"
            )
        _REGISTRY[name] = fn
        return fn
    return _decorator


def registered_names():
    """Return the sorted set of registered data-op names."""
    return sorted(_REGISTRY)


def run_pending_dataops(sender=None, **kwargs):
    """Run every registered data op that hasn't been marked done.

    Accepts ``**kwargs`` so it doubles as a ``post_migrate`` signal
    receiver (Django passes ``app_config``, ``verbosity``, ``using``,
    etc.). Calling it directly with no args also works.

    Returns a list of ``{"name", "result"}`` dicts for the ops that ran
    this invocation (empty when everything was already marked).
    """
    ran = []
    for name, fn in _REGISTRY.items():
        if DataOpMarker.objects.filter(name=name).exists():
            continue
        logger.info("dataop '%s' starting", name)
        result = fn()
        DataOpMarker.objects.get_or_create(name=name)
        logger.info("dataop '%s' done (result=%s)", name, result)
        ran.append({"name": name, "result": result})
    return ran


# ── Registered data operations ─────────────────────────────────────
# Add new ops above the import (or anywhere at module scope with the
# decorator); they are picked up automatically at import time.

@register_dataop("import_pending_ledger_entries")
def _import_pending_ledger_entries():
    """Import the late W6/W7 retrospective ErrorEvent rows (task #238).

    Idempotent on ``(source, source_id)`` — re-runs add zero rows.
    Registered here so a service restart applies it without a manual
    ``testing_tools/errors.py --import-pending`` runbook.
    """
    from .errors import import_pending_ledger_entries
    return import_pending_ledger_entries()
