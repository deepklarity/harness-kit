"""ONE routing policy — failure-class routing consolidated (task #328).

Before #328 the "who gets this task when X fails" answer lived in scattered
code paths: the execution_result endpoint tier-jumped on ANY failure via
``_maybe_escalate_model`` (it restarted a truncated glm run cold on a
higher tier), while the Celery worker path used the per-class
``failure_policy`` table. This test suite pins the unified policy:

  Initial routing         cheapest capable (odin _route_task — unchanged).
  Protocol / infra fail   retry SAME agent, then same-tier ROUTING PEER,
                          never a tier jump; hold only after the peer.
  Capability fail         repeated review rejection → escalate exactly ONE
                          deliberate tier.
  Crash / env / disk /    hold for a human — no automatic switch.
  unknown
  Preference order        glm/minimax first, agy where fine, claude/codex
                          for firepower — drives peer selection, editable
                          per board and live.

Every automatic reassignment posts a rule-named comment (no silent
switches). The board's ``routing_policy`` is the single editable override
surface, round-tripping through the standard board PATCH.

NonVisual — no browser/screenshots needed.
"""

import os

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from types import SimpleNamespace
from unittest.mock import patch

from tests.base import APITestCase
from tasks import failure_policy as fp
from tasks.failure_policy import (
    DEFAULT_PREFERENCE_ORDER,
    CapabilityPolicy,
    apply_failure_policy,
    capability_escalate_after,
    capability_policy,
    effective_preference_order,
    resolve_policy,
)
from tasks.models import (
    BoardMembership,
    CommentType,
    ReflectionReport,
    ReflectionStatus,
    Task,
    TaskComment,
    TaskStatus,
    User,
    UserRole,
)


# ── Agents from the active lineup (data/agent_models.json) ───────────
GLM_MODEL = "zai-coding-plan/glm-5.2"
MINIMAX_MODEL = "minimax-coding-plan/MiniMax-M3"
CLAUDE_MODEL = "claude-sonnet-5"


def _fail_meta(failure_class, **extra):
    md = {"failure_class": failure_class,
          "last_failure_reason": extra.pop("reason", "boom")}
    md.update(extra)
    return md


class PolicyTableIsOneTable(APITestCase):
    """The single policy table + its editable override surface."""

    def test_default_preference_order_cheap_first(self):
        # glm/minimax before claude/codex — the standing preference.
        order = DEFAULT_PREFERENCE_ORDER
        self.assertLess(order.index("glm"), order.index("claude"))
        self.assertLess(order.index("minimax"), order.index("codex"))
        self.assertIn("agy", order)

    def test_transient_infra_classes_have_peer_fallback(self):
        # Protocol/infra classes retry same then PEER (no tier jump).
        for cls in ("truncation", "silent_hang", "transport_error",
                    "lock_race", "stale_execution", "error_loop"):
            self.assertTrue(
                fp.DEFAULT_POLICY_TABLE[cls].peer_fallback,
                f"{cls} must fall back to a routing peer",
            )
        # Human-hold and reassign classes must NOT peer-fallback.
        for cls in ("crash", "env_missing", "disk_exhaustion", "unknown",
                    "quota_exhaustion"):
            self.assertFalse(fp.DEFAULT_POLICY_TABLE[cls].peer_fallback)

    def test_crash_and_unknown_hold_for_human(self):
        self.assertEqual(resolve_policy({"failure_class": "crash"}).action, "human")
        self.assertEqual(resolve_policy({"failure_class": "unknown"}).action, "human")
        self.assertEqual(resolve_policy({}).action, "human")  # missing → human

    def test_board_routing_policy_override_changes_action(self):
        # Editing the board's routing_policy alters the resolved policy —
        # the round-trip that "alters real dispatch behavior".
        board = SimpleNamespace(routing_policy={
            "failure_actions": {"truncation": {"max_retries": 7}},
        })
        base = resolve_policy({"failure_class": "truncation"})
        overridden = resolve_policy({"failure_class": "truncation"}, board=board)
        self.assertEqual(base.max_retries, 2)
        self.assertEqual(overridden.max_retries, 7)

    def test_board_can_override_failure_class_action(self):
        # The per-failure-class ACTION itself is board-editable — not just the
        # retry cap. Switching crash from human→auto_requeue is the missing
        # dimension of "full per-failure-class policy editable"; it must flow
        # through resolve_policy so a settings edit alters real dispatch.
        board = SimpleNamespace(routing_policy={
            "failure_actions": {"crash": {"action": "auto_requeue", "max_retries": 2}},
        })
        base = resolve_policy({"failure_class": "crash"})
        overridden = resolve_policy({"failure_class": "crash"}, board=board)
        self.assertEqual(base.action, "human")
        self.assertEqual(overridden.action, "auto_requeue")
        self.assertEqual(overridden.max_retries, 2)
        # And the effective table the settings UI reads reflects it.
        self.assertEqual(
            fp.effective_policy_table(board)["crash"]["action"], "auto_requeue",
        )

    def test_invalid_action_override_is_ignored(self):
        # Garbage action values never corrupt the resolved policy — the class
        # default stands. Only auto_requeue/reassign/human are accepted.
        board = SimpleNamespace(routing_policy={
            "failure_actions": {"crash": {"action": "rm_-rf"}},
        })
        self.assertEqual(
            resolve_policy({"failure_class": "crash"}, board=board).action, "human",
        )

    def test_effective_preference_order_uses_board_override(self):
        board = SimpleNamespace(routing_policy={"preference_order": ["minimax", "glm"]})
        self.assertEqual(effective_preference_order(board), ["minimax", "glm"])
        # Empty policy → the built-in default.
        self.assertEqual(
            effective_preference_order(SimpleNamespace(routing_policy={})),
            list(DEFAULT_PREFERENCE_ORDER),
        )

    def test_capability_escalate_after_default_and_override(self):
        self.assertEqual(capability_escalate_after(SimpleNamespace(routing_policy={})), 2)
        self.assertEqual(
            capability_escalate_after(SimpleNamespace(routing_policy={"capability_escalate_after": 3})),
            3,
        )


