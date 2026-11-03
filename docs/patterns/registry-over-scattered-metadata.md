# Registry over scattered metadata

**Principle.** When the same fact must be recorded for many files (a tier,
an owner, a category, a status), it lives in one table, and a check keeps
the table and the files in step. Spreading the fact across the files makes
every read a directory walk and every change a mass edit, and it borrows
space in files that other tools parse.

**How to recognise it.** You are about to add the same key or line to more
than a handful of files. A subagent proposes "add X to every header". A
question like "which ones are Y?" would need grep to answer.

**What to do instead.** One CSV or index file next to the things it
describes, columns for the fact and a short reason, a script that fails
when a row has no folder or a folder has no row, and the file that other
tools read (frontmatter, config) left to those tools.

## Instances

- Skill tiers for the scaffolding harness. Tier tags (`tier: kit` or
  `tier: workspace`) went into all 37 SKILL.md headers, the 37 stubs and
  the playbook files, so the copy script could read them. The owner asked
  why the headers were being touched at all. Replaced by
  `docs/skills/skills.csv` with name, category, tier and reason;
  `list_tiers.py` checks it against the folders; `harness-kit/src/copy.ts` reads it.
  The tenet "one fact, one place" already covered this; it was not applied
  because the mass edit was proposed by a subagent and worked.
