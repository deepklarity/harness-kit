# Prompts for the eight principle audits

Each sub-agent (Agent tool, `subagent_type: general-purpose`, `model: sonnet`) gets the output
shape below plus one principle's section. Pass both verbatim.

## Output shape, the same for every principle

Return markdown in this order:

1. `## <Principle> audit`
2. An inventory table with the columns the principle's section names, one row per item found,
   each row naming the file it came from.
3. `### Coverage: X/Y` where X is the count that meets the principle and Y the total
   inventoried.
4. `### Gaps`: one bullet per item that does not meet it, with the file and a severity 1 to 5
   (5 worst: the agent cannot do a whole class of what the user can, or corrupts shared data;
   4 blocks a common job; 3 the agent works around it at a cost; 2 inconsistent or rare;
   1 a nit).
5. `### What to change`: one bullet per change, highest severity first, naming the file.

Plain sentences throughout; the report goes on a page as written.

---

## 1. Action parity

Whatever the user can do, the agent can do.

1. Enumerate every user action in the frontend: API calls, button clicks, form submissions.
   Search the API service files, fetch calls, form handlers, routes and components.
2. Find the agent tool definitions and map each user action to the tool that does the same.
3. Coverage: actions the agent can do out of actions the user can do.

Columns: Action, Location, Agent tool, Covered.

---

## 2. Tools as primitives

Tools provide capability, not behaviour.

1. Find and read every agent tool file.
2. Classify each as a primitive (read, write, store, list: capability with no business
   logic) or a workflow (encodes business logic, makes decisions, orchestrates steps).
3. Coverage: primitives out of tools.

Columns: Tool, File, Kind, Reason.

---

## 3. Context injection

The system prompt carries live app state.

1. Find the context injection code (search for "context", "system prompt", "inject") and read
   the agent prompts and system messages.
2. For each of these, say whether it is injected and where: available resources (files,
   drafts, documents), user preferences and settings, recent activity, the capability list,
   session history, workspace state.
3. Coverage: injected out of six.

Columns: Context type, Injected, Location, Notes.

---

## 4. Shared workspace

Agent and user work on the same data.

1. List every data store, table and model.
2. For each, check whether agent and user read and write the same rows or the agent has a
   separate space.
3. Coverage: shared stores out of stores.

Columns: Data store, User access, Agent access, Shared.

---

## 5. CRUD completeness

Every entity has create, read, update and delete.

1. List every entity or model.
2. For each, check whether an agent tool exists for create, read, update and delete.
3. Coverage: entities with all four out of entities.

Columns: Entity, Create, Read, Update, Delete.

---

## 6. UI integration

Agent actions show up in the UI at once.

1. Trace how an agent write reaches the frontend: streaming (SSE, WebSocket), polling, shared
   state or services, event buses, file watching.
2. List every agent action that changes state and the mechanism, if any, that updates the UI.
3. Coverage: actions the UI reflects at once out of agent actions.

Columns: Agent action, UI mechanism, Immediate, Notes.

---

## 7. Capability discovery

Users can find out what the agent can do.

1. Check for each of these seven: an onboarding flow showing agent capabilities, help
   documentation, capability hints in the UI, the agent describing itself in responses,
   suggested prompts or actions, empty-state guidance, slash commands (`/help`, `/tools`).
2. Coverage: present out of seven.

Columns: Mechanism, Exists, Location, Notes.

---

## 8. Prompt-native features

Features are prompts defining outcomes, not code.

1. Read every agent prompt.
2. Classify each feature or behaviour as defined in a prompt (the outcome is stated in natural
   language) or in code (the business logic is hardcoded), and say whether changing it needs a
   prompt edit or a code change.
3. Coverage: prompt-defined out of features.

Columns: Feature, Defined in, Change needs, Notes.
