#!/usr/bin/env python3
"""Pull full conversation bodies from Cursor's global.vscdb by composerId.

Takes one or more composer UUIDs (from triage step) and extracts the full
turn-by-turn conversation. Outputs human-readable transcript and/or JSON.

Usage:
    python3 cursor_pull_conversation.py <composerId> [composerId2 ...]
    python3 cursor_pull_conversation.py f409c14d-4a32-... --json
    python3 cursor_pull_conversation.py --ids-from-file top_ids.txt

    # Pipe from triage script
    python3 cursor_triage_headers.py foo.vscdb 2>/dev/null | head -5 | awk '{print $2}' | \
        python3 cursor_pull_conversation.py --ids-from-stdin

Pass the path to global.vscdb via --db flag or set CURSOR_GLOBAL_DB env var.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

GLOBAL_DB = Path.home() / "projects" / "cursor-chat-export" / "global.vscdb"


def pull_conversation(conn, composer_id: str) -> dict | None:
    """Pull a single conversation from global.vscdb."""
    # composerData keys use full UUID, but we might have a prefix
    if len(composer_id) < 36:
        # Prefix search
        row = conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?",
            (f"composerData:{composer_id}%",),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key = ?",
            (f"composerData:{composer_id}",),
        ).fetchone()

    if not row:
        return None

    try:
        return json.loads(row[1])
    except json.JSONDecodeError as e:
        print(f"  JSON decode error for {composer_id}: {e}", file=sys.stderr)
        return None


def extract_bubble_text(conn, composer_id: str, bubble_id: str) -> str | None:
    """Pull individual bubble content if needed."""
    key = f"bubbleId:{composer_id}:{bubble_id}"
    row = conn.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?", (key,)
    ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
        # Bubble structure varies — try common fields
        return data.get("text", data.get("content", data.get("message", "")))
    except Exception:
        return None


def format_conversation(data: dict, conn) -> str:
    """Format a conversation into a human-readable transcript."""
    lines = []
    composer_id = data.get("composerId", "unknown")
    lines.append(f"=== CONVERSATION: {composer_id} ===")
    lines.append(f"Name: {data.get('name', data.get('text', 'unnamed'))}")

    conversation = data.get("conversation", [])
    lines.append(f"Turns: {len(conversation)}")
    lines.append("")

    for i, turn in enumerate(conversation):
        turn_type = turn.get("type", "?")
        role = "USER" if turn_type == 1 else "ASSISTANT" if turn_type == 2 else f"TYPE_{turn_type}"
        bubble_id = turn.get("bubbleId", "")

        lines.append(f"--- Turn {i+1} [{role}] bubble:{bubble_id[:8]} ---")

        # Try to get text from the turn itself
        text = turn.get("text", "")

        # If no text in turn, try pulling the bubble
        if not text and bubble_id:
            bubble_text = extract_bubble_text(conn, composer_id, bubble_id)
            if bubble_text:
                if isinstance(bubble_text, str):
                    text = bubble_text
                elif isinstance(bubble_text, dict):
                    text = json.dumps(bubble_text, indent=2)[:2000]

        if text:
            # Truncate very long turns but keep enough to be useful
            if len(text) > 2000:
                text = text[:1000] + f"\n\n[...truncated, {len(text)} chars total...]\n\n" + text[-500:]
            for line in text.split("\n"):
                lines.append(f"  {line}")
        else:
            lines.append(f"  [no text in turn data — check bubble {bubble_id}]")

        # Note interesting metadata
        relevant_files = turn.get("relevantFiles", [])
        if relevant_files:
            lines.append(f"  [files in context: {', '.join(relevant_files[:10])}]")

        human_changes = turn.get("humanChanges", [])
        if human_changes:
            lines.append(f"  [HUMAN CHANGES between turns: {len(human_changes)} diffs]")
            for hc in human_changes[:3]:
                if isinstance(hc, dict):
                    lines.append(f"    file: {hc.get('fileName', '?')}")
                    diff = hc.get("diff", hc.get("changes", ""))
                    if diff and isinstance(diff, str):
                        lines.append(f"    diff: {diff[:300]}")

        cursor_rules = turn.get("cursorRules", [])
        if cursor_rules:
            lines.append(f"  [.cursorrules active: {len(cursor_rules)} rules]")

        is_agentic = turn.get("isAgentic", False)
        if is_agentic:
            lines.append(f"  [agentic mode]")

        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Pull full Cursor conversations from global.vscdb")
    parser.add_argument("ids", nargs="*", help="Composer ID(s) or prefixes")
    parser.add_argument("--ids-from-stdin", action="store_true", help="Read IDs from stdin, one per line")
    parser.add_argument("--ids-from-file", default=None, help="Read IDs from a file")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of transcript")
    parser.add_argument("--db", default=str(GLOBAL_DB), help="Path to global.vscdb")
    args = parser.parse_args()

    # Collect IDs
    ids = list(args.ids)
    if args.ids_from_stdin:
        for line in sys.stdin:
            line = line.strip()
            if line:
                ids.append(line)
    if args.ids_from_file:
        with open(args.ids_from_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.append(line)

    if not ids:
        print("No composer IDs provided", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        for cid in ids:
            data = pull_conversation(conn, cid)
            if not data:
                print(f"NOT FOUND: {cid}", file=sys.stderr)
                continue

            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(format_conversation(data, conn))
                print("\n" + "=" * 120 + "\n")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
