"""``.claude/skills/<name>/SKILL.md`` stubs resolve to the real skill under ``docs/skills/``."""
import tempfile
from pathlib import Path
from unittest import TestCase

from tasks.skill_importer import list_installed_skills, parse_skill_md

STUB = "---\nname: {n}\ndescription: Demo audit\n---\n\nThis skill lives in `docs/skills/audits/{n}/`. Read `docs/skills/audits/{n}/SKILL.md` and follow it exactly.\n"


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class SkillStubTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_stub_body_is_replaced_by_target(self):
        real = self.root / "docs/skills/audits/hk-demo/SKILL.md"
        _write(real, "---\nname: hk-demo\ndescription: Demo audit\n---\n# Demo Audit\n\nRun `pip install somepkg` then the security audit.\n")
        stub = self.root / ".claude/skills/hk-demo/SKILL.md"
        _write(stub, STUB.format(n="hk-demo"))

        parsed = parse_skill_md(stub)
        self.assertEqual(parsed["title"], "Demo Audit")
        self.assertIn("security audit", parsed["body"])
        self.assertEqual(Path(parsed["skill_dir"]).resolve(), real.parent.resolve())
        self.assertEqual(parsed["frontmatter"]["name"], "hk-demo")

        listed = list_installed_skills(self.root / ".claude/skills")
        self.assertEqual(len(listed), 1)
        self.assertIn("somepkg", listed[0]["python_dependencies"], listed[0])

    def test_missing_stub_target_keeps_stub(self):
        stub = self.root / ".claude/skills/hk-gone/SKILL.md"
        _write(stub, STUB.format(n="hk-gone"))
        parsed = parse_skill_md(stub)
        self.assertIn("This skill lives in", parsed["body"])
        self.assertNotIn("skill_dir", parsed)
