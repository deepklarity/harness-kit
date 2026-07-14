"""F45 mandate #2: no silent model switches.

Origin: F45 (no silent model switches mandate). Task #119.

Regression pins for the contract that any model switch at dispatch time
MUST be recorded with a reason in task metadata and a TaskHistory row
that names the policy.

Dispatch flow under test (odin/src/odin/orchestrator.py:3322)::

    task_obj = self.task_mgr.get_task(task_id)
    model = self._resolve_task_model(task_obj, agent_name)
    # <-- NEW: _record_model_pickup(task_obj, model, agent_name) here -->
    # ... harness dispatch follows ...

Two contract surfaces are pinned by these tests:

1. ``Orchestrator._resolve_task_model()`` precedence: metadata
   ``selected_model`` wins; ``task.model_name`` is the operator's
   authoritative fallback column (the Django Taskit field).
2. ``Orchestrator._record_model_pickup()`` (helper added by the
   implementation step) records the change with reason + policy
   whenever dispatch would use a different model than the task's
   authoritative values.

No actual DB writes. The ``task_mgr`` is a ``MagicMock``.

Tags:
- [mock]  mocked task_mgr, no I/O
- [simple]  pure orchestrator logic
"""

import pytest
from unittest.mock import MagicMock

from odin.models import AgentConfig, CostTier, OdinConfig
from odin.orchestrator import Orchestrator


# ── Test helpers ───────────────────────────────────────────────────────


def _make_orchestrator(tmp_path) -> Orchestrator:
    """Stub orchestrator with real config + mocked task_mgr.

    Mirrors the pattern in ``test_build_reflection_context.py``: build a
    real Orchestrator (so ``config.agents`` etc. are real), then replace
    ``task_mgr`` with a ``MagicMock`` to avoid any disk or backend I/O.
    """
    task_dir = tmp_path / "tasks"
    log_dir = tmp_path / "logs"
    cost_dir = tmp_path / "costs"
    spec_dir = tmp_path / "specs"
    for d in (task_dir, log_dir, cost_dir, spec_dir):
        d.mkdir(parents=True, exist_ok=True)

    agents = {
        "claude": AgentConfig(
            cli_command="claude",
            capabilities=["reasoning", "coding"],
            cost_tier=CostTier.HIGH,
            default_model="claude-sonnet-4-5",
            premium_model="claude-opus-4-6",
        ),
    }

    cfg = OdinConfig(
        base_agent="claude",
        board_backend="local",
        task_storage=str(task_dir),
        log_dir=str(log_dir),
        cost_storage=str(cost_dir),
        spec_storage=str(spec_dir),
        agents=agents,
    )
    orch = Orchestrator(cfg)
    orch.task_mgr = MagicMock()
    return orch


class _DuckTask:
    """Duck-typed task object that mirrors a Taskit Django backend payload.

    Pydantic's ``Task`` model has no ``model``/``model_name`` field, so
    these tests use a plain class (same pattern as
    ``test_resolve_task_model.py::TestResolveTaskModelExplicit``) so the
    orchestrator's ``getattr(task, ...)`` calls work without surprise.
    """

    def __init__(
        self,
        task_id: str = "test-task-119",
        metadata=None,
        model_name: str | None = None,
        model: str | None = None,
    ):
        self.id = task_id
        self.metadata = dict(metadata) if metadata else {}
        if model_name is not None:
            self.model_name = model_name
        if model is not None:
            self.model = model


def _resolve_helper(orch: Orchestrator) -> None:
    """pytest.fail with a clear message if the F45 helper is missing.

    Centralizes the missing-helper failure message so each test's intent
    is clear: the helper is the contract surface the F45 mandate demands.
    """
    if not hasattr(orch, "_record_model_pickup"):
        pytest.fail(
            "Orchestrator._record_model_pickup is not implemented; "
            "F45 mandate #2 (no silent model switches) requires it. "
            "Add the helper at orchestrator.py:3322 and invoke it from "
            "_execute_subtask() immediately after _resolve_task_model()."
        )


# ── Class 2 (passes today): picker precedence at dispatch ─────────────


