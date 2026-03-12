#!/usr/bin/env python3
"""Triage Cursor conversations by extracting headers from workspace databases.

Sorts conversations by code volume (linesAdded + linesRemoved) to find the richest
sessions worth deep-mining from global.vscdb.

Usage:
    python3 cursor_triage_headers.py <workspace.vscdb>
    python3 cursor_triage_headers.py /path/to/*.vscdb
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def extract_headers(db_path: str) -> list[dict]:
    """Extract conversation headers from a workspace database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
        ).fetchone()
        if not row:
            return []
        data = json.loads(row[0])
        return data.get("allComposers", [])
    except Exception as e:
        print(f"  ERROR reading {db_path}: {e}", file=sys.stderr)
        return []
    finally:
        conn.close()


def format_timestamp(unix_ms):
    if not unix_ms:
        return "unknown"
    try:
        return datetime.fromtimestamp(unix_ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def main():
    if len(sys.argv) < 2:
        print("Usage: cursor_triage_headers.py <workspace.vscdb> [...]")
        sys.exit(1)

    all_composers = []
    for db_path in sys.argv[1:]:
        p = Path(db_path)
        if not p.exists():
            print(f"SKIP: {db_path} not found", file=sys.stderr)
            continue
        headers = extract_headers(db_path)
        for h in headers:
            h["_source_db"] = p.name
        all_composers.extend(headers)
        print(f"  {p.name}: {len(headers)} conversations", file=sys.stderr)

    # Sort by total code churn
    all_composers.sort(
        key=lambda x: (x.get("totalLinesAdded", 0) + x.get("totalLinesRemoved", 0)),
        reverse=True,
    )

    print(f"\n{'='*120}")
    print(f"TOP CONVERSATIONS BY CODE VOLUME ({len(all_composers)} total)")
    print(f"{'='*120}")

    for c in all_composers[:80]:
        added = c.get("totalLinesAdded", 0)
        removed = c.get("totalLinesRemoved", 0)
        files = c.get("filesChangedCount", 0)
        ctx = c.get("contextUsagePercent", 0)
        mode = c.get("unifiedMode", "?")
        name = c.get("name", "unnamed")
        cid = c.get("composerId", "?")[:12]
        created = format_timestamp(c.get("createdAt"))
        source = c.get("_source_db", "?")[:40]

        print(
            f"{created}  {cid}  +{added:>5} -{removed:>5}  files:{files:>3}  "
            f"ctx:{ctx:>5.1f}%  {mode:>6}  {source:>40}  {name[:60]}"
        )

    # Also dump full JSON for programmatic use
    print(f"\n\n--- FULL JSON (top 80) ---", file=sys.stderr)
    top_json = []
    for c in all_composers[:80]:
        top_json.append({
            "composerId": c.get("composerId"),
            "name": c.get("name"),
            "createdAt": c.get("createdAt"),
            "lastUpdatedAt": c.get("lastUpdatedAt"),
            "totalLinesAdded": c.get("totalLinesAdded", 0),
            "totalLinesRemoved": c.get("totalLinesRemoved", 0),
            "filesChangedCount": c.get("filesChangedCount", 0),
            "contextUsagePercent": c.get("contextUsagePercent", 0),
            "unifiedMode": c.get("unifiedMode"),
            "_source_db": c.get("_source_db"),
        })
    # Print JSON to stderr so stdout stays human-readable
    print(json.dumps(top_json, indent=2), file=sys.stderr)


if __name__ == "__main__":
    main()
