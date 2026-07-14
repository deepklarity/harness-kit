"""Unit tests pinning the 'every claim needs raw output' rule in
docs/task_brief_template.md.

The template is read by `odin plan` (orchestrator._build_plan_prompt) and
followed by the operator when writing briefs by hand. The "Prove it"
section used to read like documentation; it now demands pasted command
output for every claim so reflection reviewers and humans can falsify
the work, not just admire it.

These tests load the canonical template and assert the new rule is
present. They do NOT regenerate the template from code — the template
is the source of truth, not generated output, and the rule is meant
to travel with the human-written content.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "docs" / "task_brief_template.md"


def _load_template() -> str:
    assert TEMPLATE_PATH.exists(), (
        f"task brief template missing at {TEMPLATE_PATH}"
    )
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template_text() -> str:
    return _load_template()


def _prove_it_block(text: str) -> str:
    """Extract the '## Prove it' section content.

    Stops at the next H2 header so we don't pull in the standing-rules
    block (which is identical in every brief and lives further down).
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == "## prove it":
            start = i + 1
            break
    assert start is not None, "task brief template missing '## Prove it' section"
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## "):
            end = j
            break
    return "\n".join(lines[start:end])


class TestProveItRequiresRawOutput:
    """The 'Prove it' section must demand raw output for every claim.

    Two prior failures drove this rule: a worker claimed a UI screenshot
    that did not exist, and a worker claimed it had updated pages it
    never rendered. Reflection caught both, but only after burning an
    attempt. The cheaper fix is upstream: every claim in proof.md gets
    the raw output that proves it.
    """

    def test_template_exists(self, template_text):
        assert len(template_text) > 200

    def test_prove_it_section_exists(self, template_text):
        block = _prove_it_block(template_text)
        assert block.strip(), "'## Prove it' section must not be empty"

    def test_prove_it_requires_raw_output_for_every_claim(self, template_text):
        block = _prove_it_block(template_text).lower()
        # The rule has to be in plain English (this is the human-facing
        # template) — pin the substance, not exact wording.
        assert "raw output" in block or "pasted" in block or "paste" in block, (
            "Prove it section must demand raw/pasted output, not prose claims; "
            f"got: {block[:500]!r}"
        )

    def test_prove_it_names_tests_pass_example(self, template_text):
        block = _prove_it_block(template_text).lower()
        # The rule must show what "tests pass" looks like when proved:
        # the pytest tail, not a sentence.
        assert "tests pass" in block or "test passes" in block, (
            "Prove it section must call out the 'tests pass' claim as one "
            "that needs the actual pytest output behind it"
        )

    def test_prove_it_names_render_or_screenshot_example(self, template_text):
        block = _prove_it_block(template_text).lower()
        # The second canonical example: rendering a page needs the log
        # or screenshot, not "I rendered it".
        has_render = "render" in block
        has_screenshot = "screenshot" in block
        assert has_render or has_screenshot, (
            "Prove it section must call out the 'page renders' or "
            "screenshot example as needing real visual evidence"
        )

    def test_prove_it_does_not_accept_prose_alone(self, template_text):
        block = _prove_it_block(template_text).lower()
        # The rule explicitly rejects prose-only claims.
        assert any(
            phrase in block
            for phrase in (
                "not proof",
                "alone is not",
                "prose alone",
                "prose is not",
                "is not proof",
            )
        ), (
            "Prove it section must say prose claims alone are not proof; "
            f"got: {block[:500]!r}"
        )

    def test_prove_it_stays_proportional(self, template_text):
        """The rule must NOT become a wall-of-logs tax on small tasks.

        The operator directive is explicit: a one-line docs task should
        not need a wall of logs. The rule is "output for claims, not
        volume" — every claim still needs raw output, but the volume
        tracks the claims, not the worker.
        """
        block = _prove_it_block(template_text).lower()
        assert any(
            phrase in block
            for phrase in (
                "proportional",
                "not volume",
                "output for claims",
                "wall of logs",
                "one-line",
                "small task",
            )
        ), (
            "Prove it section must explicitly stay proportional — "
            "small tasks don't need a wall of logs; "
            f"got: {block[:500]!r}"
        )

    def test_prove_it_mentions_proof_md(self, template_text):
        """The rule names the canonical proof artifact so the worker
        knows where the raw output lands. This couples the brief to
        the per-task `.proof/task-<id>/proof.md` convention enforced
        by `odin/src/odin/reflection.py`."""
        block = _prove_it_block(template_text).lower()
        assert "proof" in block, (
            "Prove it section must reference the proof file convention "
            "so workers know where to paste the raw output"
        )

    def test_prove_it_keeps_existing_done_means_intact(self, template_text):
        """Regression: the rule is added to 'Prove it', not in place
        of the existing 'Done means' checks. Both sections must still
        be present so the brief shape stays five-section."""
        lower = template_text.lower()
        assert "## done means" in lower, "'## Done means' must remain in the template"
        assert "## prove it" in lower, "'## Prove it' must remain in the template"
        assert "## standing rules" in lower, "'## Standing rules' must remain in the template"