---
name: matt-domain-modeling
description: "Build and sharpen a project's glossary and domain model, kept in the Language section of the nearest CLAUDE.md, with decisions recorded in docs/patterns/. Use when a term is fuzzy or contested, when adding to or editing the glossary, or when recording a design decision (an ADR)."
imported: true
imported_from: https://github.com/mattpocock/skills
preset_enabled: false
---

# Domain Modeling

Actively build and sharpen the project's domain model as you design: challenge terms, invent edge-case scenarios, and write the glossary and decisions down the moment they crystallise. Merely reading the glossary for vocabulary is a one-line habit any skill can do; this skill is for changing the model.

## Where it lives

- **Glossary**: a `## Language` section in the nearest `CLAUDE.md`. Repo-wide terms go in the root `CLAUDE.md`; terms of one project go in that project's (`<project>/CLAUDE.md`). Add the section when the first term is resolved.
- **Decisions (ADRs)**: a `docs/patterns/` entry appended to the matching file, or, when the decision is a standing rule every session must follow rather than a learning, its own `##` section directly under Language in the same `CLAUDE.md`, headed by the rule in one sentence.

## During the session

### Challenge against the glossary

When the user uses a term that conflicts with the existing Language section, call it out immediately. "Your glossary defines 'cancellation' as X, but you seem to mean Y. Which is it?"

### Sharpen fuzzy language

When the user uses vague or overloaded terms, propose a precise canonical term. "You're saying 'account': do you mean the Customer or the User? Those are different things."

### Discuss concrete scenarios

When domain relationships are being discussed, stress-test them with specific scenarios. Invent scenarios that probe edge cases and force the user to be precise about the boundaries between concepts.

### Cross-reference with code

When the user states how something works, check whether the code agrees. If you find a contradiction, surface it: "Your code cancels entire Orders, but you just said partial cancellation is possible. Which is right?"

### Update the glossary inline

When a term is resolved, add or edit its entry in the Language section right there, in the shape below. The glossary holds terms and their definitions only; implementation decisions go to a pattern.

### Record decisions sparingly

Record a decision only when all three hold:

1. **Hard to reverse**: the cost of changing your mind later is meaningful.
2. **Surprising without context**: a future reader will look at the code and wonder why it was done this way.
3. **The result of a real trade-off**: there were genuine alternatives and you picked one for specific reasons.

If a decision is easy to reverse, you'll just reverse it. If it's not surprising, nobody will wonder why. If there was no real alternative, there's nothing to record beyond "we did the obvious thing." When all three hold, append to the matching file in `docs/patterns/` (never fork) an entry "decision: <context>, <what was decided>, <why>"; the decision is the principle. A rejected alternative worth remembering goes in the principle's own sentences. Say in one line which file it went to.

What qualifies: architectural shape ("the write model is event-sourced"), integration patterns between projects ("the CLI talks to the API over REST, never the database"), technology choices that carry lock-in, boundary and ownership decisions, deliberate deviations from the obvious path, constraints not visible in the code, and rejected alternatives when the rejection is non-obvious.

## Glossary entry shape

```md
## Language

**Order**:
{One or two sentences: what the term is, not what it does.}
_Avoid_: Purchase, transaction

**Invoice**:
A request for payment sent to a customer after delivery.
_Avoid_: Bill, payment request
```

- **Be opinionated.** When several words exist for one concept, pick the best and list the others under `_Avoid_`.
- **Only terms specific to this project.** General programming concepts (timeouts, error types, utility patterns) stay out even when the project uses them heavily.
- **Group under subheadings** when natural clusters emerge; a flat list is fine for one cohesive area.
