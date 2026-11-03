---
name: matt-codebase-design
description: "Glossary and principles for deep modules: module, interface, depth, seam, adapter, leverage, locality. Use when designing an interface, placing a seam, or when another skill needs the vocabulary; also scans a codebase for deepening candidates and writes them up. Triggers on: 'find deepening opportunities', 'improve the architecture', 'architecture review', or /matt-codebase-design."
imported: true
imported_from: https://github.com/mattpocock/skills
preset_enabled: false
---

# Codebase Design

Design **deep modules**: a lot of behaviour behind a small interface, placed at a clean seam, testable through that interface. The aim is leverage for callers, locality for maintainers, and testability for everyone.

The vocabulary and principles below are the reference half; the workflow at the end scans a codebase for deepening candidates and walks the one the user picks. Another skill that only needs the terms reads the glossary and stops there.

## Glossary

Use these terms exactly; each entry lists the words it replaces.

**Module**: anything with an interface and an implementation. Deliberately scale-agnostic: a function, class, package, or tier-spanning slice. _Avoid_: unit, component, service.

**Interface**: everything a caller must know to use the module correctly: the type signature, but also invariants, ordering constraints, error modes, required configuration, and performance characteristics. _Avoid_: API, signature (they refer only to the type-level surface).

**Implementation**: what's inside a module, its body of code. Distinct from **Adapter**: a thing can be a small adapter with a large implementation (a Postgres repo) or a large adapter with a small implementation (an in-memory fake). Reach for "adapter" when the seam is the topic; "implementation" otherwise.

**Depth**: leverage at the interface. The amount of behaviour a caller (or test) can exercise per unit of interface they have to learn. Depth is measured as leverage, not as Ousterhout's ratio of implementation lines to interface lines, because the ratio rewards padding the implementation.

```
┌─────────────────────┐          ┌─────────────────────────────────┐
│   Small interface   │          │       Large interface           │
├─────────────────────┤          ├─────────────────────────────────┤
│                     │          │  Thin implementation            │
│  Deep implementation│          └─────────────────────────────────┘
│                     │
└─────────────────────┘          Shallow: the interface is nearly as
Deep: complex logic hidden       complex as what sits behind it
```

**Seam** _(Michael Feathers)_: a place where you can alter behaviour without editing in that place; the *location* at which a module's interface lives. Where to put the seam is its own design decision, distinct from what goes behind it. _Avoid_: boundary (overloaded with DDD's bounded context).

