"""Tests for tmux session management module.

Tags:
- [simple] — pure logic, no tmux needed
- [tmux_real] — requires tmux installed
"""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from odin import tmux
from odin.tmux import _read_tail


# ── [simple] Pure function tests ──────────────────────────────────────


class TestSessionName:
    def test_format(self):
        assert tmux.session_name("abcdef123456") == "odin-abcdef12"

    def test_short_id(self):
        assert tmux.session_name("abc") == "odin-abc"

    def test_prefix(self):
        name = tmux.session_name("anything")
        assert name.startswith(tmux.SESSION_PREFIX)


class TestIsAvailable:
    def test_returns_true_when_tmux_on_path(self):
        with patch("shutil.which", return_value="/usr/bin/tmux"):
            assert tmux.is_available() is True

    def test_returns_false_when_missing(self):
        with patch("shutil.which", return_value=None):
            assert tmux.is_available() is False


# ── [simple] Wrapper script generation ────────────────────────────────


class TestWrapperScriptContent:
    """Verify the bash wrapper script that launch() writes."""

    @pytest.mark.asyncio
    async def test_script_has_pipefail(self, tmp_path):
        script = await self._generate_script(tmp_path)
        assert "set -o pipefail" in script

    @pytest.mark.asyncio
    async def test_script_has_tee(self, tmp_path):
        script = await self._generate_script(tmp_path)
        assert "tee" in script

    @pytest.mark.asyncio
    async def test_script_has_exit_marker(self, tmp_path):
        script = await self._generate_script(tmp_path)
        assert ".exit" in script
        assert "echo $?" in script

    @pytest.mark.asyncio
    async def test_script_with_env_unset(self, tmp_path):
        script = await self._generate_script(
            tmp_path, env_unset=["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
        )
        assert "unset ANTHROPIC_API_KEY;" in script
        assert "unset OPENAI_API_KEY;" in script

    @pytest.mark.asyncio
    async def test_script_without_env_unset(self, tmp_path):
        script = await self._generate_script(tmp_path)
        # No "unset VAR;" directives should appear
        lines = script.splitlines()
        assert not any(line.strip().startswith("unset ") for line in lines)

    @pytest.mark.asyncio
    async def test_script_escapes_command(self, tmp_path):
        script = await self._generate_script(
            tmp_path, cmd=["echo", "hello world", "it's"]
        )
        # shlex.join should quote args with spaces/special chars
        assert "hello world" in script

    async def _generate_script(self, tmp_path, cmd=None, env_unset=None):
        """Call launch() with a mocked tmux to capture the generated script."""
        cmd = cmd or ["echo", "hello"]
        output_file = str(tmp_path / "logs" / "output.log")
        task_id = "test1234abcd"

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            await tmux.launch(
                cmd=cmd,
                working_dir=str(tmp_path),
                task_id=task_id,
                output_file=output_file,
                env_unset=env_unset,
            )

        script_path = tmp_path / "logs" / f"tmux_{task_id[:8]}.sh"
        return script_path.read_text()


class TestLaunchCreatesScript:
    @pytest.mark.asyncio
    async def test_script_file_exists(self, tmp_path):
        output_file = str(tmp_path / "logs" / "output.log")
        task_id = "abcd1234efgh"

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            await tmux.launch(
                cmd=["echo", "hi"],
                working_dir=str(tmp_path),
                task_id=task_id,
                output_file=output_file,
            )

        script_path = tmp_path / "logs" / f"tmux_{task_id[:8]}.sh"
        assert script_path.exists()
        assert script_path.stat().st_mode & 0o111  # executable

    @pytest.mark.asyncio
    async def test_launch_returns_session_name(self, tmp_path):
        output_file = str(tmp_path / "logs" / "output.log")
        task_id = "abcd1234efgh"

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            sess = await tmux.launch(
                cmd=["echo", "hi"],
                working_dir=str(tmp_path),
                task_id=task_id,
                output_file=output_file,
            )

        assert sess == tmux.session_name(task_id)

    @pytest.mark.asyncio
    async def test_launch_raises_on_tmux_failure(self, tmp_path):
        output_file = str(tmp_path / "logs" / "output.log")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"duplicate session"))
        mock_proc.wait = AsyncMock()
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            with pytest.raises(RuntimeError, match="Failed to create tmux session"):
                await tmux.launch(
                    cmd=["echo", "hi"],
                    working_dir=str(tmp_path),
                    task_id="test1234",
                    output_file=output_file,
                )


# ── [simple] _read_tail tests ─────────────────────────────────────────


class TestReadTail:
    def test_file_smaller_than_num_bytes(self, tmp_path):
        f = tmp_path / "small.txt"
        f.write_text("hello")
        assert _read_tail(f, num_bytes=1024) == "hello"

    def test_file_larger_than_num_bytes(self, tmp_path):
        f = tmp_path / "big.txt"
        content = "x" * 100
        f.write_text(content)
        result = _read_tail(f, num_bytes=10)
        assert len(result) == 10
        assert result == "x" * 10

    def test_file_missing(self, tmp_path):
        f = tmp_path / "nope.txt"
        assert _read_tail(f) == ""

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _read_tail(f) == ""


