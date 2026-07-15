"""Tests for spec file-attachment endpoint (POST /specs/{id}/attachments/).

Covers:
- Upload a file as a SpecCommentAttachment (orphan, comment=None)
- Linking an orphan attachment to a SpecComment via attachment_ids
- Spec API response listing file_attachments on comments
- Error cases (no files, oversized)
"""

import os
import shutil
import tempfile

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tests.base import APITestCase
from tasks.models import SpecCommentAttachment


_TEMP_MEDIA = tempfile.mkdtemp(prefix="test_spec_media_")


@override_settings(FIREBASE_AUTH_ENABLED=False, MEDIA_ROOT=_TEMP_MEDIA)
class TestSpecAttachmentUpload(APITestCase):
    """Upload endpoint: POST /specs/{id}/attachments/"""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.spec = self.make_spec(self.board)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)

    def _url(self, spec_id=None):
        return f"/specs/{spec_id or self.spec.id}/attachments/"

    def _html(self, name="preview.html"):
        content = b"<html><body><h1>Plan Preview</h1></body></html>"
        return SimpleUploadedFile(name, content, content_type="text/html")

    # ── Happy path ──────────────────────────────────────────────

    def test_upload_single_html(self):
        resp = self.client.post(self._url(), {"files": self._html()}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(len(data), 1)
        att = data[0]
        self.assertIn("id", att)
        self.assertEqual(att["original_filename"], "preview.html")
        self.assertEqual(att["content_type"], "text/html")
        self.assertIn("url", att)
        self.assertTrue(SpecCommentAttachment.objects.filter(spec=self.spec).exists())

    def test_upload_sets_author_email(self):
        resp = self.client.post(
            self._url(),
            {"files": self._html(), "author_email": "planner@odin.agent"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        att = SpecCommentAttachment.objects.get(spec=self.spec)
        self.assertEqual(att.uploaded_by, "planner@odin.agent")
        self.assertIsNone(att.comment)

    def test_link_attachment_to_comment(self):
        """Upload then POST a comment with attachment_ids links the file."""
        upload = self.client.post(self._url(), {"files": self._html()}, format="multipart")
        att_id = upload.json()[0]["id"]

        resp = self.client.post(
            f"/specs/{self.spec.id}/comments/",
            {
                "content": "Plan preview attached.",
                "comment_type": "planning",
                "author_email": "planner@odin.agent",
                "attachment_ids": [att_id],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        att = SpecCommentAttachment.objects.get(id=att_id)
        self.assertEqual(att.comment_id, resp.json()["id"])

    def test_spec_api_lists_file_attachments(self):
        """GET /specs/:id/ includes comments with file_attachments."""
        upload = self.client.post(self._url(), {"files": self._html()}, format="multipart")
        att_id = upload.json()[0]["id"]
        self.client.post(
            f"/specs/{self.spec.id}/comments/",
            {
                "content": "Preview attached.",
                "comment_type": "planning",
                "author_email": "planner@odin.agent",
                "attachment_ids": [att_id],
            },
            format="json",
        )

        resp = self.client.get(f"/specs/{self.spec.id}/")
        self.assertEqual(resp.status_code, 200)
        comments = resp.json()["comments"]
        self.assertEqual(len(comments), 1)
        file_atts = comments[0]["file_attachments"]
        self.assertEqual(len(file_atts), 1)
        self.assertEqual(file_atts[0]["id"], att_id)
        self.assertEqual(file_atts[0]["original_filename"], "preview.html")

    # ── Error cases ─────────────────────────────────────────────

    def test_no_files_returns_400(self):
        resp = self.client.post(self._url(), {}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    def test_oversized_file_returns_400(self):
        big = SimpleUploadedFile("huge.html", b"x" * (11 * 1024 * 1024), content_type="text/html")
        resp = self.client.post(self._url(), {"files": big}, format="multipart")
        self.assertEqual(resp.status_code, 400)
