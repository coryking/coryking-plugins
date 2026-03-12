#!/usr/bin/env python3
"""Extract daily AI usage stats from Cursor's global.vscdb.

Pulls aiCodeTracking.dailyStats entries for timeline of tab completions vs
composer suggestions, acceptance rates, etc.

Usage:
    python3 cursor_daily_stats.py [path/to/global.vscdb]
"""

import json
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = None  # Pass a .vscdb path as first argument


def main():
    if len(sys.argv) < 2:
        print("Usage: cursor_daily_stats.py <path-to-global.vscdb>", file=sys.stderr)
        sys.exit(1)
    db_path = sys.argv[1]

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT key, value FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats%' ORDER BY key"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No daily stats found.")
        return

    print(f"Found {len(rows)} daily stat entries\n")

    stats = []
    for key, value in rows:
        try:
            data = json.loads(value)
            stats.append(data)
        except json.JSONDecodeError:
            continue

    # Sort by date
    stats.sort(key=lambda x: x.get("date", ""))

    # Header
    print(f"{'Date':<12} {'Tab Suggest':>12} {'Tab Accept':>11} {'Tab Rate':>9} {'Comp Suggest':>13} {'Comp Accept':>12} {'Comp Rate':>10}")
    print("-" * 85)

    total_tab_suggest = 0
    total_tab_accept = 0
    total_comp_suggest = 0
    total_comp_accept = 0

    for s in stats:
        date = s.get("date", "?")
        ts = s.get("tabSuggestedLines", 0)
        ta = s.get("tabAcceptedLines", 0)
        cs = s.get("composerSuggestedLines", 0)
        ca = s.get("composerAcceptedLines", 0)

        tab_rate = f"{ta/ts*100:.0f}%" if ts > 0 else "-"
        comp_rate = f"{ca/cs*100:.0f}%" if cs > 0 else "-"

        total_tab_suggest += ts
        total_tab_accept += ta
        total_comp_suggest += cs
        total_comp_accept += ca

        # Only print days with activity
        if ts + ta + cs + ca > 0:
            print(f"{date:<12} {ts:>12} {ta:>11} {tab_rate:>9} {cs:>13} {ca:>12} {comp_rate:>10}")

    print("-" * 85)
    tab_total_rate = f"{total_tab_accept/total_tab_suggest*100:.0f}%" if total_tab_suggest > 0 else "-"
    comp_total_rate = f"{total_comp_accept/total_comp_suggest*100:.0f}%" if total_comp_suggest > 0 else "-"
    print(f"{'TOTAL':<12} {total_tab_suggest:>12} {total_tab_accept:>11} {tab_total_rate:>9} {total_comp_suggest:>13} {total_comp_accept:>12} {comp_total_rate:>10}")

    print(f"\n=== SUMMARY ===")
    print(f"Total lines suggested by tab completion: {total_tab_suggest}")
    print(f"Total lines accepted from tab completion: {total_tab_accept}")
    print(f"Total lines suggested by composer: {total_comp_suggest}")
    print(f"Total lines accepted from composer: {total_comp_accept}")
    print(f"Total AI-suggested lines: {total_tab_suggest + total_comp_suggest}")
    print(f"Total AI-accepted lines: {total_tab_accept + total_comp_accept}")

    if stats:
        print(f"\nDate range: {stats[0].get('date', '?')} → {stats[-1].get('date', '?')}")
        active_days = sum(1 for s in stats if (s.get("tabSuggestedLines", 0) + s.get("composerSuggestedLines", 0)) > 0)
        print(f"Active days: {active_days}")


if __name__ == "__main__":
    main()
