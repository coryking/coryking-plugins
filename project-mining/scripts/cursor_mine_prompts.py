#!/usr/bin/env python3
"""Mine user prompts from Cursor workspace databases.

Extracts user messages to AI from aiService.prompts across workspace DBs.
Searches for patterns: corrections, redirections, context-setting, domain knowledge,
methodology instructions, model opinions, frustration/pivots.

Usage:
    python3 cursor_mine_prompts.py <workspace.vscdb> [...]
    python3 cursor_mine_prompts.py /path/to/*.vscdb

    # Search for specific patterns
    python3 cursor_mine_prompts.py --search "stop,wrong,actually,no that" /path/to/*.vscdb
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Patterns that indicate interesting AI-directing behavior
BEHAVIOR_PATTERNS = {
    "corrections": [
        r"\bno[,.]?\s+(that'?s?\s+)?(wrong|incorrect|not right|not what)",
        r"\bstop\b", r"\bwait\b", r"\bhold on\b",
        r"\bactually[,.]?\s+(I|we|it|that|the|don't|let)",
        r"\bthat'?s not what I",
        r"\bI (said|meant|asked|wanted)",
        r"\byou'?re (wrong|confused|missing|ignoring)",
        r"\bdon'?t (do|change|touch|modify|add|remove|delete)",
    ],
    "redirections": [
        r"\binstead[,.]?\s",
        r"\blet'?s (try|go|do|switch|use|take)",
        r"\bdifferent approach",
        r"\bscrap (that|this|it)",
        r"\bforget (that|this|it|about)",
        r"\bstart over",
        r"\bnever ?mind",
    ],
    "context_setting": [
        r"\bfor context[,:]",
        r"\bbackground[,:]",
        r"\bhere'?s (what|how|the)",
        r"\bthe (architecture|system|design|pattern|convention) is",
        r"\bwe (use|have|run|deploy|store)",
        r"\bour (system|app|service|backend|frontend|scraper|pipeline)",
    ],
    "domain_knowledge": [
        # Add project-specific domain terms here
    ],
    "methodology": [
        r"\bwrite the test first",
        r"\bTDD\b",
        r"\bdon'?t (over.?engineer|gold.?plate|yagni)",
        r"\bKISS\b",
        r"\bkeep it simple",
        r"\bone thing at a time",
        r"\bstep by step",
        r"\bplan (first|before|it out)",
    ],
    "model_opinions": [
        r"\b(sonnet|opus|haiku|grok|gemini|gpt|claude)\b.*\b(better|worse|good|bad|fast|slow|smart|dumb)",
        r"\bswitch.*(model|to sonnet|to opus|to grok|to gemini)",
        r"\buse (sonnet|opus|haiku|grok|gemini|gpt)",
    ],
    "frustration": [
        r"\bthis (isn'?t|is not|doesn'?t) work",
        r"\bwhy (is|does|did|are|do) (it|this|that)",
        r"\bugh\b", r"\bffs\b", r"\bcome on\b",
        r"\bstill (broken|wrong|failing|not)",
        r"\bsame (error|problem|issue|bug)",
        r"\byou keep (doing|making|getting)",
    ],
}


def extract_prompts(db_path: str) -> list[dict]:
    """Extract user prompts from a workspace database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'aiService.prompts'"
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


def classify_prompt(text: str) -> dict[str, list[str]]:
    """Classify a prompt by behavior patterns. Returns {category: [matched_patterns]}."""
    matches = defaultdict(list)
    text_lower = text.lower()
    for category, patterns in BEHAVIOR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                matches[category].append(pattern)
    return dict(matches)


def main():
    parser = argparse.ArgumentParser(description="Mine Cursor user prompts for behavior patterns")
    parser.add_argument("databases", nargs="+", help="Workspace .vscdb files")
    parser.add_argument("--search", default=None, help="Comma-separated custom search terms")
    parser.add_argument("--all", action="store_true", help="Show all prompts, not just classified ones")
    parser.add_argument("--min-length", type=int, default=10, help="Minimum prompt length to consider")
    args = parser.parse_args()

    all_prompts = []
    for db_path in args.databases:
        p = Path(db_path)
        if not p.exists() or p.suffix != ".vscdb":
            continue
        prompts = extract_prompts(db_path)
        for prompt in prompts:
            prompt["_source"] = p.name
        all_prompts.extend(prompts)
        if prompts:
            print(f"  {p.name}: {len(prompts)} prompts", file=sys.stderr)

    print(f"\nTotal prompts across all workspaces: {len(all_prompts)}\n")

    # Custom search mode
    if args.search:
        terms = [t.strip().lower() for t in args.search.split(",")]
        print(f"=== CUSTOM SEARCH: {terms} ===\n")
        for prompt in all_prompts:
            text = prompt.get("text", "")
            if len(text) < args.min_length:
                continue
            text_lower = text.lower()
            matched = [t for t in terms if t in text_lower]
            if matched:
                source = prompt.get("_source", "?")
                cmd = "composer" if prompt.get("commandType") == 4 else "cmd-k"
                print(f"[{source}] [{cmd}] matched: {matched}")
                # Print first 300 chars, indented
                for line in text[:300].split("\n"):
                    print(f"  {line}")
                if len(text) > 300:
                    print(f"  [...{len(text)} chars total]")
                print()
        return

    # Pattern classification mode
    category_counts = Counter()
    category_examples = defaultdict(list)

    for prompt in all_prompts:
        text = prompt.get("text", "")
        if len(text) < args.min_length:
            continue

        classifications = classify_prompt(text)
        if not classifications and not args.all:
            continue

        for category in classifications:
            category_counts[category] += 1
            if len(category_examples[category]) < 15:  # Keep top 15 examples per category
                category_examples[category].append({
                    "text": text[:400],
                    "full_length": len(text),
                    "source": prompt.get("_source", "?"),
                    "categories": list(classifications.keys()),
                })

    # Output by category
    print(f"=== BEHAVIOR PATTERN SUMMARY ===\n")
    for category, count in category_counts.most_common():
        print(f"{category}: {count} prompts")
    print()

    for category, count in category_counts.most_common():
        examples = category_examples[category]
        print(f"\n{'='*80}")
        print(f"CATEGORY: {category} ({count} total, showing {len(examples)})")
        print(f"{'='*80}")
        for ex in examples:
            source = ex["source"]
            print(f"\n[{source}]")
            for line in ex["text"].split("\n"):
                print(f"  {line}")
            if ex["full_length"] > 400:
                print(f"  [...{ex['full_length']} chars total]")


if __name__ == "__main__":
    main()
