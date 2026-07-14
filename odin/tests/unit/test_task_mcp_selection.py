"""Task 183 — MCP-section selection heuristics must not false-positive.

The executor wraps every non-skip_proof task's prompt with a browser
(~1.5 KB) or mobile (~5.1 KB) proof-instruction section, gated by
``_task_text_needs_browser`` / ``_task_text_needs_mobile``. The original
matcher used substring ``in`` on generic tokens, so ``"ui" in "build"`` and
``"form" in "platform"`` fired the browser section on nearly every backend/CLI
task. These tests pin word-boundary matching + a curated marker set: genuine
web/mobile tasks still match; incidental English/DB words do not.
"""

from odin.orchestrator import Orchestrator


class TestBrowserHeuristicNoSubstringFalsePositives:
    def test_build_does_not_trigger_browser(self):
        # "ui" is a substring of "build" — the classic false positive.
        assert Orchestrator._task_text_needs_browser("run the build and commit") is False

    def test_platform_does_not_trigger_browser(self):
        # "form" is a substring of "platform".
        assert Orchestrator._task_text_needs_browser("host-side platform verification") is False

    def test_generic_words_do_not_trigger_browser(self):
        for text in ("require a monotonic clock", "add guidance to the docs",
                     "refactor the pricing registry module"):
            assert Orchestrator._task_text_needs_browser(text) is False, text

    def test_bare_generic_ui_table_form_dropped(self):
        # Standalone generic tokens that appear in backend/DB/doc tasks must
        # NOT pull in the browser section on their own.
        for text in ("update the task table schema", "validate the form payload",
                     "add a button to the API response"):
            assert Orchestrator._task_text_needs_browser(text) is False, text


class TestBrowserHeuristicGenuinePositives:
    def test_genuine_browser_terms_still_match(self):
        for text in ("take a screenshot in the browser",
                     "fix the CSS in the frontend",
                     "the chrome devtools panel renders blank",
                     "render the HTML page and inspect the DOM"):
            assert Orchestrator._task_text_needs_browser(text) is True, text


class TestMobileHeuristic:
    def test_generic_device_word_dropped(self):
        # "device" appears in VM/hardware contexts (e.g. task 176 VM memory).
        assert Orchestrator._task_text_needs_mobile("measure VM device memory") is False

    def test_ios_substring_in_scenarios_not_matched(self):
        assert Orchestrator._task_text_needs_mobile("cover the failure scenarios") is False

    def test_genuine_mobile_terms_still_match(self):
        for text in ("build the android app in expo",
                     "screenshot the iphone simulator",
                     "the react native emulator crashes"):
            assert Orchestrator._task_text_needs_mobile(text) is True, text


class TestSelectTaskMcpsBackendTask:
    def test_pure_backend_brief_selects_taskit_only(self):
        from odin.models import OdinConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = OdinConfig(mcps=["taskit", "mobile", "chrome-devtools"])
        # A representative backend brief that mentions "build" and "table".
        brief = ("Auto-commit must respect gitignore; run the build and add a "
                 "regression test over the task table.")
        assert orch._select_task_mcps(brief) == ["taskit"]

    def test_genuine_browser_brief_selects_chrome(self):
        from odin.models import OdinConfig

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = OdinConfig(mcps=["taskit", "mobile", "chrome-devtools"])
        brief = "Add a TraceViewer live mode; the browser shows the stream."
        assert "chrome-devtools" in orch._select_task_mcps(brief)
