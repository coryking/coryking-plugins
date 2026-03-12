#!/usr/bin/env python3
"""Quick search across all Cursor user prompts for a keyword/phrase.

Lightweight — just searches aiService.prompts across all workspace databases.
Designed for rapid "does this pattern exist?" checks before deeper mining.

Usage:
    python3 cursor_search_prompts.py "search term" /path/to/*.vscdb
    python3 cursor_search_prompts.py "you're wrong" /path/to/*.vscdb
"""

import json
import sqlite3
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print("Usage: cursor_search_prompts.py <search_term> <workspace.vscdb> [...]")
        sys.exit(1)

    search_term = sys.argv[1].lower()
    db_paths = sys.argv[2:]

    total_hits = 0
    total_prompts = 0

    for db_path in db_paths:
        p = Path(db_path)
        if not p.exists() or p.suffix != ".vscdb":
            continue

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = 'aiService.prompts'"
            ).fetchone()
            conn.close()

            if not row:
                continue

            prompts = json.loads(row[0])
            if not isinstance(prompts, list):
                continue

            total_prompts += len(prompts)

            hits = []
            for prompt in prompts:
                text = prompt.get("text", "")
                if search_term in text.lower():
                    hits.append(text)

            if hits:
                print(f"\n=== {p.name} ({len(hits)} hits) ===")
                for hit in hits:
                    # Show context around the match
                    text_lower = hit.lower()
                    idx = text_lower.find(search_term)
                    start = max(0, idx - 80)
                    end = min(len(hit), idx + len(search_term) + 80)
                    snippet = hit[start:end].replace("\n", " ")
                    if start > 0:
                        snippet = "..." + snippet
                    if end < len(hit):
                        snippet = snippet + "..."
                    print(f"  > {snippet}")
                    total_hits += 1

        except Exception as e:
            print(f"  ERROR: {p.name}: {e}", file=sys.stderr)

    print(f"\n---\nSearched {total_prompts} prompts, found {total_hits} hits for '{search_term}'")


if __name__ == "__main__":
    main()
