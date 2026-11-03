# Skill mechanics in Claude Code

What the harness does with a SKILL.md: where it looks, which frontmatter keys it reads, and the substitutions it performs before the agent sees the text. How to write the body is `docs/skills/knowledge/matt-writing-for-agents/SKILL.md`; this file is only the plumbing. Based on the [Claude Code skills documentation](https://code.claude.com/docs/en/skills).

## Anatomy

```
<name>/
├── SKILL.md          # frontmatter + instructions; loaded when the skill fires
├── scripts/          # code the agent runs; never loaded into context unless read
├── references/       # docs the agent reads on demand
└── assets/           # files the output is built from (templates, fonts)
```

Three loading tiers: the description is in context on every turn; the SKILL.md body loads when the skill fires; bundled files load only when the skill points the agent at them. Reference for one branch of the skill belongs in `references/` behind a pointer, so the branches that do not need it never pay for it.

## Where skills live

| Location | Path | Applies to |
|---|---|---|
| Enterprise | Managed settings | Everyone in the organisation |
| Personal | `~/.claude/skills/<name>/SKILL.md` | All your projects |
| Project | `.claude/skills/<name>/SKILL.md` | This project only |
| Plugin | `<plugin>/skills/<name>/SKILL.md` | Where the plugin is enabled |

Same name at more than one level: enterprise beats personal beats project. Plugin skills are namespaced `plugin:skill` and cannot collide. Directories passed with `--add-dir` are scanned too.

In this repo the project row is a stub. The skill itself lives at `docs/skills/<category>/<name>/` and the stub carries the same frontmatter plus a paragraph pointing there (`docs/skills/README.md`). Name bundled files by that real path; `${CLAUDE_SKILL_DIR}` resolves to the stub directory and finds nothing.

## Frontmatter

Keys this repo's skills use:

| Key | Meaning |
|---|---|
| `name` | Slash command and display name. Lowercase letters, digits, hyphens, 64 chars or fewer, equal to the directory name. |
| `description` | What the skill makes, then the trigger phrases, then `/name`. One quoted line under 400 characters; the only part loaded on every turn. |
| `argument-hint` | Shown at autocomplete, e.g. `[pr-number]`. |
| `allowed-tools` | Tools the skill may use without a prompt, comma separated. The subagent tool is `Agent`. |
| `model` | Model override while the skill is active. |
| `disable-model-invocation` | `true` means only a person can run it (deploy, commit, anything with side effects); the description leaves the agent's context. |
| `generated`, `generated_from` | Set by an exporter for skills generated from a preset. The body under docs/skills is the live source. |
| `imported`, `imported_from` | Set by the skill importer for skills copied from another repo. |
| `preset_enabled` | Whether the preset for the skill is on. |

Keys the harness accepts that this repo does not use: `user-invocable: false` (hidden from the `/` menu, agent can still fire it), `effort`, `context: fork` with `agent`, `paths`, `hooks`, `shell`. The validator accepts them without a warning.

## Substitutions

| Token | Replaced with |
|---|---|
| `$ARGUMENTS` | Everything typed after the skill name. Absent from the body, the arguments are appended as `ARGUMENTS: <value>`. |
| `$ARGUMENTS[N]`, `$N` | The Nth argument, from 0. |
| `${CLAUDE_SESSION_ID}` | The session id. |
| `${CLAUDE_SKILL_DIR}` | The directory holding the SKILL.md that fired: the stub, in this repo. |

Skills that take arguments open with a context block:

```markdown
<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, ask what to work on.
```

## Dynamic context

`` !`<command>` `` runs a shell command before the body reaches the agent and replaces itself with the output; the agent never sees the command. A fenced block opened with ```` ```! ```` does the same for several lines. Use it for live state a step needs on every run: `git status --short`, `gh pr diff`.

## Running in a subagent

`context: fork` runs the skill as a subagent's prompt with no conversation history, and `agent:` picks the agent type (`Explore`, `Plan`, `general-purpose`, or one from `.claude/agents/`). It only works for a skill whose body is a task with steps; a body of guidelines gives the subagent nothing to do. The other direction, a subagent definition with a `skills:` field, loads those skills into the subagent and takes its task from the delegation message.

## What goes wrong most

1. A description that names a category ("a skill for working with files") and no trigger phrase; it never fires.
2. A description that opens with background and reaches the trigger phrases after the reader stops.
3. `name` differing from the directory name.
4. Bundled files nothing in SKILL.md points at, or SKILL.md pointing at files that do not exist. The validator catches both.
5. A skill that only works on the prompts it was tested with.
6. `context: fork` on a guidelines-only skill.
7. Editing the description in docs/skills and forgetting the stub; `init_skill.py <name> --category <c> --stub-only` refreshes it.
