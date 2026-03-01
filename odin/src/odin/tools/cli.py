"""CLI entry point for taskit-tool.

Usage:
    taskit-tool comment "progress update"
    taskit-tool ask "How should I handle auth?" --wait --timeout 300
    taskit-tool proof --summary "All tests pass" --steps '["pytest -v"]' --files '["src/auth.py"]'
    taskit-tool proof --summary "Implemented login" --handover "Session tokens in Redis"
    taskit-tool context
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import client_from_env


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskit-tool",
        description="Communicate with the TaskIt task board during execution.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # comment
    p_comment = sub.add_parser("comment", help="Post a progress comment")
    p_comment.add_argument("content", help="Comment text")

    # ask
    p_ask = sub.add_parser("ask", help="Ask a question")
    p_ask.add_argument("content", help="Question text")
    p_ask.add_argument("--wait", action="store_true", help="Wait for a reply")
    p_ask.add_argument("--timeout", type=int, default=300, help="Wait timeout in seconds")

    # proof
    p_proof = sub.add_parser("proof", help="Submit proof of work")
    p_proof.add_argument("--summary", required=True, help="Summary of work done")
    p_proof.add_argument("--steps", help="JSON array of verification steps")
    p_proof.add_argument("--files", help="JSON array of files changed")
    p_proof.add_argument("--handover", help="Handover notes for next agent/human")

    # context
    sub.add_parser("context", help="Get task details and upstream context")

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        client = client_from_env()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "comment":
            result = client.post_comment(args.content)
            print(json.dumps({"ok": True, "comment_id": result["id"]}))

        elif args.command == "ask":
            result = client.ask_question(
                args.content, wait=args.wait, timeout=args.timeout
            )
            output = {"ok": True, "comment_id": result["id"]}
            if args.wait:
                reply = result.get("reply")
                output["reply"] = reply
                if reply:
                    print(f"Reply: {reply}", file=sys.stderr)
                else:
                    print("No reply received (timed out)", file=sys.stderr)
            print(json.dumps(output))

        elif args.command == "proof":
            steps = json.loads(args.steps) if args.steps else None
            files = json.loads(args.files) if args.files else None
            result = client.submit_proof(
                summary=args.summary,
                steps=steps,
                files=files,
                handover=args.handover,
            )
            print(json.dumps({"ok": True, "comment_id": result["id"]}))

        elif args.command == "context":
            result = client.get_context()
            print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
