"""Guard against odin version skew in branch worktrees.

When odin is installed editable (``pip install -e``) against the MAIN
checkout, backend tests run from a branch worktree silently import the
main checkout's odin. A one-line change in the worktree's odin/src then
surfaces as phantom TypeErrors (e.g. ``MergeResult(...) got an unexpected
keyword argument``) that take 20 minutes to trace back to version skew.

This guard fails loudly — naming the fix — whenever odin is missing or
resolves outside this tree's odin/src. scripts/verify.sh prepends this
tree's odin/src to PYTHONPATH, so the guard passes under verify.sh and
fails when that pinning is absent or defeated by a foreign install.
"""

from pathlib import Path
from unittest import TestCase

# taskit-backend/ == BASE_DIR (config/settings.py uses the same parent.parent
# idiom to locate sibling src-layouts); repo root is two dirs above it.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent.parent
_EXPECTED_ODIN_SRC = _REPO_ROOT / "odin" / "src"


class OdinImportSkewTest(TestCase):
    """odin must import from THIS worktree's odin/src, never a foreign copy."""

    def test_odin_resolves_from_this_tree(self):
        try:
            import odin
        except ImportError:
            self.fail(
                "odin is not importable at all — this tree's odin/src is not "
                f"on sys.path. FIX: prepend {_EXPECTED_ODIN_SRC} to PYTHONPATH "
                "(scripts/verify.sh does this automatically)."
            )

        odin_file = getattr(odin, "__file__", None)
        self.assertIsNotNone(
            odin_file,
            "odin resolved as a namespace package (odin/src not on sys.path). "
            f"FIX: prepend {_EXPECTED_ODIN_SRC} to PYTHONPATH — "
            "scripts/verify.sh does this automatically.",
        )

        resolved = Path(odin_file).resolve()
        self.assertTrue(
            _EXPECTED_ODIN_SRC in resolved.parents,
            "odin version skew: tests imported odin from "
            f"{resolved}, expected under {_EXPECTED_ODIN_SRC}/odin/. "
            "Backend tests are using a DIFFERENT odin than the one in this "
            "worktree (likely an editable/pipx install pointing at the main "
            "checkout). FIX: prepend this tree's odin/src to PYTHONPATH, e.g. "
            f"PYTHONPATH={_EXPECTED_ODIN_SRC} python manage.py test  "
            "(scripts/verify.sh already does this).",
        )
