#!/usr/bin/env python3
"""
Scaffold a new skill directory with template SKILL.md.

Usage:
    python init_skill.py <skill-name>
    python init_skill.py <skill-name> --path /custom/location

Default path: .claude/skills (relative to repo root, detected via git)
"""

import subprocess
import sys
from pathlib import Path


SKILL_TEMPLATE = '''---
name: {skill_name}
description: "[TODO: What this skill does and when to trigger it. Front-load the key use case (truncated at 250 chars in listing). Be specific — include trigger phrases, contexts, and near-miss scenarios. Descriptions should be slightly pushy to combat undertriggering.]"
argument-hint: "[optional: describe expected arguments]"
allowed-tools: Bash Read Edit Write Grep Glob
# --- Optional fields (uncomment as needed) ---
# disable-model-invocation: true   # Only user can invoke via /name (use for side-effect workflows)
# user-invocable: false            # Only Claude can invoke (use for background knowledge)
# context: fork                    # Run in isolated subagent
# agent: general-purpose           # Subagent type when context: fork (Explore, Plan, general-purpose, or custom)
# model: sonnet                    # Model override when skill is active
# effort: high                     # Effort level: low, medium, high, max
# paths: "*.py,*.ts"              # Only activate when working with matching files
# shell: bash                      # Shell for !`command` blocks (bash or powershell)
---

# /{skill_name} — {skill_title}

[TODO: 1-2 sentences — what this skill enables and why it exists.]

## Context

<skill_context> $ARGUMENTS </skill_context>

If the context above is empty or unclear, ask the user what they need.

## Process

### Step 1: [TODO]

[TODO: First major step. Use imperative form. Explain why, not just what.]

### Step 2: [TODO]

[TODO: Next step. Reference bundled resources where relevant.]

## Resources

- `scripts/` — [TODO: describe bundled scripts, or delete if unused. Reference via `${{CLAUDE_SKILL_DIR}}/scripts/`]
- `references/` — [TODO: describe reference docs, or delete if unused]
'''


def find_repo_root():
    """Find git repo root, or return cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def init_skill(skill_name, path):
    """Create a new skill directory with template SKILL.md."""
    skill_dir = Path(path).resolve() / skill_name

    if skill_dir.exists():
        print(f"Error: directory already exists: {skill_dir}")
        return None

    # Validate name
    import re
    if not re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', skill_name):
        print(f"Error: name must be hyphen-case (lowercase letters, digits, hyphens): {skill_name}")
        return None

    if len(skill_name) > 64:
        print(f"Error: name too long ({len(skill_name)} chars, max 64)")
        return None

    # Create structure
    skill_dir.mkdir(parents=True)
    (skill_dir / "scripts").mkdir()
    (skill_dir / "references").mkdir()

    # Write SKILL.md
    skill_title = " ".join(w.capitalize() for w in skill_name.split("-"))
    (skill_dir / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(skill_name=skill_name, skill_title=skill_title)
    )

    print(f"Created skill at {skill_dir}")
    print(f"  SKILL.md    — edit frontmatter and instructions")
    print(f"  scripts/    — add executable scripts (or delete if unused)")
    print(f"  references/ — add reference docs (or delete if unused)")
    print(f"\nNext: edit SKILL.md, then validate with validate_skill.py")
    return skill_dir


def main():
    if len(sys.argv) < 2:
        print("Usage: init_skill.py <skill-name> [--path <path>]")
        print("Default path: .claude/skills/ (relative to repo root)")
        sys.exit(1)

    skill_name = sys.argv[1]

    # Parse --path
    path = None
    if "--path" in sys.argv:
        idx = sys.argv.index("--path")
        if idx + 1 < len(sys.argv):
            path = sys.argv[idx + 1]
        else:
            print("Error: --path requires a value")
            sys.exit(1)

    if path is None:
        path = str(find_repo_root() / ".claude" / "skills")

    result = init_skill(skill_name, path)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
