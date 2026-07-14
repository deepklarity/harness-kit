"""Tests for the advisor consult-when-stuck trial module (W7 routing-and-cost).

Tags: [io] + [async] — file I/O via tmp_path and in-process asyncio, no
subprocess/network (the strong-model call is injected via consult_fn).
"""

import asyncio

import pytest

from odin.advisor import (
    AdvisorWatcher,
    ConsultResult,
    build_advisor_prompt,
    record_advisor_cost,
)
from odin.cost_tracking import CostStore, CostTracker
from odin.models import TaskResult


def _fixed_consult(answer="Use approach B.", usage=None):
    """A consult_fn stub that records every call and returns a fixed answer."""
    usage = usage or {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    calls = []

    async def _consult(question, task_title):
        calls.append((question, task_title))
        return ConsultResult(question=question, answer=answer, token_usage=dict(usage))

    _consult.calls = calls
    return _consult


# ── build_advisor_prompt ────────────────────────────────────────────────


class TestBuildAdvisorPrompt:
    def test_includes_question(self):
        prompt = build_advisor_prompt("Should I use approach A or B?")
        assert "Should I use approach A or B?" in prompt

    def test_includes_task_title_when_given(self):
        prompt = build_advisor_prompt("What now?", task_title="Fix the flaky test")
        assert "Fix the flaky test" in prompt

    def test_omits_task_title_section_when_blank(self):
        prompt = build_advisor_prompt("What now?", task_title="")
        assert "Task under advisement" not in prompt

    def test_instructs_concise_no_clarifying_questions_back(self):
        prompt = build_advisor_prompt("Q")
        assert "clarifying questions" in prompt.lower()


# ── AdvisorWatcher: single consult ──────────────────────────────────────


class TestAdvisorWatcherSingleConsult:
    def test_no_op_when_no_request_file(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult)

        asyncio.run(watcher._check_once())

        assert consult.calls == []
        assert not watcher.answer_path.exists()
        assert watcher.consults == []

    def test_answers_a_request(self, tmp_path):
        consult = _fixed_consult(answer="Do X.")
        watcher = AdvisorWatcher(str(tmp_path), consult)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)
        watcher.request_path.write_text("Should I use X or Y?")

        asyncio.run(watcher._check_once())

        assert len(consult.calls) == 1
        assert consult.calls[0] == ("Should I use X or Y?", "")
        assert watcher.answer_path.read_text().strip() == "Do X."
        assert len(watcher.consults) == 1

    def test_ignores_unchanged_request_content(self, tmp_path):
        """Re-polling identical, still-unconsumed content is not a new consult."""
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)
        watcher.request_path.write_text("Same question")

        asyncio.run(watcher._check_once())
        asyncio.run(watcher._check_once())

        assert len(consult.calls) == 1

    def test_empty_request_file_is_a_no_op(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)
        watcher.request_path.write_text("   \n")

        asyncio.run(watcher._check_once())

        assert consult.calls == []

    def test_new_question_after_answer_triggers_a_second_consult(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=2)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        watcher.request_path.write_text("First question")
        asyncio.run(watcher._check_once())
        watcher.request_path.write_text("Second, different question")
        asyncio.run(watcher._check_once())

        assert len(consult.calls) == 2


# ── AdvisorWatcher: cap enforcement ─────────────────────────────────────


class TestAdvisorWatcherCap:
    def test_third_request_is_refused_not_consulted(self, tmp_path):
        """Acceptance: a third consult beyond the cap is refused."""
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=2)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        for q in ("Question one", "Question two", "Question three"):
            watcher.request_path.write_text(q)
            asyncio.run(watcher._check_once())

        assert len(consult.calls) == 2  # the model was never called a 3rd time
        assert watcher.refused_count == 1
        assert len(watcher.consults) == 2
        assert "cap reached" in watcher.answer_path.read_text().lower()

    def test_refusal_message_mentions_cap_count(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=1)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        watcher.request_path.write_text("Q1")
        asyncio.run(watcher._check_once())
        watcher.request_path.write_text("Q2")
        asyncio.run(watcher._check_once())

        assert "1 consult" in watcher.answer_path.read_text()

    def test_cap_of_zero_refuses_the_first_request(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=0)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)
        watcher.request_path.write_text("Any question")

        asyncio.run(watcher._check_once())

        assert consult.calls == []
        assert watcher.refused_count == 1


# ── AdvisorWatcher: full run loop (mocked stuck run) ────────────────────


