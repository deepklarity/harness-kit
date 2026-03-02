## 2026-03-02 - [Kanban column reordering optimization]
**Learning:** [Task ordering positions are managed explicitly through integer columns in the database. Updating tasks individually in a loop leads to an N+1 query pattern, which was observed to perform very poorly when columns have thousands of tasks]
**Action:** [Use Django's `bulk_update` instead of individual `update` queries inside loops for bulk-modifying attributes like `kanban_position`. Using batch sizing like `batch_size=500` ensures stable operation for arbitrary volumes without hitting database statement size limits]
