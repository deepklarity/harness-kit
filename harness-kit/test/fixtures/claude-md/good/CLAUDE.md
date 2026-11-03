# CLAUDE.md

## Read before you act

| About to... | Open first |
|---|---|
| Debug a failure, or explore a flow | `docs/breadcrumb_analysis/_INDEX.md#read-first` |
| Write a doc | `docs/playbook/tone_and_taste.md` |
| Run tests | [the index](docs/breadcrumb_analysis/_INDEX.md) |

## Methodical Problem-Solving

**Breadcrumb-first:** read the index before exploring a flow.

**Diagnostics before code inspection.** Run the scripts in `scripts/`.

## Tool Rules

- **Find the row first.** When a row matches, open its page or run its tool before any grep; grep only for what no row covers, and when you had to, add the row.
- Long commands run in the background.

## Testing

```bash
./scripts/verify.sh
```

## Subagent Strategy

Explore on a cheap model.

## Stop and Ask

Stop when a fix fails twice.
