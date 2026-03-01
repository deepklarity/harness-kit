"""Tests for screenshot upload endpoint (POST /tasks/{id}/screenshots/)."""

import os
import shutil
import tempfile

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from tests.base import APITestCase
from tasks.models import CommentAttachment, TaskComment


# Create a temp media root for tests so uploads don't pollute real media/
_TEMP_MEDIA = tempfile.mkdtemp(prefix="test_media_")


@override_settings(FIREBASE_AUTH_ENABLED=False, MEDIA_ROOT=_TEMP_MEDIA)
class TestScreenshotUpload(APITestCase):
    """Upload endpoint: POST /tasks/{id}/screenshots/"""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)

    def _url(self, task_id=None):
        return f"/tasks/{task_id or self.task.id}/screenshots/"

    def _png(self, name="screenshot.png", size=128):
        """Create a minimal fake PNG file."""
        # PNG header (8 bytes) + some padding
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * size
        return SimpleUploadedFile(name, content, content_type="image/png")

    # ── Happy path ──────────────────────────────────────────────

    def test_upload_single_png(self):
        resp = self.client.post(self._url(), {"files": self._png()}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(len(data), 1)
        att = data[0]
        self.assertEqual(att["original_filename"], "screenshot.png")
        self.assertEqual(att["content_type"], "image/png")
        self.assertIn("url", att)
        self.assertIn("/media/screenshots/", att["url"])

    def test_upload_multiple_files(self):
        files = [self._png(f"shot_{i}.png") for i in range(3)]
        resp = self.client.post(self._url(), {"files": files}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(resp.json()), 3)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 3)

    def test_existing_text_proof_unchanged(self):
        """Uploading screenshots does not affect existing text-only proof comments."""
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Proof: completed",
            comment_type="proof",
            attachments=[{"type": "proof", "summary": "done"}],
        )
        resp = self.client.post(self._url(), {"files": self._png()}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        comment.refresh_from_db()
        self.assertEqual(comment.content, "Proof: completed")
        self.assertEqual(comment.attachments, [{"type": "proof", "summary": "done"}])

    def test_upload_sets_author_email(self):
        resp = self.client.post(
            self._url(),
            {"files": self._png(), "author_email": "claude+sonnet@odin.agent"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, 201)
        att = CommentAttachment.objects.get(task=self.task)
        self.assertEqual(att.uploaded_by, "claude+sonnet@odin.agent")

    def test_upload_default_author_email(self):
        resp = self.client.post(self._url(), {"files": self._png()}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        att = CommentAttachment.objects.get(task=self.task)
        self.assertEqual(att.uploaded_by, "agent@odin.agent")

    # ── Edge cases ──────────────────────────────────────────────

    def test_upload_no_files_returns_400(self):
        resp = self.client.post(self._url(), {}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No files", resp.json()["detail"])

    def test_upload_file_too_large_returns_400(self):
        big = SimpleUploadedFile(
            "huge.png",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1),
            content_type="image/png",
        )
        resp = self.client.post(self._url(), {"files": big}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("exceeds 10 MB", resp.json()["detail"])

    def test_upload_sanitized_filename(self):
        """Path traversal in filename should be sanitized by Django's storage."""
        evil = self._png("../../etc/passwd.png")
        resp = self.client.post(self._url(), {"files": evil}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        att = CommentAttachment.objects.get(task=self.task)
        # Django storage sanitizes: no path traversal in stored filename
        self.assertNotIn("..", att.file.name)
        # SimpleUploadedFile also sanitizes .name, so original_filename gets sanitized too
        self.assertNotIn("..", att.original_filename)

    def test_upload_nonexistent_task_returns_404(self):
        resp = self.client.post(self._url(task_id=99999), {"files": self._png()}, format="multipart")
        self.assertEqual(resp.status_code, 404)

    # ── Integration: roundtrip ──────────────────────────────────

    def test_roundtrip_upload_verifies_stored_file(self):
        """Upload a file, then verify the stored file exists and content matches."""
        content = b"\x89PNG\r\n\x1a\n" + b"test-image-content"
        f = SimpleUploadedFile("round.png", content, content_type="image/png")
        resp = self.client.post(self._url(), {"files": f}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        att = CommentAttachment.objects.get(task=self.task)
        # File was stored and content is readable
        att.file.open("rb")
        stored = att.file.read()
        att.file.close()
        self.assertEqual(stored, content)
        self.assertIn("/media/screenshots/", resp.json()[0]["url"])

    def test_upload_then_link_to_comment(self):
        """Upload screenshots, then submit proof with attachment_ids — verify linked."""
        resp = self.client.post(self._url(), {"files": self._png()}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        att_id = resp.json()[0]["id"]

        # Create a proof comment
        comment = TaskComment.objects.create(
            task=self.task,
            author_email="agent@odin.agent",
            content="Proof: task done",
            comment_type="proof",
        )
        # Link the attachment to the comment
        att = CommentAttachment.objects.get(id=att_id)
        att.comment = comment
        att.save()

        # Verify via comment serializer (through task detail)
        detail_resp = self.client.get(f"/tasks/{self.task.id}/detail/")
        self.assertEqual(detail_resp.status_code, 200)
        comments = detail_resp.json()["comments"]
        proof_comments = [c for c in comments if c["comment_type"] == "proof"]
        self.assertEqual(len(proof_comments), 1)
        self.assertEqual(len(proof_comments[0]["file_attachments"]), 1)
        self.assertEqual(proof_comments[0]["file_attachments"][0]["original_filename"], "screenshot.png")

    # ── Cleanup: task deletion cascades ─────────────────────────

    def test_task_deletion_cascades_attachments(self):
        self.client.post(self._url(), {"files": self._png()}, format="multipart")
        self.assertEqual(CommentAttachment.objects.count(), 1)
        self.task.delete()
        self.assertEqual(CommentAttachment.objects.count(), 0)
