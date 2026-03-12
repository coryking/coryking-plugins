#!/usr/bin/env python3
"""Extract model usage patterns from Cursor workspace databases.

Mines aiService.generations for model identifiers, timestamps, and usage patterns.
Cross-references with conversation headers to determine which model was used where.

Usage:
    python3 cursor_model_usage.py /path/to/*.vscdb
"""

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def extract_generations(db_path: str) -> list[dict]:
    """Extract generation metadata from a workspace database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'aiService.generations'"
        ).fetchone()
        if not row:
            return []
        data = json.loads(row[0])
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"  ERROR reading {db_path}: {e}", file=sys.stderr)
        return []
    finally:
        conn.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: cursor_model_usage.py <workspace.vscdb> [...]")
        sys.exit(1)

    all_generations = []
    for db_path in sys.argv[1:]:
        p = Path(db_path)
        if not p.exists() or p.suffix != ".vscdb":
            continue
        gens = extract_generations(db_path)
        for g in gens:
            g["_source"] = p.name
        all_generations.extend(gens)
        if gens:
            print(f"  {p.name}: {len(gens)} generations", file=sys.stderr)

    print(f"Total generations: {len(all_generations)}\n")

    if not all_generations:
        print("No generation data found.")
        return

    # Analyze all available fields to discover model info
    print("=== FIELD INVENTORY ===")
    field_counts = Counter()
    for g in all_generations:
        for key in g.keys():
            if not key.startswith("_"):
                field_counts[key] += 1
    for field, count in field_counts.most_common():
        print(f"  {field}: {count}")
    print()

    # Look for model-related fields — Cursor's schema may vary
    model_fields = ["model", "modelName", "modelId", "provider", "aiProvider"]
    for mf in model_fields:
        values = Counter()
        for g in all_generations:
            v = g.get(mf)
            if v:
                values[str(v)] += 1
        if values:
            print(f"=== {mf} values ===")
            for val, count in values.most_common():
                print(f"  {val}: {count}")
            print()

    # Type breakdown
    type_counts = Counter()
    for g in all_generations:
        type_counts[g.get("type", "unknown")] += 1
    print(f"=== Generation types ===")
    for t, c in type_counts.most_common():
        print(f"  {t}: {c}")
    print()

    # Timeline — when was Cursor being used?
    dates = []
    for g in all_generations:
        ts = g.get("unixMs")
        if ts:
            try:
                dates.append(datetime.fromtimestamp(ts / 1000))
            except Exception:
                pass

    if dates:
        dates.sort()
        print(f"=== USAGE TIMELINE ===")
        print(f"First generation: {dates[0].strftime('%Y-%m-%d %H:%M')}")
        print(f"Last generation: {dates[-1].strftime('%Y-%m-%d %H:%M')}")
        print(f"Span: {(dates[-1] - dates[0]).days} days")
        print()

        # Activity by month
        monthly = Counter()
        for d in dates:
            monthly[d.strftime("%Y-%m")] += 1
        print("Activity by month:")
        for month, count in sorted(monthly.items()):
            bar = "#" * min(count // 5, 60)
            print(f"  {month}: {count:>5} {bar}")
        print()

        # Activity by day of week
        dow = Counter()
        for d in dates:
            dow[d.strftime("%A")] += 1
        print("Activity by day of week:")
        for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
            count = dow.get(day, 0)
            print(f"  {day}: {count}")
        print()

    # Per-workspace breakdown
    print(f"=== PER-WORKSPACE ===")
    by_source = defaultdict(int)
    for g in all_generations:
        by_source[g.get("_source", "?")] += 1
    for source, count in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {source}: {count}")

    # Dump sample records for schema discovery
    print(f"\n=== SAMPLE RECORDS (first 5) ===")
    for g in all_generations[:5]:
        cleaned = {k: v for k, v in g.items() if not k.startswith("_")}
        # Truncate long values
        for k, v in cleaned.items():
            if isinstance(v, str) and len(v) > 200:
                cleaned[k] = v[:200] + "..."
        print(json.dumps(cleaned, indent=2, default=str))
        print()


if __name__ == "__main__":
    main()