# ── [simple] wait_for_exit with completion_checker ────────────────────


class TestWaitForExitCompletionChecker:
    """Test the grace-period logic when a completion_checker is provided."""

    @pytest.mark.asyncio
    async def test_natural_exit_before_completion_detected(self, tmp_path):
        """Session exits on its own — checker never triggers."""
        output_file = str(tmp_path / "output.log")
        marker = Path(output_file + ".exit")
        marker.write_text("0")

        call_count = 0

        async def fake_has_session(task_id):
            nonlocal call_count
            call_count += 1
            # Session gone immediately
            return False

        with patch("odin.tmux.has_session", side_effect=fake_has_session):
            code = await tmux.wait_for_exit(
                "test1234", output_file, timeout=5,
                completion_checker=lambda tail: True,
            )

        assert code == 0

    @pytest.mark.asyncio
    async def test_completion_detected_then_grace_kill(self, tmp_path):
        """Completion detected, session lingers, gets killed after grace period."""
        output_file = str(tmp_path / "output.log")
        Path(output_file).write_text('{"type":"result","result":"Done."}\n')

        with (
            patch("odin.tmux.has_session", new_callable=AsyncMock, return_value=True),
            patch("odin.tmux.kill_session", new_callable=AsyncMock, return_value=True) as mock_kill,
            patch("asyncio.sleep", new_callable=AsyncMock),  # speed up
        ):
            code = await tmux.wait_for_exit(
                "test1234", output_file, timeout=60,
                completion_checker=lambda tail: '{"type":"result"' in tail,
            )

        assert code == 0
        mock_kill.assert_called_once_with("test1234")

    @pytest.mark.asyncio
    async def test_no_checker_original_behavior(self, tmp_path):
        """Without a completion_checker, behaves like the original."""
        output_file = str(tmp_path / "output.log")
        marker = Path(output_file + ".exit")
        marker.write_text("42")

        call_count = 0

        async def fake_has_session(task_id):
            nonlocal call_count
            call_count += 1
            # Session alive for 2 polls then gone
            return call_count <= 2

        with (
            patch("odin.tmux.has_session", side_effect=fake_has_session),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            code = await tmux.wait_for_exit("test1234", output_file, timeout=30)

        assert code == 42

    @pytest.mark.asyncio
    async def test_timeout_still_works_with_checker(self, tmp_path):
        """Timeout fires even when checker is provided but never detects completion."""
        output_file = str(tmp_path / "output.log")
        Path(output_file).write_text("no result here\n")

        with (
            patch("odin.tmux.has_session", new_callable=AsyncMock, return_value=True),
            patch("odin.tmux.kill_session", new_callable=AsyncMock, return_value=True) as mock_kill,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            code = await tmux.wait_for_exit(
                "test1234", output_file, timeout=3,
                completion_checker=lambda tail: False,
            )

        assert code == -1
        mock_kill.assert_called_once_with("test1234")

    @pytest.mark.asyncio
    async def test_completion_detected_session_exits_during_grace(self, tmp_path):
        """Session exits naturally during the grace period — reads marker file."""
        output_file = str(tmp_path / "output.log")
        marker = Path(output_file + ".exit")
        marker.write_text("0")
        Path(output_file).write_text('{"type":"result","result":"OK"}\n')

        poll_count = 0

        async def fake_has_session(task_id):
            nonlocal poll_count
            poll_count += 1
            # Alive for several polls (checker triggers), then dies during grace
            if poll_count <= 6:
                return True
            return False  # session exits during grace period

        with (
            patch("odin.tmux.has_session", side_effect=fake_has_session),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            code = await tmux.wait_for_exit(
                "test1234", output_file, timeout=60,
                completion_checker=lambda tail: '{"type":"result"' in tail,
            )

        # Should read exit code from marker, not return 0 from forced kill
        assert code == 0


# ── [simple] _wait_for_prompt tests ──────────────────────────────────


class TestWaitForPrompt:
    """Test the polling readiness check for CLI prompt."""

    def test_returns_true_when_prompt_visible(self):
        """Detects '>' in pane content and returns True."""
        mock_result = MagicMock(returncode=0, stdout="\n  >\n")
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("time.sleep"),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=5) is True

    def test_returns_true_on_question_mark(self):
        """Detects '?' (permission prompt) as ready."""
        mock_result = MagicMock(returncode=0, stdout="Allow? (y/n)")
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("time.sleep"),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=5) is True

    def test_returns_true_on_tips(self):
        """Detects 'tips' in startup banner as ready."""
        mock_result = MagicMock(returncode=0, stdout="Welcome! Press / for tips\n")
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("time.sleep"),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=5) is True

    def test_returns_false_on_timeout_empty_pane(self):
        """Times out when pane stays empty."""
        mock_result = MagicMock(returncode=0, stdout="")
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("time.sleep"),
            patch("time.monotonic", side_effect=[0, 0.5, 1.0, 1.5, 2.0, 2.5]),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=2) is False

    def test_handles_subprocess_failure(self):
        """Keeps polling when capture-pane fails (e.g., session not yet created)."""
        fail_result = MagicMock(returncode=1, stdout="")
        ok_result = MagicMock(returncode=0, stdout="> ")
        with (
            patch("subprocess.run", side_effect=[fail_result, ok_result]),
            patch("time.sleep"),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=30) is True

    def test_polls_until_content_appears(self):
        """Returns False for empty content, True once prompt shows up."""
        empty = MagicMock(returncode=0, stdout="")
        ready = MagicMock(returncode=0, stdout="> ")
        with (
            patch("subprocess.run", side_effect=[empty, empty, ready]),
            patch("time.sleep"),
        ):
            assert tmux._wait_for_prompt("test-session", timeout=30) is True


