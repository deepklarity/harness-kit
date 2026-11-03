# Skills

The real skill files live here, one folder per category. `.claude/skills/<name>/SKILL.md` is a stub with the same frontmatter that points back to the folder here, so Claude Code discovers the skill while the content stays organised and versioned in docs. Edit the copy here, then refresh the stub with `python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category> --stub-only`.

To add a skill, run `/hk-skill-creator` or `python3 docs/skills/knowledge/hk-skill-creator/scripts/init_skill.py <name> --category <category>`; it writes both the folder here and the stub.

## Categories

- [audits](audits/README.md), 13 skills: read-only audits that produce a page.
- [reviews](reviews/README.md), 2 skills: reviewing changes.
- [development](development/README.md), 6 skills: building features and designing modules.
- [debugging](debugging/README.md), 5 skills: finding root causes.
- [git](git/README.md), 3 skills: merging, conflicts, changelogs.
- [knowledge](knowledge/README.md), 6 skills: capturing, writing up and handing over knowledge.
- [understanding](understanding/README.md), 3 skills: building human understanding and alignment (asking until clarity, explaining, quizzing).

## Tiers, and how a kit skill names workspace things

Every skill's tier lives as a row in `docs/skills/skills.csv`, not in the
SKILL.md itself. `kit` means the skill is copied into any workspace by
`npx @deepklarity/harnesskit init`; `workspace` means it is wired to this repo's own
projects and stays here. `list_tiers.py` under
`knowledge/hk-skill-creator/scripts/` checks `skills.csv` against the skill
folders on disk and prints the list grouped by tier.

A kit skill never hardcodes a path that belongs to a workspace. It names the
thing and says where the workspace's CLAUDE.md declares it:

<details>
<summary>Example: what a kit command stands for</summary>

| Instead of | Write |
|---|---|
| `scripts/verify.sh backend` | the verify gate (CLAUDE.md, Testing section) for the suite the change touched |
| `tail -f backend/logs/app.log` | tail the log named in CLAUDE.md for that service |
| `<inspect-script> <id> --brief` | the diagnostic script for that entity (see CLAUDE.md), or a direct read-only query |
| `cli/tests/`, `backend/tests/` | the suite that owns the code |
| `frontend/src/services/` | the frontend's API client folder |

</details>

The harness-kit specifics are still useful as an example. Keep them in one
fold at the end of the skill, headed `## Example: in harness-kit`, so a
reader here still gets the exact commands and a reader elsewhere can skip it.