class OrchestratorResolveTaskModelForDispatch:
    """[simple] Regression pins for _resolve_task_model() at dispatch.

    The dispatch path at orchestrator.py:3322 calls
    ``_resolve_task_model()`` immediately before harness execution.
    These tests pin the precedence contract the F45 mandate #2 history
    helper relies on (so the helper can compare the dispatch model to
    the authoritative values).

    Note: the class name follows the spec literally (no ``Test`` prefix).
    ``__test__ = True`` opts the class into pytest collection.
    """

    __test__ = True

    def test_resolve_task_model_returns_metadata_selected_model_first(
        self, tmp_path
    ):
        """metadata['selected_model'] wins over task.model_name.

        The plan router / TaskIt backend already merges ``model_name``
        into ``metadata['selected_model']`` (see ``backends/taskit.py``),
        but we still pin the in-memory precedence explicitly because
        the F45 helper compares against both surfaces.
        """
        orch = _make_orchestrator(tmp_path)
        task = _DuckTask(
            task_id="t-metadata-wins",
            metadata={"selected_model": "claude-opus-4-6"},
            model_name="claude-sonnet-4-5",
            model="claude-sonnet-4-5",
        )

        result = orch._resolve_task_model(task, agent_name="claude")

        assert result == "claude-opus-4-6", (
            "metadata['selected_model'] is the highest-priority source; "
            "task.model_name must not override it."
        )

    def test_resolve_task_model_falls_back_to_task_model_name(
        self, tmp_path
    ):
        """Empty metadata falls back to task.model_name.

        Sets both ``model_name`` (operator's authoritative column on the
        Django backend) and ``model`` (defensive attribute the picker
        currently reads). Both are pinned here so the F45 helper can
        rely on either surface.
        """
        orch = _make_orchestrator(tmp_path)
        task = _DuckTask(
            task_id="t-fallback-model-name",
            metadata={},
            model_name="claude-sonnet-4-5",
            model="claude-sonnet-4-5",
        )

        result = orch._resolve_task_model(task, agent_name="claude")

        assert result == "claude-sonnet-4-5", (
            "When metadata has no selected_model, _resolve_task_model "
            "must fall back to task.model_name (operator's authoritative "
            "column on the Django Taskit backend)."
        )


# ── Class 1 (FAILS today): change-history helper contract ──────────────