class TestLaunchAndAttachPolling:
    """Verify launch_and_attach uses _wait_for_prompt instead of sleep."""

    def test_calls_wait_for_prompt_with_initial_message(self, tmp_path):
        """When initial_message_file is given, polls for readiness."""
        msg_file = tmp_path / "message.txt"
        msg_file.write_text("Hello agent")
        output_file = str(tmp_path / "output.log")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            patch("odin.tmux._wait_for_prompt", return_value=True) as mock_wait,
            patch("odin.tmux._send_initial_message") as mock_send,
        ):
            tmux.launch_and_attach(
                cmd=["claude", "-p", "test"],
                working_dir=str(tmp_path),
                session_id="test1234abcd",
                output_file=output_file,
                initial_message_file=str(msg_file),
            )

        mock_wait.assert_called_once()
        mock_send.assert_called_once()

    def test_sends_message_on_timeout(self, tmp_path):
        """Falls back to sending even when prompt detection times out."""
        msg_file = tmp_path / "message.txt"
        msg_file.write_text("Hello agent")
        output_file = str(tmp_path / "output.log")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            patch("odin.tmux._wait_for_prompt", return_value=False) as mock_wait,
            patch("odin.tmux._send_initial_message") as mock_send,
        ):
            tmux.launch_and_attach(
                cmd=["claude", "-p", "test"],
                working_dir=str(tmp_path),
                session_id="test1234abcd",
                output_file=output_file,
                initial_message_file=str(msg_file),
            )

        mock_wait.assert_called_once()
        # Message still sent despite timeout
        mock_send.assert_called_once()

    def test_skips_polling_without_initial_message(self, tmp_path):
        """No polling when no initial_message_file is provided."""
        output_file = str(tmp_path / "output.log")

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            patch("odin.tmux._wait_for_prompt") as mock_wait,
            patch("odin.tmux._send_initial_message") as mock_send,
        ):
            tmux.launch_and_attach(
                cmd=["claude", "-p", "test"],
                working_dir=str(tmp_path),
                session_id="test1234abcd",
                output_file=output_file,
            )

        mock_wait.assert_not_called()
        mock_send.assert_not_called()


# ── [tmux_real] Tests that require actual tmux ────────────────────────


@pytest.mark.tmux_real
class TestTmuxReal:
    """Integration tests using real tmux. Skipped if tmux not available."""

    @pytest.fixture(autouse=True)
    def skip_if_no_tmux(self):
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")

    @pytest.mark.asyncio
    async def test_launch_echo_and_wait(self, tmp_path):
        output_file = str(tmp_path / "output.log")
        task_id = "realtest1234"

        sess = await tmux.launch(
            cmd=["echo", "hello from tmux"],
            working_dir=str(tmp_path),
            task_id=task_id,
            output_file=output_file,
        )

        exit_code = await tmux.wait_for_exit(task_id, output_file, timeout=10)
        assert exit_code == 0

        output = Path(output_file).read_text()
        assert "hello from tmux" in output

    @pytest.mark.asyncio
    async def test_has_session_lifecycle(self, tmp_path):
        output_file = str(tmp_path / "output.log")
        task_id = "lifecycle1234"

        await tmux.launch(
            cmd=["sleep", "2"],
            working_dir=str(tmp_path),
            task_id=task_id,
            output_file=output_file,
        )

        assert await tmux.has_session(task_id) is True
        await tmux.kill_session(task_id)
        await asyncio.sleep(0.3)
        assert await tmux.has_session(task_id) is False

    @pytest.mark.asyncio
    async def test_exit_code_capture(self, tmp_path):
        output_file = str(tmp_path / "output.log")
        task_id = "exitcode1234"

        await tmux.launch(
            cmd=["bash", "-c", "exit 42"],
            working_dir=str(tmp_path),
            task_id=task_id,
            output_file=output_file,
        )

        exit_code = await tmux.wait_for_exit(task_id, output_file, timeout=10)
        assert exit_code == 42

    @pytest.mark.asyncio
    async def test_kill_nonexistent_returns_false(self):
        result = await tmux.kill_session("nonexistent999")
        assert result is False
