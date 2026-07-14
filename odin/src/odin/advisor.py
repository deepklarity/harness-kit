"""Advisor pattern: bounded consult-when-stuck escalation to a strong model.

A cheap executor agent may hit a genuinely stuck point mid-task — failing
the same test twice, or an architecture choice it holds real uncertainty
about. Instead of grinding on it or failing into a rework round, it can
write ONE question to ``.odin/advice_request.md`` in its worktree and keep
working on other parts of the task if possible. A host-side
:class:`AdvisorWatcher` polls for that file while the harness runs, answers
it with a single strong-model call, and writes ``.odin/advice.md`` for the
executor to read. Consults are capped per run (default 2, config-driven);
once the cap is hit, the watcher writes a refusal instead of calling the
model, so the executor is never left waiting on a file that will never
appear.

Cited result (docs/wiki/agent-practices/the-advisor-strategy.md): pairing a
cheap executor with a capped strong-model advisor drops cost while holding
quality — this module is the trial harness for that pattern.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

logger = logging.getLogger("odin.advisor")

REQUEST_RELPATH = ".odin/advice_request.md"
ANSWER_RELPATH = ".odin/advice.md"
DEFAULT_MAX_CONSULTS = 2
DEFAULT_ADVISOR_MODEL = "claude-sonnet-5"
DEFAULT_ADVISOR_AGENT = "claude"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

CAP_REFUSAL_TEMPLATE = (
    "Advisor cap reached ({cap} consult(s) already used this run). "
    "No further advice will be given — proceed with your best judgment "
    "and do not wait for a reply to this question."
)


def build_advisor_prompt(question: str, task_title: str = "") -> str:
    """Build the one-shot prompt sent to the strong-model advisor.

    Deliberately minimal: no MCP tools, no proof envelope, no repo-wide
    context dump — the advisor answers the question, nothing else.
    """
    header = f"## Task under advisement\n{task_title.strip()}\n\n" if task_title.strip() else ""
    return (
        "You are a strong-model advisor consulted by a cheaper executor "
        "agent that hit a genuinely stuck point mid-task (a test failing "
        "twice in a row, or a real architecture uncertainty). Answer ONLY "
        "the question below — concisely and actionably. Do not restate "
        "the question, do not ask clarifying questions back: give your "
        "best recommendation from the information provided.\n\n"
        f"{header}## Question\n{question.strip()}\n"
    )


@dataclass
class ConsultResult:
    """One answered (or about-to-be-answered) consult."""

    question: str
    answer: str
    token_usage: dict = field(default_factory=dict)
    duration_ms: float = 0.0


# (question, task_title) -> ConsultResult
ConsultFn = Callable[[str, str], Awaitable[ConsultResult]]


class AdvisorWatcher:
    """Watches a worktree for advice requests while a task executes.

    Runs as a background asyncio task alongside harness execution. Polls
    ``working_dir/.odin/advice_request.md`` for new content; each new
    request triggers one call to ``consult_fn`` (a single strong-model
    call), up to ``max_consults`` times per run. Once the cap is reached,
    further requests get a written refusal instead of a model call.
    """

    def __init__(
        self,
        working_dir: str,
        consult_fn: ConsultFn,
        max_consults: int = DEFAULT_MAX_CONSULTS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        task_title: str = "",
    ):
        self.working_dir = Path(working_dir)
        self._consult_fn = consult_fn
        self.max_consults = max_consults
        self.poll_interval = poll_interval
        self.task_title = task_title
        self.consults: List[ConsultResult] = []
        self.refused_count = 0
        self._stop = asyncio.Event()
        self._last_seen_request: Optional[str] = None

    @property
    def request_path(self) -> Path:
        return self.working_dir / REQUEST_RELPATH

    @property
    def answer_path(self) -> Path:
        return self.working_dir / ANSWER_RELPATH

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll until :meth:`stop` is called. Safe as an asyncio background task."""
        while not self._stop.is_set():
            try:
                await self._check_once()
            except Exception:
                logger.warning("advisor watcher check failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self) -> None:
        if not self.request_path.exists():
            return
        question = self.request_path.read_text().strip()
        if not question or question == self._last_seen_request:
            return
        self._last_seen_request = question
        await self._handle_request(question)

    async def _handle_request(self, question: str) -> None:
        if len(self.consults) >= self.max_consults:
            self.refused_count += 1
            self._write_answer(CAP_REFUSAL_TEMPLATE.format(cap=self.max_consults))
            logger.info(
                "advisor consult refused (cap=%s) for %s", self.max_consults, self.working_dir,
            )
            return
        result = await self._consult_fn(question, self.task_title)
        self.consults.append(result)
        self._write_answer(result.answer)

    def _write_answer(self, answer: str) -> None:
        self.answer_path.parent.mkdir(parents=True, exist_ok=True)
        self.answer_path.write_text(answer.strip() + "\n")

    @property
    def total_token_usage(self) -> dict:
        """Sum token usage across all answered consults (not refusals)."""
        totals: dict = {}
        for c in self.consults:
            for k, v in (c.token_usage or {}).items():
                if isinstance(v, (int, float)):
                    totals[k] = totals.get(k, 0) + v
        return totals


async def consult_advisor(
    question: str,
    task_title: str = "",
    *,
    agent_cfg,
    agent: str = DEFAULT_ADVISOR_AGENT,
    model: str = DEFAULT_ADVISOR_MODEL,
    working_dir: Optional[str] = None,
) -> ConsultResult:
    """Make ONE strong-model call to answer a stuck executor's question.

    Reuses the harness plumbing the same way reflection.py's reviewer call
    does: a plain one-shot prompt, no MCP, no proof envelope, just an
    answer written back for the executor to read.
    """
    from odin.harnesses import get_harness

    prompt = build_advisor_prompt(question, task_title)
    harness = get_harness(agent, agent_cfg)
    context = {
        "working_dir": working_dir,
        "model": model,
        "validate_status": False,
        "read_only_workspace": True,
    }
    start = time.time()
    result = await harness.execute(prompt, context)
    duration_ms = (time.time() - start) * 1000
    usage = (result.metadata or {}).get("usage", {}) if result else {}
    answer = (result.output or "").strip() if result else ""
    if not answer:
        answer = "Advisor produced no answer — proceed with your best judgment."
    return ConsultResult(question=question, answer=answer, token_usage=usage, duration_ms=duration_ms)


def record_advisor_cost(
    cost_tracker,
    *,
    task_id: str,
    spec_id: Optional[str],
    consults: List[ConsultResult],
    model: str,
    agent: str = "advisor",
) -> list:
    """Record each consult's tokens against the task's own ``task_id``.

    ``CostStore.summarize_task()`` sums every record sharing a ``task_id``
    (it already does this for retries), so recording consults under the
    SAME id as the main task execution folds advisor tokens into the
    task's total automatically — the league table and quotes see the real
    cost of the trial without any bespoke aggregation.
    """
    from odin.models import TaskResult

    records = []
    for c in consults:
        fake_result = TaskResult(
            success=True,
            output=c.answer,
            duration_ms=c.duration_ms,
            agent=agent,
            metadata={"usage": c.token_usage},
        )
        records.append(
            cost_tracker.record_task(
                task_id=task_id, spec_id=spec_id, result=fake_result, model=model,
            )
        )
    return records
