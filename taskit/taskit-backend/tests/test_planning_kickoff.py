"""The planning-terminal kickoff must be paste-then-Enter, two PTY writes.

Regression: the kickoff message and its trailing \r were sent as a single
pty.write(). Claude Code's bracketed-paste detection absorbed the \r as
literal text, so the message sat unsubmitted in the input box and planning
never started (0 tokens, session idle until a human pressed Enter).
"""

from unittest import mock

from django.test import SimpleTestCase

from tasks.consumers import _KICKOFF_MESSAGE, _send_kickoff


class SendKickoffTests(SimpleTestCase):
    def test_message_and_enter_are_separate_writes(self):
        pty = mock.Mock()
        _send_kickoff(pty, settle_seconds=0)

        self.assertEqual(pty.write.call_count, 2)
        first, second = pty.write.call_args_list
        self.assertEqual(first.args[0], _KICKOFF_MESSAGE.encode())
        self.assertEqual(second.args[0], b'\r')

    def test_message_itself_carries_no_submit_character(self):
        # \n inside the message is fine (multi-line paste); a \r would be
        # swallowed by paste detection and must only ever travel alone.
        self.assertNotIn('\r', _KICKOFF_MESSAGE)
