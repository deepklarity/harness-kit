"""End-to-end test: full MCP question/reply flow.

Simulates an agent asking a question and a human replying via the API.
Verifies the has_pending_question metadata flag lifecycle.

Usage:
    python test_question_flow_e2e.py <task_id> [base_url]

Requires: httpx (pip install httpx)
"""

import sys
import time

import httpx


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <task_id> [base_url]")
        sys.exit(1)

    task_id = sys.argv[1]
    base = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
    client = httpx.Client(base_url=base, timeout=30)

    print(f"=== Question/Reply E2E Flow ===")
    print(f"Task: {task_id} | Base: {base}\n")

    # Step 1: Post a question
    print("1. Agent posts a blocking question...")
    r = client.post(f"/tasks/{task_id}/question/", json={
        "author_email": "claude+sonnet@odin.agent",
        "author_label": "claude (sonnet)",
        "content": "Should I use REST or GraphQL for the API?",
    })
    r.raise_for_status()
    question = r.json()
    q_id = question["id"]
    print(f"   Question created: #{q_id}")
    assert question["comment_type"] == "question"
    print(f"   comment_type: {question['comment_type']}")

    # Step 2: Verify has_pending_question flag is set
    print("\n2. Checking has_pending_question flag...")
    r = client.get(f"/tasks/{task_id}/detail/")
    r.raise_for_status()
    detail = r.json()
    assert detail["metadata"].get("has_pending_question") is True, "Expected has_pending_question=True"
    print(f"   metadata.has_pending_question: {detail['metadata']['has_pending_question']}")

    # Step 3: Simulate agent polling (what the MCP tool does)
    print("\n3. Simulating agent poll for reply...")
    r = client.get(f"/tasks/{task_id}/comments/", params={"after": q_id})
    r.raise_for_status()
    results = r.json()["results"]
    print(f"   Replies found: {len(results)} (expected 0, no reply yet)")
    assert len(results) == 0

    # Step 4: Human replies
    print("\n4. Human replies via the board...")
    r = client.post(f"/tasks/{task_id}/comments/{q_id}/reply/", json={
        "author_email": "human@example.com",
        "author_label": "Dan",
        "content": "Use REST. Keep it simple.",
    })
    r.raise_for_status()
    reply = r.json()
    print(f"   Reply created: #{reply['id']}")
    assert reply["comment_type"] == "reply"
    print(f"   comment_type: {reply['comment_type']}")

    # Step 5: Agent polls again — finds reply
    print("\n5. Agent polls again...")
    r = client.get(f"/tasks/{task_id}/comments/", params={"after": q_id})
    r.raise_for_status()
    results = r.json()["results"]
    replies = [c for c in results if c.get("comment_type") == "reply"]
    print(f"   Replies found: {len(replies)}")
    assert len(replies) == 1
    print(f"   Reply content: {replies[0]['content']}")

    # Step 6: Verify has_pending_question flag is cleared
    print("\n6. Checking has_pending_question flag cleared...")
    r = client.get(f"/tasks/{task_id}/detail/")
    r.raise_for_status()
    detail = r.json()
    has_pending = detail["metadata"].get("has_pending_question", False)
    assert has_pending is False, f"Expected has_pending_question=False, got {has_pending}"
    print(f"   metadata.has_pending_question: {has_pending}")

    # Step 7: Verify question attachment shows "answered"
    print("\n7. Checking question status in attachments...")
    question_comments = [c for c in detail["comments"] if c["id"] == q_id]
    assert len(question_comments) == 1
    q_att = question_comments[0]["attachments"][0]
    assert q_att["status"] == "answered", f"Expected 'answered', got '{q_att['status']}'"
    print(f"   Question attachment status: {q_att['status']}")

    print("\n=== All checks passed ===")


if __name__ == "__main__":
    main()
