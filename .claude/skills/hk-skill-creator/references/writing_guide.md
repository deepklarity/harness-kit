# Skill Writing Guide

Detailed reference for writing effective SKILL.md files and bundled resources. Based on the [official Claude Code skills documentation](https://code.claude.com/docs/en/skills).

## Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (configuration)
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code for deterministic/repetitive tasks
    ├── references/ - Docs loaded into context as needed
    └── assets/     - Files used in output (templates, icons, fonts)
```

## Progressive Disclosure

Skills use a three-level loading system:

1. **Metadata (name + description)** — Always in context. Descriptions are truncated at 250 characters in the skill listing, so front-load the key use case.
2. **SKILL.md body** — Loaded when skill triggers (<500 lines ideal)
3. **Bundled resources** — Loaded on demand (unlimited size, scripts can execute without loading into context)

These limits are approximate — go longer if the content earns its space.

### Resource guidelines

- **scripts/**: Executable code for tasks needing deterministic reliability or that are repeatedly rewritten. Token efficient — may be executed without loading into context. If every test run independently writes a similar helper script, that's a strong signal to bundle it here. Reference via `${CLAUDE_SKILL_DIR}/scripts/my_script.py` for portable paths.
- **references/**: Documentation loaded as needed. Keep SKILL.md lean; move detailed schemas, API docs, and large examples here. For files >300 lines, include a table of contents. Include grep search patterns in SKILL.md for very large reference files.
- **assets/**: Files used in output (templates, boilerplate, images). Not loaded into context — copied or used in final output.

### Domain organization

When a skill supports multiple domains/frameworks, organize by variant:

```
cloud-deploy/
├── SKILL.md (workflow + selection)
└── references/
    ├── aws.md
    ├── gcp.md
    └── azure.md
```

Claude reads only the relevant reference file based on user context.

## Where Skills Live

Where you store a skill determines who can use it:

| Location   | Path                                         | Applies to                     |
|------------|----------------------------------------------|--------------------------------|
| Enterprise | Managed settings                             | All users in your organization |
| Personal   | `~/.claude/skills/<skill-name>/SKILL.md`     | All your projects              |
| Project    | `.claude/skills/<skill-name>/SKILL.md`       | This project only              |
| Plugin     | `<plugin>/skills/<skill-name>/SKILL.md`      | Where plugin is enabled        |

When skills share the same name across levels, higher-priority locations win: enterprise > personal > project. Plugin skills use a `plugin-name:skill-name` namespace, so they cannot conflict.

Skills in `--add-dir` directories are also auto-discovered and support live change detection.

## Frontmatter Reference

All fields are optional. Only `description` is recommended so Claude knows when to use the skill.

### Core fields

| Field            | Required    | Description |
|------------------|-------------|-------------|
| `name`           | No          | Display name / slash command. If omitted, uses directory name. Lowercase letters, numbers, hyphens only (max 64 chars). For this project, use `hk-` prefix. |
| `description`    | Recommended | What the skill does and when to use it. Claude uses this to decide when to apply the skill. Front-load the key use case — truncated at 250 chars in the listing. |
| `argument-hint`  | No          | Hint shown during autocomplete. Example: `[issue-number]` or `[filename] [format]`. |
| `allowed-tools`  | No          | Tools Claude can use without asking permission when this skill is active. Space-separated string or YAML list. |

### Invocation control

| Field                      | Default | Description |
|----------------------------|---------|-------------|
| `disable-model-invocation` | `false` | Set `true` to prevent Claude from auto-loading. Use for side-effect workflows (deploy, commit, send-message). Description is removed from context entirely. |
| `user-invocable`           | `true`  | Set `false` to hide from `/` menu. Use for background knowledge Claude should know but users shouldn't invoke directly. |

How the two fields interact:

| Frontmatter                      | User can invoke | Claude can invoke | When loaded into context |
|----------------------------------|-----------------|-------------------|--------------------------|
| (default)                        | Yes             | Yes               | Description always in context, full skill loads when invoked |
| `disable-model-invocation: true` | Yes             | No                | Description not in context, full skill loads when user invokes |
| `user-invocable: false`          | No              | Yes               | Description always in context, full skill loads when invoked |

### Execution control

| Field    | Default           | Description |
|----------|-------------------|-------------|
| `model`  | Inherits          | Model to use when this skill is active. |
| `effort` | Inherits          | Effort level: `low`, `medium`, `high`, `max` (Opus 4.6 only). Overrides session effort. |
| `context`| (inline)          | Set to `fork` to run in an isolated subagent context. |
| `agent`  | `general-purpose` | Which subagent type to use when `context: fork` is set. Options: `Explore`, `Plan`, `general-purpose`, or any custom agent from `.claude/agents/`. |
| `shell`  | `bash`            | Shell for `!` command `` and ` ```! ` blocks. Accepts `bash` or `powershell`. |

### Scoping

| Field   | Default | Description |
|---------|---------|-------------|
| `paths` | (all)   | Glob patterns that limit when this skill activates. Comma-separated string or YAML list. Claude loads the skill automatically only when working with files matching the patterns. |
| `hooks` | (none)  | Hooks scoped to this skill's lifecycle. See hooks documentation. |

### Description writing

Descriptions should be slightly "pushy" to combat undertriggering. Claude tends to not use skills even when they'd be useful. Front-load the most important information because of the 250-char truncation.

**Weak**: "How to process data files."
**Strong**: "Process and transform data files. Use whenever the user mentions data transformation, CSV processing, column manipulation, file conversion, or wants to reshape structured data, even if they don't explicitly ask for a 'data skill.'"

Include:
- What the skill does (one sentence, first)
- Specific trigger contexts and phrases
- Near-miss scenarios where the skill should still trigger
- The slash command (e.g., "or /hk-skill-name")

## String Substitutions

Skills support dynamic value substitution in the skill content:

| Variable               | Description |
|------------------------|-------------|
| `$ARGUMENTS`           | All arguments passed when invoking. If not present in content, args are appended as `ARGUMENTS: <value>`. |
| `$ARGUMENTS[N]`        | Access specific argument by 0-based index. `$ARGUMENTS[0]` = first arg. |
| `$N`                   | Shorthand for `$ARGUMENTS[N]`. `$0` = first arg, `$1` = second. |
| `${CLAUDE_SESSION_ID}` | Current session ID. Useful for logging or session-specific files. |
| `${CLAUDE_SKILL_DIR}`  | Directory containing the skill's SKILL.md. Use to reference bundled scripts/files portably. |

Example using positional args:
```yaml
---
name: migrate-component
description: Migrate a component from one framework to another
---
Migrate the $0 component from $1 to $2.
Preserve all existing behavior and tests.
```

Running `/migrate-component SearchBar React Vue` replaces `$0` → `SearchBar`, `$1` → `React`, `$2` → `Vue`.

## Dynamic Context Injection

The `` !`<command>` `` syntax runs shell commands before the skill content is sent to Claude. The command output replaces the placeholder — Claude only sees the final result, not the command.

```yaml
---
name: pr-summary
description: Summarize changes in a pull request
context: fork
agent: Explore
---

## Pull request context
- PR diff: !`gh pr diff`
- PR comments: !`gh pr view --comments`
- Changed files: !`gh pr diff --name-only`

Summarize this pull request.
```

For multi-line commands, use a fenced code block opened with ` ```! `:

````markdown
## Environment
```!
node --version
npm --version
git status --short
```
````

This is preprocessing — it runs before Claude sees anything. Useful for injecting live data (git state, API responses, file listings) into the skill.

## Subagent Execution

Add `context: fork` to run a skill in an isolated subagent. The skill content becomes the subagent's prompt — it won't have access to conversation history.

**Only use `context: fork` for task-oriented skills with explicit instructions.** If a skill contains only guidelines ("use these conventions"), the subagent receives guidelines but no actionable prompt and returns nothing useful.

```yaml
---
name: deep-research
description: Research a topic thoroughly
context: fork
agent: Explore
---

Research $ARGUMENTS thoroughly:
1. Find relevant files using Glob and Grep
2. Read and analyze the code
3. Summarize findings with specific file references
```

The `agent` field picks the execution environment (model, tools, permissions). Options include built-in agents (`Explore`, `Plan`, `general-purpose`) or custom agents from `.claude/agents/`.

Skills and subagents work together in two directions:

| Approach                    | System prompt                             | Task                        |
|-----------------------------|-------------------------------------------|-----------------------------|
| Skill with `context: fork`  | From agent type (`Explore`, `Plan`, etc.) | SKILL.md content            |
| Subagent with `skills` field| Subagent's markdown body                  | Claude's delegation message |

## Body Writing Patterns

### Voice and tone

- **Imperative form** — "Run the validator" not "You should run the validator"
- **Explain why** — "Keep SKILL.md under 500 lines because the full body loads into context on every trigger, and long skills waste tokens on irrelevant instructions" not "Keep SKILL.md under 500 lines"
- **Avoid MUST/NEVER overload** — if you need a strong constraint, explain the reasoning so the model understands. "ALWAYS use JSON output" is weaker than "Use JSON output because downstream consumers parse it programmatically and will break on freeform text"

### Structure patterns

**Workflow-Based** (sequential processes):
```
## Overview → ## Decision Tree → ## Step 1 → ## Step 2...
```

**Task-Based** (tool collections):
```
## Overview → ## Quick Start → ## Task Category 1 → ## Task Category 2...
```

**Reference/Guidelines** (standards):
```
## Overview → ## Guidelines → ## Specifications → ## Usage...
```

**Capabilities-Based** (integrated systems):
```
## Overview → ## Core Capabilities → ### 1. Feature → ### 2. Feature...
```

Most skills combine patterns — start with one, add others where they help.

### Defining output formats

```markdown
## Report structure
Use this template:
# [Title]
## Executive summary
## Key findings
## Recommendations
```

### Examples pattern

```markdown
## Commit message format
**Example 1:**
Input: Added user authentication with JWT tokens
Output: feat(auth): implement JWT-based authentication
```

### Arguments / context block

If the skill accepts arguments, include a context block that captures them:

```markdown
<skill_context> $ARGUMENTS </skill_context>

If the context above is empty, ask the user what they need.
```

### Enabling extended thinking

Include the word "ultrathink" anywhere in the skill content to enable extended thinking mode.

## Common Mistakes

1. **Description too vague** — "A skill for working with files" triggers on nothing useful
2. **Description too long without front-loading** — important info gets truncated at 250 chars
3. **Body too long** — loading 1000 lines of instructions for a simple task wastes tokens
4. **No resource references** — bundled scripts/references exist but SKILL.md never mentions when to read/run them
5. **Over-specified** — rigid step-by-step for tasks that need flexibility. Give the model room to adapt.
6. **Under-specified** — "Do the thing" with no guidance on edge cases, output format, or quality bar
7. **Testing your mocks** — skill instructions that only work for the exact examples used during testing
8. **name/directory mismatch** — frontmatter `name` must exactly match the directory name
9. **Guidelines-only fork** — `context: fork` on a skill with no task instructions produces nothing because the subagent has no conversation context
10. **Hardcoded paths** — use `${CLAUDE_SKILL_DIR}` instead of absolute paths to reference bundled files