class ModelPickupHistoryRecorded:
    """[mock] F45 mandate #2: model change history must be recorded.

    Contract: when ``_resolve_task_model`` returns a different model
    than the task's authoritative values (``task.model_name`` or
    ``metadata['selected_model']``), the dispatch path must record the
    change via ``Orchestrator._record_model_pickup`` so a downstream
    auditor can see *why* the model switched, *who* triggered it, and
    *which policy* authorised it.

    The recorded history has three surfaces:

    1. ``task.metadata['last_model_change']`` -- dict with
       ``{old, new, reason, at, policy}``.
    2. ``task_mgr.add_history(field_name='model', old_value=...,
       new_value=..., changed_by=...)`` -- first-class history row.
    3. ``task_mgr.add_comment(...)`` with ``comment_type=STATUS_UPDATE``
       summarising the change in human-readable form.

    These tests fail today (helper not yet implemented) and will pass
    once the implementation step lands the helper at
    orchestrator.py:3322 and the history surface on task_mgr.

    Note: the class name follows the spec literally (no ``Test`` prefix).
    ``__test__ = True`` opts the class into pytest collection.
    """

    __test__ = True

    def test_model_change_records_history_with_reason_and_metadata(
        self, tmp_path
    ):
        """Dispatch with a different model records the change."""
        orch = _make_orchestrator(tmp_path)
        _resolve_helper(orch)  # pytest.fail if missing

        original_model = "claude-sonnet-4-5"
        dispatch_model = "claude-opus-4-6"
        task = _DuckTask(
            task_id="t-model-change",
            metadata={"selected_model": original_model},
            model_name=original_model,
            model=original_model,
        )

        orch._record_model_pickup(
            task=task,
            dispatch_model=dispatch_model,
            agent_name="claude",
            reason="lineup_default_promotion",
            policy="f45_no_silent_model_switch",
            actor="odin",
            changed_by="odin",
        )

        # 1. last_model_change dict on task metadata with the full contract.
        assert "last_model_change" in task.metadata, (
            "_record_model_pickup must write last_model_change to "
            "task.metadata when the dispatch model differs from the "
            "authoritative values."
        )
        change = task.metadata["last_model_change"]
        assert isinstance(change, dict), (
            f"last_model_change must be a dict, got {type(change).__name__}"
        )
        for key in ("old", "new", "reason", "at", "policy"):
            assert key in change, (
                f"last_model_change is missing required key '{key}'; "
                f"got keys: {sorted(change.keys())}"
            )
        assert change["old"] == original_model
        assert change["new"] == dispatch_model
        assert change["reason"] == "lineup_default_promotion"
        assert change["policy"] == "f45_no_silent_model_switch"
        # 'at' must be a non-empty timestamp string (ISO 8601 is the
        # convention used elsewhere in the orchestrator's metadata).
        assert isinstance(change["at"], str) and change["at"], (
            "last_model_change['at'] must be a non-empty ISO timestamp"
        )

        # 2. add_history was called with the field_name='model' row.
        assert hasattr(orch.task_mgr, "add_history"), (
            "F45 mandate #2 requires a TaskHistory surface on task_mgr "
            "(task_mgr.add_history). The implementation step must add it."
        )
        assert orch.task_mgr.add_history.called, (
            "_record_model_pickup must call task_mgr.add_history to "
            "write a first-class TaskHistory row."
        )
        history_kwargs = orch.task_mgr.add_history.call_args.kwargs
        assert history_kwargs.get("field_name") == "model", (
            f"TaskHistory row must target field_name='model', "
            f"got {history_kwargs.get('field_name')!r}"
        )
        assert history_kwargs.get("old_value") == original_model
        assert history_kwargs.get("new_value") == dispatch_model
        assert history_kwargs.get("changed_by"), (
            "TaskHistory row must name a changed_by actor "
            "(e.g., 'odin' or 'odin@claude')."
        )

        # 3. STATUS_UPDATE comment posted summarising the change.
        assert orch.task_mgr.add_comment.called, (
            "_record_model_pickup must post a STATUS_UPDATE comment "
            "summarising the model change for human reviewers."
        )
        comment_kwargs = orch.task_mgr.add_comment.call_args.kwargs
        ct = comment_kwargs.get("comment_type")
        assert ct is not None and "status" in str(ct).lower(), (
            f"Comment posted by _record_model_pickup must have "
            f"comment_type containing 'status' (e.g., STATUS_UPDATE); "
            f"got {ct!r}"
        )
        content = comment_kwargs.get("content", "")
        assert dispatch_model in content and original_model in content, (
            "STATUS_UPDATE comment must mention both old and new model "
            "names so reviewers can see what changed."
        )

    def test_same_model_dispatch_writes_no_history_row(self, tmp_path):
        """No-op path: same model means no metadata key, no comment, no row.

        This is the false-positive guard. Without it, every dispatch
        would spam comments and history rows, drowning out the
        legitimate changes.
        """
        orch = _make_orchestrator(tmp_path)
        _resolve_helper(orch)  # pytest.fail if missing

        same_model = "claude-sonnet-4-5"
        task = _DuckTask(
            task_id="t-same-model",
            metadata={"selected_model": same_model},
            model_name=same_model,
            model=same_model,
        )

        orch._record_model_pickup(
            task=task,
            dispatch_model=same_model,
            agent_name="claude",
            reason="lineup_default_promotion",
            policy="f45_no_silent_model_switch",
            actor="odin",
            changed_by="odin",
        )

        # No last_model_change key -- the dispatch is a no-op.
        assert "last_model_change" not in task.metadata, (
            "When dispatch model == authoritative model, "
            "_record_model_pickup must NOT add last_model_change to "
            "metadata (would create false-positive change events)."
        )

        # No history row -- silent dispatch is the EXPECTED case.
        if hasattr(orch.task_mgr, "add_history"):
            assert not orch.task_mgr.add_history.called, (
                "Same-model dispatch must not write a TaskHistory row; "
                "the F45 mandate only requires recording actual changes."
            )

        # No comment -- the comment exists to summarise a change.
        assert not orch.task_mgr.add_comment.called, (
            "Same-model dispatch must not post a STATUS_UPDATE comment; "
            "comments are reserved for actual model changes."
        )
