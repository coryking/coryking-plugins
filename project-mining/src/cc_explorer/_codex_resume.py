"""Codex rollout -> conversation-turn extraction — VENDORED from codex-resume.

Turns a Codex rollout's ``response_item`` records into alternating user/assistant
text turns: Codex tool calls become ``[Codex tool: <name>]`` text blocks (input and
output truncated), injected context (environment, AGENTS.md, skills) is dropped
from user messages, encrypted reasoning is dropped, consecutive same-role items
merge into one turn, and the oldest turns fall off past a total size budget.
conversion.py wraps the resulting turns in Claude Code subagent-transcript lines.

═══════════════════════════════════════════════════════════════════════════════
PROVENANCE — read this before editing.
═══════════════════════════════════════════════════════════════════════════════
Source repo : github.com/ostiums/codex-resume
Source file : codex_resume.py
Vendored at : commit 126c922ef4a4a7faffc60f50f860b0102c621c3f (2026-09-24)
License     : MIT (notice reproduced below, as the license requires)

Only the parse/render layer is vendored: the constants, ``Item``/``Turn``,
``truncate`` through ``build_turns``. Upstream's discovery, fzf picker, autosync
hook and state file are not — cc-explorer's corpus does discovery, and
conversion.py does the writing. The vendored code is COPIED VERBATIM (names,
bodies, comments) so a diff against upstream stays mechanical. Do not "improve"
it here; cc-explorer-specific behavior belongs in conversion.py.

HOW TO CHECK FOR UPDATES:
  gh api repos/ostiums/codex-resume/contents/codex_resume.py --jq .content \
    | base64 -d > /tmp/upstream_codex_resume.py
Diff the symbols below against upstream; port changes and bump "Vendored at".

───────────────────────────────────────────────────────────────────────────────
MIT License

Copyright (c) 2026 Vladimir Berestnev

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

TOOL_INPUT_LIMIT = 1000
TOOL_OUTPUT_LIMIT = 2000
MESSAGE_LIMIT = 8000  # per user/assistant message
TOTAL_LIMIT = 400_000  # whole imported history; oldest turns are dropped beyond this
IMAGE_MAX_CHARS = 5 * 1024 * 1024  # base64 payload cap per image (Claude API limit is 5 MB)
IMAGE_DATA_URL = re.compile(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)\Z")
LEAD_USER_TEXT = "[Continuing a chat from Codex]"
NOISE_PREFIXES = (
    "<environment_context>", "<app-context>", "<recommended_plugins>",
    "<guardian_tool_descriptions>", "<user_instructions>", "<INSTRUCTIONS>",
    "<skill>", "<turn_aborted>", "# AGENTS.md instructions",
)

# ---------------------------------------------------------------- parse


@dataclass
class Item:
    role: str  # "user" | "assistant" | "tool"
    text: str  # message text, or tool input for role == "tool"
    ts: str | None
    name: str = ""
    output: str | None = None
    images: list[dict] = field(default_factory=list)  # Claude image blocks (user messages only)


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    parts: list[str]
    ts: str | None
    images: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.parts)


def truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"…[truncated, {len(s)} chars]"


def image_block(url) -> dict | None:
    """Codex `input_image` data URL → Claude image block; None if unusable (then only a text marker remains)."""
    m = IMAGE_DATA_URL.match(url) if isinstance(url, str) else None
    if not m or len(m.group(2)) > IMAGE_MAX_CHARS:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}


def _message_content(payload: dict) -> tuple[str, list[dict]]:
    role = payload.get("role")
    parts, images = [], []
    for c in payload.get("content") or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "input_image":
            parts.append("[image]")
            block = image_block(c.get("image_url")) if role == "user" else None
            if block:
                images.append(block)
            continue
        text = c.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        if role == "user" and text.lstrip().startswith(NOISE_PREFIXES):
            continue
        parts.append(text)
    return "\n\n".join(parts), images


def _tool_input(payload: dict) -> str:
    if payload.get("type") == "custom_tool_call":
        return str(payload.get("input") or "")
    raw = payload.get("arguments") or ""
    try:
        args = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return str(raw)
    if isinstance(args, dict):
        for key in ("cmd", "command", "code"):
            value = args.get(key)
            if isinstance(value, list):
                return " ".join(map(str, value))
            if isinstance(value, str):
                return value
    return str(raw)


def _tool_output(payload: dict) -> str:
    out = payload.get("output")
    if isinstance(out, list):
        return "\n".join("[screenshot]" if c.get("type") == "input_image" else c.get("text", "")
                         for c in out if isinstance(c, dict))
    if out is None:
        return ""
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def extract_items(records: list[dict]) -> list[Item]:
    items: list[Item] = []
    calls: dict[str, Item] = {}

    def add(payload: dict, ts: str | None) -> None:
        kind = payload.get("type")
        if kind == "message" and payload.get("role") in ("user", "assistant"):
            text, images = _message_content(payload)
            text = truncate(text, MESSAGE_LIMIT)
            if text:
                items.append(Item(payload["role"], text, ts, images=images))
        elif kind in ("function_call", "custom_tool_call"):
            item = Item("tool", _tool_input(payload), ts, name=str(payload.get("name") or "?"))
            items.append(item)
            if payload.get("call_id"):
                calls[payload["call_id"]] = item
        elif kind in ("function_call_output", "custom_tool_call_output"):
            item = calls.get(payload.get("call_id"))
            if item is not None:
                item.output = _tool_output(payload)

    # `compacted` records are ignored: their replacement_history is only the user
    # messages plus an encrypted summary, while the rollout keeps the full raw history.
    for rec in records:
        if rec.get("type") == "response_item" and isinstance(rec.get("payload"), dict):
            add(rec["payload"], rec.get("timestamp"))
    return items


def render_tool(item: Item) -> str:
    output = "(no output)" if item.output is None else truncate(item.output, TOOL_OUTPUT_LIMIT)
    return f"[Codex tool: {item.name}]\n{truncate(item.text, TOOL_INPUT_LIMIT)}\n→ {output}"


def build_turns(items: list[Item], header: str | None = None, max_chars: int = TOTAL_LIMIT) -> list[Turn]:
    turns: list[Turn] = []
    for item in items:
        role = "user" if item.role == "user" else "assistant"
        text = render_tool(item) if item.role == "tool" else item.text
        if not (turns and turns[-1].role == role):
            turns.append(Turn(role, [], item.ts))
        turns[-1].parts.append(text)
        turns[-1].images.extend(item.images)
    dropped = 0
    total = sum(len(t.text) for t in turns)
    while len(turns) > 1 and total > max_chars:
        total -= len(turns.pop(0).text)
        dropped += 1
    if turns and turns[0].role == "assistant":
        turns.insert(0, Turn("user", [LEAD_USER_TEXT], turns[0].ts))
    if turns and dropped:
        turns[0].parts.insert(0, f"[{dropped} earlier turns were not carried over because of size — they remain in Codex.]")
    if turns and header:
        turns[0].parts.insert(0, header)
    return turns