class ExecutionFailureRouting(APITestCase):
    """Protocol/infra vs crash at the execution_result endpoint — the site
    that used to tier-jump on ANY failure."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(
            allow_project_root_execution=True,
            escalation_enabled=True,
            # Priority list that the OLD path WOULD have tier-jumped along:
            # glm (low) → claude (high). The new policy must NOT use it here.
            model_escalation_priority=[
                {"agent_name": "claude", "model_name": CLAUDE_MODEL},
                {"agent_name": "glm", "model_name": GLM_MODEL},
            ],
        )
        self.glm = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": GLM_MODEL, "is_default": True}],
        )
        self.minimax = User.objects.create(
            name="minimax", email="minimax@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": MINIMAX_MODEL, "is_default": True}],
        )
        self.claude = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": CLAUDE_MODEL, "is_default": True}],
        )
        for u in (self.glm, self.minimax, self.claude):
            BoardMembership.objects.create(board=self.board, user=u)

    def _post_failure(self, task, failure_type, failure_reason):
        return self.client.post(
            f"/tasks/{task.id}/execution_result/",
            {
                "execution_result": {
                    "success": False,
                    "raw_output": "",
                    "error": failure_reason,
                    "duration_ms": 1000.0,
                    "agent": "glm",
                    "failure_type": failure_type,
                    "failure_reason": failure_reason,
                    "metadata": {},
                },
                "status": "FAILED",
                "updated_by": "glm+glm-5.2@odin.agent",
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_protocol_failure_retries_same_agent_no_tier_jump(self, mock_strategy):
        """A truncation (protocol hiccup) must retry the SAME agent, not
        tier-jump to claude — the exact #328 bug."""
        task = self.make_task(
            self.board, title="truncated glm run", status=TaskStatus.EXECUTING,
            assignee=self.glm, model_name=GLM_MODEL,
        )
        resp = self._post_failure(
            task, "agent_execution_failure",
            "response truncated mid-generation, did not emit ODIN-STATUS",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.metadata.get("failure_class"), "truncation")
        # Retried on the SAME agent + model — NOT escalated to claude.
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.glm.id)
        self.assertEqual(task.model_name, GLM_MODEL)
        self.assertNotEqual(task.assignee_id, self.claude.id)

    @patch("tasks.execution.get_strategy")
    def test_crash_holds_for_human(self, mock_strategy):
        """A crash holds for a human — no automatic switch."""
        task = self.make_task(
            self.board, title="crashed run", status=TaskStatus.EXECUTING,
            assignee=self.glm, model_name=GLM_MODEL,
        )
        resp = self._post_failure(
            task, "internal_error", "unhandled exception: KeyError('x')",
        )
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.metadata.get("failure_class"), "crash")
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.assignee_id, self.glm.id)  # not switched
        # A rule-named audit comment explains why nothing auto-fired.
        joined = " ".join(
            TaskComment.objects.filter(task=task).values_list("content", flat=True)
        )
        self.assertIn("class=crash", joined)
        self.assertIn("human", joined)

    @patch("tasks.execution.get_strategy")
    def test_protocol_failure_routes_to_same_tier_peer_after_retries(self, mock_strategy):
        """When same-agent retries are exhausted, route to a same-tier PEER
        (glm → minimax, both LOW) — never a tier jump to claude."""
        task = self.make_task(
            self.board, title="glm exhausted", status=TaskStatus.FAILED,
            assignee=self.glm, model_name=GLM_MODEL,
            metadata=_fail_meta("truncation", auto_redispatch_count=2),
        )
        moved = apply_failure_policy(task)
        self.assertTrue(moved)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        # Peer is the same-tier agent, NOT claude (high tier).
        self.assertEqual(task.assignee_id, self.minimax.id)
        self.assertNotEqual(task.assignee_id, self.claude.id)
        self.assertTrue(task.metadata.get("routing_peer_reassigned"))
        comment = TaskComment.objects.filter(task=task).last()
        self.assertIn("routing peer", comment.content)
        self.assertIn("NOT a tier jump", comment.content)

    @patch("tasks.execution.get_strategy")
    def test_peer_preference_order_is_board_editable(self, mock_strategy):
        """The board's preference order decides which peer is picked —
        proving a settings edit alters real dispatch."""
        # Only claude + a second low agent so tier-preference is exercised.
        self.board.routing_policy = {"preference_order": ["agy", "minimax", "glm"]}
        self.board.save(update_fields=["routing_policy"])
        agy = User.objects.create(
            name="agy", email="agy@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": "Gemini 3.5 Flash (High)", "is_default": True}],
        )
        BoardMembership.objects.create(board=self.board, user=agy)
        task = self.make_task(
            self.board, title="glm exhausted 2", status=TaskStatus.FAILED,
            assignee=self.glm, model_name=GLM_MODEL,
            metadata=_fail_meta("truncation", auto_redispatch_count=2),
        )
        apply_failure_policy(task)
        task.refresh_from_db()
        # agy sorts first in the new preference order among same-tier peers.
        self.assertEqual(task.assignee_id, agy.id)


