# Agent Prompts for Each Principle Audit

Each section below is the full prompt to pass to an Explore sub-agent. Copy the entire section for the relevant principle.

---

## Agent 1: Action Parity

Audit for ACTION PARITY — "Whatever the user can do, the agent can do."

Tasks:
1. Enumerate ALL user actions in the frontend (API calls, button clicks, form submissions)
   - Search for API service files, fetch calls, form handlers
   - Check routes and components for user interactions
2. Check which have corresponding agent tools
   - Search for agent tool definitions
   - Map user actions to agent capabilities
3. Score: "Agent can do X out of Y user actions"

Output format:

```
## Action Parity Audit

### User Actions Found

| Action | Location | Agent Tool | Status |
|--------|----------|------------|--------|

### Score: X/Y (percentage%)

### Missing Agent Tools
- ...

### Recommendations
- ...
```

---

## Agent 2: Tools as Primitives

Audit for TOOLS AS PRIMITIVES — "Tools provide capability, not behavior."

Tasks:
1. Find and read ALL agent tool files
2. Classify each as:
   - PRIMITIVE (good): read, write, store, list — enables capability without business logic
   - WORKFLOW (bad): encodes business logic, makes decisions, orchestrates steps
3. Score: "X out of Y tools are proper primitives"

Output format:

```
## Tools as Primitives Audit

### Tool Analysis

| Tool | File | Type | Reasoning |
|------|------|------|-----------|

### Score: X/Y (percentage%)

### Problematic Tools (workflows that should be primitives)
- ...

### Recommendations
- ...
```

---

## Agent 3: Context Injection

Audit for CONTEXT INJECTION — "System prompt includes dynamic context about app state."

Tasks:
1. Find context injection code (search for "context", "system prompt", "inject")
2. Read agent prompts and system messages
3. Enumerate what IS injected vs what SHOULD be:
   - Available resources (files, drafts, documents)
   - User preferences/settings
   - Recent activity
   - Available capabilities listed
   - Session history
   - Workspace state

Output format:

```
## Context Injection Audit

### Context Types Analysis

| Context Type | Injected? | Location | Notes |
|--------------|-----------|----------|-------|

### Score: X/Y (percentage%)

### Missing Context
- ...

### Recommendations
- ...
```

---

## Agent 4: Shared Workspace

Audit for SHARED WORKSPACE — "Agent and user work in the same data space."

Tasks:
1. Identify all data stores/tables/models
2. Check if agents read/write to the SAME tables or separate ones
3. Look for sandbox isolation anti-pattern (agent has separate data space)

Output format:

```
## Shared Workspace Audit

### Data Store Analysis

| Data Store | User Access | Agent Access | Shared? |
|------------|-------------|--------------|---------|

### Score: X/Y (percentage%)

### Isolated Data (anti-pattern)
- ...

### Recommendations
- ...
```

---

## Agent 5: CRUD Completeness

Audit for CRUD COMPLETENESS — "Every entity has full CRUD."

Tasks:
1. Identify all entities/models in the codebase
2. For each entity, check if agent tools exist for:
   - Create
   - Read
   - Update
   - Delete
3. Score per entity and overall

Output format:

```
## CRUD Completeness Audit

### Entity CRUD Analysis

| Entity | Create | Read | Update | Delete | Score |
|--------|--------|------|--------|--------|-------|

### Overall Score: X/Y entities with full CRUD (percentage%)

### Incomplete Entities (list missing operations)
- ...

### Recommendations
- ...
```

---

## Agent 6: UI Integration

Audit for UI INTEGRATION — "Agent actions immediately reflected in UI."

Tasks:
1. Check how agent writes/changes propagate to the frontend
2. Look for:
   - Streaming updates (SSE, WebSocket)
   - Polling mechanisms
   - Shared state/services
   - Event buses
   - File watching
3. Identify "silent actions" anti-pattern (agent changes state but UI doesn't update)

Output format:

```
## UI Integration Audit

### Agent Action to UI Update Analysis

| Agent Action | UI Mechanism | Immediate? | Notes |
|--------------|--------------|------------|-------|

### Score: X/Y (percentage%)

### Silent Actions (anti-pattern)
- ...

### Recommendations
- ...
```

---

## Agent 7: Capability Discovery

Audit for CAPABILITY DISCOVERY — "Users can discover what the agent can do."

Tasks:
1. Check for these 7 discovery mechanisms:
   - Onboarding flow showing agent capabilities
   - Help documentation
   - Capability hints in UI
   - Agent self-describes in responses
   - Suggested prompts/actions
   - Empty state guidance
   - Slash commands (/help, /tools)
2. Score against 7 mechanisms

Output format:

```
## Capability Discovery Audit

### Discovery Mechanism Analysis

| Mechanism | Exists? | Location | Quality |
|-----------|---------|----------|---------|

### Score: X/7 (percentage%)

### Missing Discovery
- ...

### Recommendations
- ...
```

---

## Agent 8: Prompt-Native Features

Audit for PROMPT-NATIVE FEATURES — "Features are prompts defining outcomes, not code."

Tasks:
1. Read all agent prompts
2. Classify each feature/behavior as defined in:
   - PROMPT (good): outcomes defined in natural language
   - CODE (bad): business logic hardcoded
3. Check if behavior changes require prompt edit vs code change

Output format:

```
## Prompt-Native Features Audit

### Feature Definition Analysis

| Feature | Defined In | Type | Notes |
|---------|------------|------|-------|

### Score: X/Y (percentage%)

### Code-Defined Features (anti-pattern)
- ...

### Recommendations
- ...
```
