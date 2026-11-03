# Users flow

A request to `/users/<id>` lands in `get_user` in `app.py:7`, which returns the id.
The schema lives in `schema.sql:1`. Rows are read by `load_user()` in `app.py`.
The old handler sat in `legacy/users.py:12`.
