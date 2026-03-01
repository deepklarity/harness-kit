"""Manual test: exercises comment types against a live backend.

Usage:
    python test_comment_taxonomy.py <task_id> [base_url]

Requires: httpx (pip install httpx)

Comment types: status_update, question, reply
Attachment types: proof, file, debug:*, trace:*
"""

import sys
import json

import httpx


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <task_id> [base_url]")
        sys.exit(1)

    task_id = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
    client = httpx.Client(base_url=base, timeout=30)

    print(f"=== Comment Taxonomy Manual Test ===")
    print(f"Task: {task_id} | Base: {base}\n")

    # 1. POST status_update (default)
    print("--- 1. POST status_update (default) ---")
    r = client.post(f"/tasks/{task_id}/comments/", json={
        "author_email": "test@example.com",
        "content": "Status update test",
    })
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    assert r.json()["comment_type"] == "status_update", "Expected status_update"
    print()

    # 2. POST status_update via MCP-style agent email
    print("--- 2. POST status_update (agent via MCP) ---")
    r = client.post(f"/tasks/{task_id}/comments/", json={
        "author_email": "claude@odin.agent",
        "author_label": "claude",
        "content": "Starting implementation of auth module.",
        "comment_type": "status_update",
    })
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    assert r.json()["comment_type"] == "status_update", "Expected status_update"
    print()

    # 3. POST proof of work
    print("--- 3. POST proof of work (via attachment) ---")
    r = client.post(f"/tasks/{task_id}/comments/", json={
        "author_email": "claude@odin.agent",
        "author_label": "claude",
        "content": "Proof: All 15 tests pass, auth module complete.",
        "attachments": [{"type": "proof", "summary": "All 15 tests pass", "files": ["tests/output.log"]}],
    })
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    print()

    # 4. POST question
    print("--- 4. POST question ---")
    r = client.post(f"/tasks/{task_id}/question/", json={
        "author_email": "agent@odin.agent",
        "content": "What database should I use?",
    })
    r.raise_for_status()
    q_data = r.json()
    q_id = q_data["id"]
    print(json.dumps(q_data, indent=2))
    assert q_data["comment_type"] == "question", "Expected question"
    print(f"Question ID: {q_id}\n")

    # 5. POST reply
    print(f"--- 5. POST reply to question {q_id} ---")
    r = client.post(f"/tasks/{task_id}/comments/{q_id}/reply/", json={
        "author_email": "human@example.com",
        "content": "Use PostgreSQL.",
    })
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    assert r.json()["comment_type"] == "reply", "Expected reply"
    print()

    # 6. Filter by type: question
    print("--- 6. Filter: ?type=question ---")
    r = client.get(f"/tasks/{task_id}/comments/", params={"type": "question"})
    r.raise_for_status()
    results = r.json()["results"]
    print(f"  Found {len(results)} question(s)")
    for c in results:
        assert c["comment_type"] == "question"
        print(f"  #{c['id']} | {c['content'][:60]}")
    print()

    # 7. All comments — verify each has comment_type
    print("--- 7. All comments (verify comment_type field) ---")
    r = client.get(f"/tasks/{task_id}/comments/")
    r.raise_for_status()
    results = r.json()["results"]
    for c in results:
        ct = c.get("comment_type", "???")
        print(f"  #{c['id']} | type={ct:15s} | {c['content'][:60]}")
        assert "comment_type" in c, f"Comment #{c['id']} missing comment_type"
    print()

    print("=== All checks passed ===")


if __name__ == "__main__":
    main()
