# 📋 Orchestration Dashboard — Self Notes

## 🗓 Started: 2026-02-15

---

## 🎯 Objective
Build a **Task Orchestration Dashboard** that visualizes how much time team members take to perform tasks, what the outcomes are, and tracks all mutations (status changes, assignee changes) over a task's lifecycle.

---

## 📊 Data Analysis (trello_boards_actions.json)

### Boards Found
1. **Bolt Hackathon** (`684d5ccf465413e7e6f9a5f3`) — Primary board with rich action data
2. **Welcome Board** (`59480a73c6872859536659e7`) — Trello default, less relevant

### Team Members
| Name | ID | Initials |
|------|-----|----------|
| dk-crazydiv (Kartik) | `5bfeb523003e5b8259dacf7a` | D |
| PriyamS | `59480a73c6872859536659e1` | P |
| Jatin | `624aec10a74fe64e9f4c0ae0` | J |

### Lists (Workflow Stages)
| List Name | ID |
|-----------|-----|
| To Do | `684d5ccf465413e7e6f9a5ee` |
| Doing | `684d5ccf465413e7e6f9a5ef` |
| Done 🎉 | `684d5ccf465413e7e6f9a5f0` |
| Testing | `684d5ccf465413e7e6f9a5f2` |

### Action Types Available
- `createCard` — Task creation
- `updateCard` — Status changes (list moves), description updates, position changes
- `addMemberToCard` — Assignee assignments
- `addMemberToBoard` — Board membership
- `copyBoard` — Board creation
- `addToOrganizationBoard` — Organization linkage
- `addAttachmentToCard` — File attachments

### Cards/Tasks Identified (Bolt Hackathon)
| # | Card Name | Assigned To |
|---|-----------|-------------|
| 1 | Timeline | — |
| 2 | Chrome History | PriyamS |
| 3 | Youtube History | — |
| 4 | Playstore app installs | Jatin |
| 5 | Fitbit data | — |
| 6 | Google maps reviews | dk-crazydiv |
| 7 | FIX: Double back needed for upload file workflow | PriyamS |
| 8 | FIX: Twice data upload for playstore | Jatin |
| 9 | Activity Insights bug fix | Jatin |
| 10 | Show table for spends and at other places | Jatin |
| 11 | Add wordcloud data and other graphs to timeline | dk-crazydiv |
| 12 | Remove 3 unimplemented tabs from UI | Jatin |

---

## 🏗 Architecture Decision

### Tech Stack
- **Vite + React + TypeScript** — Fast, modern, great DX
- **Chart.js / Recharts** — For data visualization (timeline, bar, pie charts)
- **Vanilla CSS** — Rich custom styling with dark mode, glassmorphism
- **Data Transformer Layer** — Platform-agnostic data transformation from Trello JSON

### Key Features to Build
1. **Dashboard Overview** — KPI cards with total tasks, members, completion rate
2. **Timeline View** — Horizontal timeline showing task lifecycle mutations
3. **Member Analytics** — Per-person task distribution, time spent
4. **Task Detail View** — Individual task mutation history
5. **Board Switcher** — Support multiple boards
6. **Activity Feed** — Chronological feed of all actions

### Data Transformation Strategy
```
Raw Trello JSON → Transformer → Normalized App State
                                 ├── boards[]
                                 ├── cards[] (with computed durations)
                                 ├── members[]
                                 ├── timelines[] (per-card mutation history)
                                 └── analytics (aggregated metrics)
```

---

## ✅ Checklist

- [x] Analyze data structure from `trello_boards_actions.json`
- [x] Identify team members, lists, cards
- [x] Choose tech stack (Vite + React + TypeScript)
- [ ] Initialize Vite project
- [ ] Build data transformer layer
- [ ] Create dashboard page with KPI cards
- [ ] Build timeline visualization
- [ ] Build member analytics charts
- [ ] Build task detail/mutation view
- [ ] Add activity feed
- [ ] Polish with animations, dark mode, glassmorphism
- [ ] Test and verify

---

## 🔄 Change Log
| Date | Change |
|------|--------|
| 2026-02-15 | Initial project analysis, notes creation, project setup |