class TestAdvisorWatcherRunLoop:
    def test_mocked_stuck_run_consults_answers_and_refuses_third(self, tmp_path):
        """The acceptance scenario end to end: a mocked stuck run writes
        three requests while the watcher polls in the background; the first
        two are answered, the third is refused."""
        consult = _fixed_consult(answer="Answer body.")
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=2, poll_interval=0.02)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        async def scenario():
            run_task = asyncio.ensure_future(watcher.run())
            await asyncio.sleep(0.05)
            watcher.request_path.write_text("First stuck question")
            await asyncio.sleep(0.1)
            watcher.request_path.write_text("Second stuck question")
            await asyncio.sleep(0.1)
            watcher.request_path.write_text("Third stuck question")
            await asyncio.sleep(0.1)
            watcher.stop()
            await asyncio.wait_for(run_task, timeout=2)

        asyncio.run(scenario())

        assert len(watcher.consults) == 2
        assert watcher.refused_count == 1
        assert len(consult.calls) == 2
        assert watcher.answer_path.exists()

    def test_run_loop_is_a_no_op_when_never_asked(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, poll_interval=0.02)

        async def scenario():
            run_task = asyncio.ensure_future(watcher.run())
            await asyncio.sleep(0.08)
            watcher.stop()
            await asyncio.wait_for(run_task, timeout=2)

        asyncio.run(scenario())

        assert watcher.consults == []
        assert not watcher.answer_path.exists()


# ── total_token_usage ────────────────────────────────────────────────────


class TestTotalTokenUsage:
    def test_sums_across_consults(self, tmp_path):
        consult = _fixed_consult()
        watcher = AdvisorWatcher(str(tmp_path), consult, max_consults=2)
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        watcher.request_path.write_text("Q1")
        asyncio.run(watcher._check_once())
        watcher.request_path.write_text("Q2")
        asyncio.run(watcher._check_once())

        totals = watcher.total_token_usage
        assert totals["total_tokens"] == 300
        assert totals["input_tokens"] == 200
        assert totals["output_tokens"] == 100

    def test_empty_when_no_consults(self, tmp_path):
        watcher = AdvisorWatcher(str(tmp_path), _fixed_consult())
        assert watcher.total_token_usage == {}


# ── record_advisor_cost: tokens appear on the task ──────────────────────


class TestRecordAdvisorCost:
    def test_tokens_appear_on_the_task(self, tmp_path):
        """Acceptance: advisor tokens appear on the task via the SAME
        CostStore.summarize_task aggregation used for retries — no bespoke
        league-table plumbing needed."""
        store = CostStore(str(tmp_path / "costs"))
        tracker = CostTracker(store)

        tracker.record_task(
            task_id="task-1",
            spec_id="spec-1",
            result=TaskResult(
                success=True,
                metadata={"usage": {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}},
            ),
            model="glm-4.7",
        )

        consults = [
            ConsultResult(question="Q1", answer="A1", token_usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}),
            ConsultResult(question="Q2", answer="A2", token_usage={"input_tokens": 120, "output_tokens": 60, "total_tokens": 180}),
        ]
        record_advisor_cost(
            tracker, task_id="task-1", spec_id="spec-1", consults=consults, model="claude-sonnet-5",
        )

        summary = store.summarize_task("spec-1", "task-1")
        assert summary["invocation_count"] == 3  # main execution + 2 consults
        assert summary["total_tokens"] == 1500 + 150 + 180

    def test_records_one_entry_per_consult_tagged_as_advisor(self, tmp_path):
        store = CostStore(str(tmp_path / "costs"))
        tracker = CostTracker(store)
        consults = [
            ConsultResult(question="Q1", answer="A1", token_usage={"total_tokens": 10}),
        ]

        records = record_advisor_cost(
            tracker, task_id="task-2", spec_id="spec-2", consults=consults, model="claude-sonnet-5",
        )

        assert len(records) == 1
        assert records[0].task_id == "task-2"
        assert records[0].agent == "advisor"
        assert records[0].model == "claude-sonnet-5"
        assert records[0].total_tokens == 10

    def test_no_consults_records_nothing(self, tmp_path):
        store = CostStore(str(tmp_path / "costs"))
        tracker = CostTracker(store)

        records = record_advisor_cost(
            tracker, task_id="task-3", spec_id=None, consults=[], model="claude-sonnet-5",
        )

        assert records == []
