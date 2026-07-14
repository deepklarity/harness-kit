"""Tests for the board Claude Code token endpoint (writes <working_dir>/.claude-token)."""

import os
import stat
import tempfile
from pathlib import Path

from rest_framework.test import APITestCase

from tasks.models import Board
from tasks.serializers import BoardSerializer


class ClaudeTokenEndpointTests(APITestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.board = Board.objects.create(name="TokenBoard", working_dir=self.tmp)

    def _url(self, board=None):
        return f"/api/boards/{(board or self.board).id}/claude-token/"

    def test_set_token_writes_0600_file_and_reports_configured(self):
        resp = self.client.post(self._url(), {"token": "sk-ant-oat-xyz"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        tf = Path(self.tmp) / ".claude-token"
        self.assertTrue(tf.is_file())
        self.assertEqual(tf.read_text().strip(), "sk-ant-oat-xyz")
        self.assertEqual(stat.S_IMODE(os.stat(tf).st_mode), 0o600)
        self.assertTrue(resp.data["claude_token_configured"])

    def test_configured_flag_false_when_no_file(self):
        self.assertFalse(BoardSerializer(self.board).data["claude_token_configured"])

    def test_blank_token_rejected(self):
        resp = self.client.post(self._url(), {"token": ""}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_board_without_working_dir_rejected(self):
        board = Board.objects.create(name="NoDir")  # working_dir null
        resp = self.client.post(self._url(board), {"token": "x"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_token_value_never_returned(self):
        secret = "super-secret-token-value"
        self.client.post(self._url(), {"token": secret}, format="json")
        resp = self.client.get(f"/api/boards/{self.board.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(secret, str(resp.data))  # only the boolean flag is exposed
        self.assertTrue(resp.data["claude_token_configured"])
