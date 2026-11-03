#!/usr/bin/env python3
"""
Scaffold a skill in this repo's layout: the skill itself under
docs/skills/<category>/<name>/ and a stub at .claude/skills/<name>/SKILL.md
that Claude Code discovers.

Usage:
    python init_skill.py <name> --category <audits|reviews|development|debugging|git|knowledge|understanding>
    python init_skill.py <name> --path <dir>            # body goes to <dir>/<name>; stub still lands in the repo root
    python init_skill.py <name> --category <c> --stub-only   # rewrite the stub from an existing SKILL.md

One of --category or --path is required. The stub goes to <root>/.claude/skills/<name>/SKILL.md
(the git root, or the current folder when it is not a repo yet).
"""

import re
import subprocess
import sys
from pathlib import Path

SKILL_TEMPLATE = '''---
name: {skill_name}
description: "[TODO: what it makes, then the trigger phrases. One line, under 400 characters. Ends with: Triggers on: '...', '...', or /{skill_name}.]"
argument-hint: "[optional: expected arguments]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
# Optional: model: sonnet | disable-model-invocation: true (only the user can run it)
---

# {skill_title}

[TODO: one or two sentences: what this skill does and what it makes.]

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, ask what the skill should work on.

## Steps

### 1. [TODO]

[TODO: the first action, in imperative form, ending where the agent can tell done from not done.]

### 2. [TODO]

[TODO: the next action. Name bundled files by their real path: docs/skills/<category>/{skill_name}/scripts/x.py]

## Done when

[TODO: the check that says the job is finished, and where the output went.]
'''

STUB_BODY = '''
This skill lives in `{rel}/`. Read `{rel}/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `{rel}/`.
'''


def git_root(start):
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def write_stub(skill_dir, root):
    """Copy the SKILL.md frontmatter into .claude/skills/<name>/SKILL.md with the pointer body."""
    text = (skill_dir / "SKILL.md").read_text()
    m = re.match(r"^(---\n.*?\n---\n)", text, re.DOTALL)
    if not m:
        print(f"Error: no frontmatter in {skill_dir / 'SKILL.md'}")
        return None
    rel = skill_dir.relative_to(root).as_posix()
    stub_dir = root / ".claude" / "skills" / skill_dir.name
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "SKILL.md"
    stub.write_text(m.group(1) + STUB_BODY.format(rel=rel))
    return stub


def init_skill(skill_name, parent):
    skill_dir = Path(parent).resolve() / skill_name
    if skill_dir.exists():
        print(f"Error: directory already exists: {skill_dir}")
        return None
    skill_dir.mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "references").mkdir()
    skill_title = " ".join(w.capitalize() for w in skill_name.split("-"))
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title)
    )
    return skill_dir


def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__)
        sys.exit(1)
    skill_name = args[0]
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", skill_name) or len(skill_name) > 64:
        print(f"Error: name must be hyphen-case, 64 chars or fewer: {skill_name}")
        sys.exit(1)

    def opt(flag):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                print(f"Error: {flag} requires a value")
                sys.exit(1)
            return args[i + 1]
        return None

    category, path = opt("--category"), opt("--path")
    if bool(category) == bool(path):
        print("Error: pass exactly one of --category <name> or --path <dir>")
        sys.exit(1)

    if category:
        # The git root when there is one; the current folder otherwise (a
        # workspace being set up is committed last, after its skills land).
        root = git_root(Path.cwd()) or Path.cwd()
        if not (root / "docs" / "skills").is_dir():
            print(f"Error: no docs/skills under {root}; run from the workspace root or use --path")
            sys.exit(1)
        parent = root / "docs" / "skills" / category
        if not parent.is_dir():
            have = sorted(p.name for p in (root / "docs" / "skills").glob("*/") if p.is_dir())
            print(f"Error: no category {category!r} under docs/skills; have: {', '.join(have)}")
            sys.exit(1)
    else:
        parent = Path(path).resolve()
        root = git_root(parent) or parent

    if "--stub-only" in args:
        skill_dir = parent / skill_name
        if not (skill_dir / "SKILL.md").exists():
            print(f"Error: {skill_dir / 'SKILL.md'} does not exist")
            sys.exit(1)
    else:
        skill_dir = init_skill(skill_name, parent)
        if skill_dir is None:
            sys.exit(1)
        print(f"Created {skill_dir}")
        print("  SKILL.md    — fill in the TODOs")
        print("  scripts/    — bundled scripts, or delete if unused")
        print("  references/ — reference docs, or delete if unused")

    stub = write_stub(skill_dir, root)
    if stub is None:
        sys.exit(1)
    print(f"Stub written to {stub}")
    if "--stub-only" not in args:
        print("Next: edit SKILL.md, re-run with --stub-only to refresh the stub, then run validate_skill.py")


if __name__ == "__main__":
    main()
