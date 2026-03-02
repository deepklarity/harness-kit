1. **TaskDetailModal** (Area)
   - Issue: The header has unnecessary white space, the sidebar is too wide (320px) with many borders, making it dense but spread out.
   - Suggested Improvement: Reduce sidebar width to 280px, group related metadata tighter, use lighter typography for labels, and simplify header padding.
   - Why It's Better: Reduces visual clutter and makes scanning the task details faster.

2. **TaskCard** (Area)
   - Issue: Information in both full and compact layouts is slightly cluttered with redundant badges/spacing.
   - Suggested Improvement: Combine layout rows slightly, reduce spacing, and make badges more minimal.
   - Why It's Better: Fits more cards on screen, faster to scan.

3. **KanbanBoard** (Area)
   - Issue: Column headers are bulky, borders are thick, taking up 280px.
   - Suggested Improvement: Reduce column width to 260px, use lighter padding/backgrounds for headers, and reduce border prominence.
   - Why It's Better: Cleaner structural layout, less cognitive load.

I will proceed to implement the changes across these components, primarily focusing on `TaskDetailModal`, `TaskCard`, and `KanbanBoard`.
