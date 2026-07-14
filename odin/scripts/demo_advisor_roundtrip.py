#!/usr/bin/env python3
"""Proof-of-work demo: a mocked stuck run consults, gets answered, and a
third consult beyond the cap is refused; consult tokens land on the task's
cost record (task 243 acceptance criterion).

Run from odin/: python3 scripts/demo_advisor_roundtrip.py
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from odin.advisor import AdvisorWatcher, ConsultResult, record_advisor_cost
from odin.cost_tracking import CostStore, CostTracker
from odin.models import TaskResult


async def fake_strong_model_consult(question: str, task_title: str) -> ConsultResult:
    """Stands in for the real claude-sonnet-5 call — this is the ONE-model-call
    contract from the trial brief, mocked for a deterministic proof run."""
    print(f"  [advisor] strong-model call for task {task_title!r}: {question!r}")
    answer = f"Recommendation for '{question[:40]}...': proceed with option B."
    return ConsultResult(
        question=question,
        answer=answer,
        token_usage={"input_tokens": 400, "output_tokens": 120, "total_tokens": 520},
    )


async def main():
    work_dir = Path(tempfile.mkdtemp(prefix="odin_advisor_demo_"))
    try:
        print(f"worktree: {work_dir}")
        watcher = AdvisorWatcher(
            working_dir=str(work_dir),
            consult_fn=fake_strong_model_consult,
            max_consults=2,
            poll_interval=0.02,
            task_title="Fix flaky retry test (demo)",
        )
        watcher.request_path.parent.mkdir(parents=True, exist_ok=True)

        run_task = asyncio.ensure_future(watcher.run())

        print("\n=== executor writes request #1 (genuinely stuck) ===")
        watcher.request_path.write_text(
            "The retry test fails the same assertion twice with different "
            "timing. I tried adding a sleep and mocking time.time(); both "
            "still race. Should I use a monotonic fake clock fixture or "
            "restructure the retry loop to accept an injectable clock?"
        )
        await asyncio.sleep(0.08)
        print(f"  answer file: {watcher.answer_path.read_text().strip()}")

        print("\n=== executor writes request #2 (different question) ===")
        watcher.request_path.write_text(
            "Should the injectable clock live on the retry helper or be "
            "passed down from the caller?"
        )
        await asyncio.sleep(0.08)
        print(f"  answer file: {watcher.answer_path.read_text().strip()}")

        print("\n=== executor writes request #3 (beyond the cap) ===")
        watcher.request_path.write_text("One more thing I'm unsure about...")
        await asyncio.sleep(0.08)
        print(f"  answer file: {watcher.answer_path.read_text().strip()}")

        watcher.stop()
        await asyncio.wait_for(run_task, timeout=2)

        print(f"\nconsults answered: {len(watcher.consults)}")
        print(f"consults refused:  {watcher.refused_count}")
        assert len(watcher.consults) == 2, "expected exactly 2 answered consults"
        assert watcher.refused_count == 1, "expected exactly 1 refusal (cap=2)"

        # ── Cost accounting: tokens land on the task ────────────────────
        cost_dir = work_dir / ".odin" / "costs"
        store = CostStore(str(cost_dir))
        tracker = CostTracker(store)

        tracker.record_task(
            task_id="demo-task-243",
            spec_id="demo-spec",
            result=TaskResult(
                success=True,
                metadata={"usage": {"input_tokens": 5000, "output_tokens": 2000, "total_tokens": 7000}},
            ),
            model="zai-coding-plan/glm-4.7",
        )
        record_advisor_cost(
            tracker,
            task_id="demo-task-243",
            spec_id="demo-spec",
            consults=watcher.consults,
            model="claude-sonnet-5",
        )

        summary = store.summarize_task("demo-spec", "demo-task-243")
        print(f"\ncost summary for demo-task-243: {summary}")
        assert summary["invocation_count"] == 3
        assert summary["total_tokens"] == 7000 + 520 + 520

        print("\nOK — round trip complete: consult, answer, refuse-on-cap, tokens on task.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
