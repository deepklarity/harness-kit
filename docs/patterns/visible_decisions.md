# Visible decisions over hidden rules

**The principle.** Every operational decision the system runs on — which
model reviews, in what fallback order, what gates hold, what defaults apply
— must be VISIBLE where a human looks, CHANGEABLE as a setting, and leave a
TRAIL when changed. A rule that lives only in a code path, an ORM write, or
an operator's memory is a hidden rule, and hidden rules rot into surprises.
This is Default First's other half: a default is only honest if the human
can see it and override it without reading source.

**Test:** if explaining "why does the system do X?" requires opening a code
file or a chat transcript instead of a settings page or a doc, the decision
is hidden. Surface it.

## Instances (append as they accumulate)

- **The glm reviewer flip.** During an incident the operator changed
  `Board.reflection_model` via an ORM write, citing measurement (task 204).
  Evidence-backed — but invisible: the user discovered it weeks of
  task-time later by noticing an unfamiliar model name in a review. Had a
  reviewer-config setting existed, the flip would have been a visible
  change with a current-value display. Fix: layered reviewer config
  (global → per-board) in Settings (backlogged behind the settings
  redesign).
- **Hardcoded reflection fallback order.** Which model reviews when the
  configured one fails lives in code (`select_reviewer_by_context_size`
  precedence + a stale default that once produced opus-4-6 out of
  nowhere). Same fix as above.
- **cache_hit_rate silently wrong.** A stats formula bug showed 1300%
  cache rates for weeks; nothing defined what the number MEANT where a
  reader could check it. Metrics need their definition one hover/line away
  from the value.
- **Promote-check's phantom holds (wave 4).** The gate enforced rules
  (placeholder artifact paths) nobody had decided; operators obeyed a
  hidden rule the brief template had accidentally created.
- **A reviewer quoting a rule that does not exist** (RESUME rule 7's
  origin): the counterpart failure — an agent inventing a hidden rule.
  Same cure: rules live in named docs; anything unquotable is not a rule.

## What encouraging this looks like

- New feature with an operational choice inside → the brief names where
  the choice SURFACES (settings, doctor line, report field) — rule 6's
  sibling: "where will a human see this DECISION?"
- Operator changes system behavior → prefer a setting change over a direct
  write; when only a direct write exists, that is itself a gap to file.
- Audits check for newly-hardcoded decisions the same way they check for
  slop.
