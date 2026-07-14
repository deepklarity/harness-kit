"""Tests for proof-file upload on reflection pass (attach_task_proof_files).

After a task's reflection passes and its merge lands, the .proof/task-<id>/
files written in the worktree are uploaded as CommentAttachment rows linked
to a single PROOF comment — so evidence lives on the task board, not in git.
"""

import os
import shutil
import tempfile

os.environ.setdefault("USE_SQLITE", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FIREBASE_AUTH_ENABLED", "False")

from django.test import override_settings

from tests.base import APITestCase
from tasks.models import CommentAttachment, CommentType, TaskComment
from tasks.proof_upload import attach_task_proof_files, MAX_PROOF_FILE_SIZE

_TEMP_MEDIA = tempfile.mkdtemp(prefix="test_media_proof_")


@override_settings(FIREBASE_AUTH_ENABLED=False, MEDIA_ROOT=_TEMP_MEDIA)
class TestAttachTaskProofFiles(APITestCase):
    """attach_task_proof_files — reads .proof/task-<id>/ from the worktree."""

    def setUp(self):
        super().setUp()
        self.board = self.make_board()
        self.task = self.make_task(self.board)
        self.worktree = tempfile.mkdtemp(prefix="test_wt_")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_TEMP_MEDIA, ignore_errors=True)

    def _set_worktree(self):
        self.task.metadata = {"worktree_path": self.worktree}
        self.task.save(update_fields=["metadata"])

    def _write_proof(self, name, content):
        proof_dir = os.path.join(self.worktree, ".proof", f"task-{self.task.id}")
        os.makedirs(proof_dir, exist_ok=True)
        path = os.path.join(proof_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    # ── Happy path ──────────────────────────────────────────────

    def test_two_files_create_two_attachments_and_one_comment(self):
        self._set_worktree()
        self._write_proof("proof.md", "# Proof\nAll green.")
        self._write_proof("tests.txt", "5 passed")

        result = attach_task_proof_files(self.task)

        self.assertIsNotNone(result)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 2)
        comment = TaskComment.objects.get(
            task=self.task, comment_type=CommentType.PROOF,
            author_email="proof-upload@taskit",
        )
        self.assertEqual(comment.file_attachments.count(), 2)
        names = sorted(a.original_filename for a in comment.file_attachments.all())
        self.assertEqual(names, ["proof.md", "tests.txt"])

    def test_nested_subdir_files_are_included(self):
        self._set_worktree()
        self._write_proof("proof.md", "top-level")
        self._write_proof("raw/full.txt", "nested output")

        attach_task_proof_files(self.task)

        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 2)
        stored = sorted(a.original_filename for a in CommentAttachment.objects.filter(task=self.task))
        self.assertIn("raw/full.txt", stored)

    # ── Graceful no-ops ─────────────────────────────────────────

    def test_no_worktree_path_in_metadata_is_noop(self):
        self.task.metadata = {}
        self.task.save(update_fields=["metadata"])

        result = attach_task_proof_files(self.task)

        self.assertIsNone(result)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 0)
        self.assertEqual(
            TaskComment.objects.filter(
                task=self.task, author_email="proof-upload@taskit",
            ).count(),
            0,
        )

    def test_nonexistent_worktree_path_is_noop(self):
        self.task.metadata = {"worktree_path": "/nonexistent/path/xyz"}
        self.task.save(update_fields=["metadata"])

        result = attach_task_proof_files(self.task)

        self.assertIsNone(result)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 0)

    def test_no_proof_directory_is_noop(self):
        self._set_worktree()
        # worktree exists but has no .proof/ tree

        result = attach_task_proof_files(self.task)

        self.assertIsNone(result)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 0)

    def test_empty_proof_directory_is_noop(self):
        self._set_worktree()
        os.makedirs(os.path.join(self.worktree, ".proof", f"task-{self.task.id}"))

        result = attach_task_proof_files(self.task)

        self.assertIsNone(result)
        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 0)

    # ── Oversized files ─────────────────────────────────────────

    def test_oversized_file_is_truncated_with_marker(self):
        self._set_worktree()
        big_content = "x" * (MAX_PROOF_FILE_SIZE + 1000)
        self._write_proof("big.txt", big_content)

        attach_task_proof_files(self.task)

        att = CommentAttachment.objects.get(task=self.task)
        self.assertLess(att.file_size, len(big_content))
        with att.file.open("rb") as f:
            stored = f.read().decode("utf-8", errors="replace")
        self.assertIn("[TRUNCATED", stored)
        self.assertIn(str(len(big_content)), stored)

    def test_truncated_file_count_is_honest(self):
        """An oversized file still produces exactly one attachment."""
        self._set_worktree()
        self._write_proof("proof.md", "small")
        self._write_proof("big.txt", "y" * (MAX_PROOF_FILE_SIZE + 500))

        attach_task_proof_files(self.task)

        self.assertEqual(CommentAttachment.objects.filter(task=self.task).count(), 2)