**Adapter**: a concrete thing that satisfies an interface at a seam. Describes *role* (what slot it fills), not substance (what's inside).

**Leverage**: what callers get from depth. More capability per unit of interface they learn. One implementation pays back across N call sites and M tests.

**Locality**: what maintainers get from depth. Change, bugs, knowledge, and verification concentrate in one place rather than spreading across callers. Fix once, fixed everywhere.

## Principles

- **Depth is a property of the interface, not the implementation.** A deep module can be internally composed of small, mockable, swappable parts; they just aren't part of the interface. A module can have **internal seams** (private to its implementation, used by its own tests) as well as the **external seam** at its interface.
- **The deletion test.** Imagine deleting the module. If complexity vanishes, it was a pass-through. If complexity reappears across N callers, it was earning its keep.
- **The interface is the test surface.** Callers and tests cross the same seam. If you want to test *past* the interface, the module is probably the wrong shape.
- **One adapter means a hypothetical seam. Two adapters means a real one.** Introduce a seam only when something actually varies across it, typically production plus test.

When designing an interface, ask whether you can drop a method, simplify a parameter, or move more complexity inside.

## Designing for testability

1. **Accept dependencies, don't create them.**

   ```typescript
   // Testable
   function processOrder(order, paymentGateway) {}

   // Hard to test
   function processOrder(order) {
     const gateway = new StripeGateway();
   }
   ```

2. **Return results, don't produce side effects.**

   ```typescript
   // Testable
   function calculateDiscount(cart): Discount {}

   // Hard to test
   function applyDiscount(cart): void {
     cart.total -= discount;
   }
   ```

3. **Small surface area.** Every method you leave off the interface is a method nobody has to test or learn.

## Going deeper

- **Deepening a cluster given its dependencies**, see [DEEPENING.md](DEEPENING.md): dependency categories and replace-don't-layer testing.
- **Exploring alternative interfaces**, see [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md): spin up parallel sub-agents to design the interface several radically different ways, then compare on depth, locality, and seam placement.

## Finding deepening opportunities

Run this when the user asks for an architecture review or for the deepening opportunities in a codebase: scan, write the candidates up as one page, then walk the one they choose. Use the glossary terms exactly, on the page and in chat. Domain names come from the `## Language` section of the nearest `CLAUDE.md` when there is one, otherwise from the code; decisions already recorded in `docs/patterns/` are not re-litigated.

### 1. Scan

Deepening a module pays off by making future changes to it easier, so weight the parts of the codebase that have recently changed. Decide *where* to look before you look:

- If the user named a direction (a module, a subsystem, a pain point), take it.
- Otherwise, walk back a good stretch of `git log --oneline --stat` to find the hot spots, the files and areas that keep coming up, and let those paths pull attention first. If the changes are scattered with no clear hot spot, widen the net.

Read the Language section and any `docs/patterns/` entries touching the area first. Then spawn one sub-agent (sonnet; it is judging code, not searching) to walk the codebase. Give it the hot-spot paths and these questions to answer as it reads:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules shallow, with an interface nearly as complex as the implementation?
- Where have pure functions been extracted just for testability, but the real bugs hide in how they're called (no **locality**)?
- Where do tightly-coupled modules leak across their seams?
- Which parts of the codebase are untested, or hard to test through their current interface?

Tell it to apply the **deletion test** to anything suspected shallow: would deleting it concentrate complexity, or just move it? "Concentrates" is the signal you want. It returns when every hot-spot path has been read and each candidate has its files, the problem, and the deletion-test result.

### 2. Write the candidates up

Build the page in `docs/skills/knowledge/hk-writeup/SHELL.html` (replace the `<!-- CONTENT -->` line and the title; voice from `docs/playbook/tone_and_taste.md`) and write it to `writeup/matt-codebase-design-<subject>.html` at the repo root, where `<subject>` is the direction the user named or the repo name.

Order candidates by how much they would pay back, and say in the lede which one you would start with and why. Each candidate is one `<section>` with bold lead-ins, the same labels on every entry:

- **Files:** the modules involved, in mono type at the end of the entry.
- **Problem:** why the current shape causes friction.
- **Solution:** in plain English, what would change.
- **Wins:** in terms of locality and leverage, and how the tests improve.
- **Before / after:** two `<pre>` box sketches in the style of the deep-versus-shallow diagram above, showing the interface shrinking and the implementation absorbing the wrappers. Label the boxes with the modules and the number of entry points, not method names; the interface itself is designed in step 3.
- **Dependency category:** in-process, local-substitutable, ports & adapters, or mock, from [DEEPENING.md](DEEPENING.md).

If a candidate contradicts a recorded decision, say so in the entry only when the friction is real enough to reopen it: "contradicts the pattern <name>, but worth reopening because …".

Say in one line where the page went and which candidate you would start with; step 3 begins when the user names one.

### 3. Walk the chosen candidate

Work through these with the user in chat, and append each answer as it lands to the candidate's section on the step-2 page, under a bold lead-in with the same name:

1. **Constraints**: what any deepened shape must still satisfy (callers, performance, data ownership).
2. **Dependencies**: each one classified by [DEEPENING.md](DEEPENING.md) category, and what that means for testing across the seam.
3. **Shape**: what the deepened module's interface exposes, and how small it can get.
4. **Behind the seam**: what the implementation hides, and which internal seams stay private.
5. **Tests**: which existing tests survive, which get deleted, and what new tests at the interface look like.

Side effects happen inline as decisions crystallise:

- Naming a deepened module after a term not in the glossary, or sharpening a fuzzy term? Call `matt-domain-modeling` and add it to the Language section.
- The user rejects the candidate for a reason that passes the three tests in `matt-domain-modeling`? Record it through `matt-domain-modeling` and say so in one line.
- The user wants alternative interfaces? Use the sub-agent pattern in [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md).

The walk is done when all five answers are on the page and the outcome is stated there and in chat: proceed with this shape, or rejected and recorded.