class CapabilityEscalation(APITestCase):
    """Repeated review rejection → escalate exactly one deliberate tier."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board(
            escalation_enabled=True,
            failure_max_retries=3,
            model_escalation_priority=[
                {"agent_name": "claude", "model_name": CLAUDE_MODEL},
                {"agent_name": "glm", "model_name": GLM_MODEL},
            ],
        )
        self.glm = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": GLM_MODEL, "is_default": True}],
        )
        self.claude = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": CLAUDE_MODEL, "is_default": True}],
        )
        for u in (self.glm, self.claude):
            BoardMembership.objects.create(board=self.board, user=u)

    def _completed_needs_work(self, task, summary):
        return ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model=CLAUDE_MODEL,
            requested_by="system@taskit", status=ReflectionStatus.COMPLETED,
            verdict="NEEDS_WORK", verdict_summary=summary,
        )

    def _running_report(self, task):
        return ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model=CLAUDE_MODEL,
            requested_by="system@taskit", status=ReflectionStatus.RUNNING,
        )

    def _reject(self, report):
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Code quality needs improvement; tests still red.",
                "quota_failure": "none.",
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_first_rejection_keeps_same_agent(self, mock_strategy):
        task = self.make_task(
            self.board, title="cap task", status=TaskStatus.REVIEW,
            assignee=self.glm, model_name=GLM_MODEL,
        )
        resp = self._reject(self._running_report(task))  # completed_count == 1
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(task.assignee_id, self.glm.id)  # continuity, no escalation
        self.assertEqual(task.model_name, GLM_MODEL)

    @patch("tasks.execution.get_strategy")
    def test_double_rejection_escalates_exactly_one_tier(self, mock_strategy):
        task = self.make_task(
            self.board, title="cap task 2", status=TaskStatus.REVIEW,
            assignee=self.glm, model_name=GLM_MODEL,
        )
        self._completed_needs_work(task, "Attempt 1: still failing review.")
        resp = self._reject(self._running_report(task))  # completed_count == 2
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        # Escalated exactly one tier: glm (index 1) → claude (index 0).
        self.assertEqual(task.assignee_id, self.claude.id)
        self.assertEqual(task.model_name, CLAUDE_MODEL)
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        joined = " ".join(
            TaskComment.objects.filter(task=task).values_list("content", flat=True)
        )
        self.assertIn("Capability failure", joined)
        self.assertIn("one tier", joined)


class CapabilityPolicyEngine(APITestCase):
    """The capability-escalation decision is resolved by ONE engine
    (task #328 follow-up): ``capability_policy`` reads the board's
    ``routing_policy`` first (the single editable surface) and falls back
    to the legacy board fields only when the policy is silent. This kills
    the flag-gated capability path that lived outside the policy engine.
    """

    def test_defaults_fall_back_to_legacy_board_fields(self):
        board = SimpleNamespace(
            routing_policy={},
            escalation_enabled=True,
            failure_max_retries=3,
        )
        pol = capability_policy(board)
        self.assertIsInstance(pol, CapabilityPolicy)
        self.assertTrue(pol.enabled)          # from escalation_enabled
        self.assertEqual(pol.escalate_after, 2)   # DEFAULT_CAPABILITY_ESCALATE_AFTER
        self.assertEqual(pol.max_escalations, 3)  # from failure_max_retries

    def test_routing_policy_enabled_flag_wins_over_legacy(self):
        # Legacy flag says ON, but the policy engine says OFF — the policy
        # (the single editable surface) must win. This is the exact
        # "flag-gated path outside the engine" the review flagged.
        board = SimpleNamespace(
            routing_policy={"capability_escalation_enabled": False},
            escalation_enabled=True,
            failure_max_retries=3,
        )
        self.assertFalse(capability_policy(board).enabled)

    def test_routing_policy_overrides_escalate_after_and_max(self):
        board = SimpleNamespace(
            routing_policy={
                "capability_escalate_after": 4,
                "capability_max_escalations": 6,
            },
            escalation_enabled=True,
            failure_max_retries=3,
        )
        pol = capability_policy(board)
        self.assertEqual(pol.escalate_after, 4)
        self.assertEqual(pol.max_escalations, 6)


class CapabilityEscalationGatedByPolicy(APITestCase):
    """A double review-rejection escalation is gated by the POLICY ENGINE,
    not the raw ``escalation_enabled`` flag. Disabling capability
    escalation in routing_policy stops the tier jump even when the legacy
    flag is still True — proving the path now flows through the one engine.
    """

    def setUp(self):
        super().setUp()
        self.board = self.make_board(
            escalation_enabled=True,          # legacy flag ON
            failure_max_retries=3,
            model_escalation_priority=[
                {"agent_name": "claude", "model_name": CLAUDE_MODEL},
                {"agent_name": "glm", "model_name": GLM_MODEL},
            ],
            # Policy engine turns capability escalation OFF — must win.
            routing_policy={"capability_escalation_enabled": False},
        )
        self.glm = User.objects.create(
            name="glm", email="glm@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": GLM_MODEL, "is_default": True}],
        )
        self.claude = User.objects.create(
            name="claude", email="claude@odin.agent", role=UserRole.AGENT,
            available_models=[{"name": CLAUDE_MODEL, "is_default": True}],
        )
        for u in (self.glm, self.claude):
            BoardMembership.objects.create(board=self.board, user=u)

    def _completed_needs_work(self, task, summary):
        return ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model=CLAUDE_MODEL,
            requested_by="system@taskit", status=ReflectionStatus.COMPLETED,
            verdict="NEEDS_WORK", verdict_summary=summary,
        )

    def _running_report(self, task):
        return ReflectionReport.objects.create(
            task=task, reviewer_agent="claude", reviewer_model=CLAUDE_MODEL,
            requested_by="system@taskit", status=ReflectionStatus.RUNNING,
        )

    def _reject(self, report):
        return self.client.patch(
            f"/reflections/{report.id}/",
            {
                "status": "COMPLETED",
                "verdict": "NEEDS_WORK",
                "verdict_summary": "Still failing review.",
                "quota_failure": "none.",
            },
            format="json",
        )

    @patch("tasks.execution.get_strategy")
    def test_policy_disable_blocks_escalation_despite_legacy_flag(self, mock_strategy):
        task = self.make_task(
            self.board, title="cap gated", status=TaskStatus.REVIEW,
            assignee=self.glm, model_name=GLM_MODEL,
        )
        self._completed_needs_work(task, "Attempt 1.")
        resp = self._reject(self._running_report(task))  # completed_count == 2
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        # No tier jump — stayed on glm because the POLICY disabled it,
        # even though board.escalation_enabled is still True. completed_count
        # is 2 and escalate_after defaults to 2, so WITHOUT the policy gate
        # this would have escalated to claude.
        self.assertEqual(task.assignee_id, self.glm.id)
        self.assertEqual(task.model_name, GLM_MODEL)
        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)


class RoutingPolicyRoundTrip(APITestCase):
    """The board's routing_policy round-trips through the standard API and
    surfaces in routing-config for the settings UI."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()

    def test_patch_board_persists_routing_policy(self):
        resp = self.client.patch(
            f"/boards/{self.board.id}/",
            {"routing_policy": {"preference_order": ["minimax", "glm"],
                                 "capability_escalate_after": 3}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.board.refresh_from_db()
        self.assertEqual(self.board.routing_policy["preference_order"], ["minimax", "glm"])
        self.assertEqual(self.board.routing_policy["capability_escalate_after"], 3)

    def test_patch_board_persists_failure_action_override(self):
        # PATCH the board with a per-class ACTION override; assert it persists
        # AND flows into the resolved policy + effective_policy_table (what the
        # settings UI reads) — the round-trip that "alters real dispatch".
        resp = self.client.patch(
            f"/boards/{self.board.id}/",
            {"routing_policy": {
                "failure_actions": {"crash": {"action": "auto_requeue", "max_retries": 3}},
            }},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.board.refresh_from_db()
        self.assertEqual(
            self.board.routing_policy["failure_actions"]["crash"]["action"],
            "auto_requeue",
        )
        pol = resolve_policy({"failure_class": "crash"}, board=self.board)
        self.assertEqual(pol.action, "auto_requeue")
        self.assertEqual(pol.max_retries, 3)
        self.assertEqual(
            fp.effective_policy_table(self.board)["crash"]["action"], "auto_requeue",
        )

    def test_routing_config_exposes_effective_policy(self):
        self.board.routing_policy = {"preference_order": ["minimax", "glm"]}
        self.board.save(update_fields=["routing_policy"])
        resp = self.client.get(f"/boards/{self.board.id}/routing-config/")
        self.assertEqual(resp.status_code, 200)
        rp = resp.data["routing_policy"]
        self.assertEqual(rp["preference_order"], ["minimax", "glm"])
        self.assertEqual(rp["default_preference_order"], list(DEFAULT_PREFERENCE_ORDER))
        # Every failure class is rendered with its resolved action.
        self.assertEqual(rp["failure_actions"]["truncation"]["action"], "auto_requeue")
        self.assertTrue(rp["failure_actions"]["truncation"]["peer_fallback"])
        self.assertEqual(rp["failure_actions"]["crash"]["action"], "human")

    def test_routing_config_exposes_capability_controls(self):
        # The full editable policy — capability enabled + escalate-after +
        # max — must be surfaced so the settings UI can render and edit it.
        self.board.routing_policy = {
            "capability_escalation_enabled": False,
            "capability_escalate_after": 4,
            "capability_max_escalations": 6,
        }
        self.board.save(update_fields=["routing_policy"])
        resp = self.client.get(f"/boards/{self.board.id}/routing-config/")
        self.assertEqual(resp.status_code, 200)
        rp = resp.data["routing_policy"]
        self.assertFalse(rp["capability_escalation_enabled"])
        self.assertEqual(rp["capability_escalate_after"], 4)
        self.assertEqual(rp["capability_max_escalations"], 6)

    def test_patch_board_persists_capability_controls(self):
        resp = self.client.patch(
            f"/boards/{self.board.id}/",
            {"routing_policy": {
                "capability_escalation_enabled": False,
                "capability_escalate_after": 3,
                "failure_actions": {"truncation": {"max_retries": 5}},
            }},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.board.refresh_from_db()
        rp = self.board.routing_policy
        self.assertFalse(rp["capability_escalation_enabled"])
        self.assertEqual(rp["capability_escalate_after"], 3)
        self.assertEqual(rp["failure_actions"]["truncation"]["max_retries"], 5)
        # And the override flows into the resolved policy (real dispatch).
        self.assertEqual(
            resolve_policy({"failure_class": "truncation"}, board=self.board).max_retries,
            5,
        )
