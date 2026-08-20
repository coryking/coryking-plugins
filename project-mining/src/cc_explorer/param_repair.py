"""Forgive and teach wrong parameter names at the MCP boundary.

Callers invent plausible-but-wrong parameter names for these tools at a steady,
non-declining rate — `query` for `patterns` is the classic, present in every
month of the corpus. The cost is a wasted round trip: pydantic's default message
("patterns / Missing required argument") never mentions that `query` was the
intent, so recovery takes two or three retries.

Two layers, both applied by `ParameterRepairMiddleware` (registered on the
server, so every tool gets them without per-tool code):

1. Forgiveness — an unambiguous wrong guess is rewritten to the canonical name
   before validation, and a bare string handed to a list-typed parameter is
   wrapped. The call succeeds. Aliases are a RECOVERY mechanism, never
   advertised: the JSON schema the client sees stays canonical, so nothing here
   teaches a caller to keep guessing.
2. Teaching — when a wrong name is NOT alias-covered, the validation error names
   the likely intended parameter (nearest match) and always lists the tool's
   real parameter names, so one retry is enough.

Both layers read the tool's own JSON schema, so a new tool or a renamed
parameter is covered the moment it is registered — nothing here enumerates
tools.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

# Wrong name -> canonical candidates, best first. A candidate is only used when
# the tool actually has that parameter, so one alias serves tools with different
# shapes: `session_id` lands on `session` for grep_session and on `sessions` for
# grep_sessions, and on nothing at all for a tool with neither.
#
# Every entry must be UNAMBIGUOUS on every tool it can fire on — if a guess could
# plausibly mean two different parameters of the same tool, leave it out and let
# the teaching layer name the options.
ALIASES: dict[str, tuple[str, ...]] = {
    # Search surface: callers guess `query` for anything searchable.
    "query": ("patterns",),
    "pattern": ("patterns",),
    # Singular/plural slips.
    "project": ("projects",),
    "session_id": ("session", "sessions"),
    "session": ("sessions",),
    "agent_id": ("agent_ids",),
    # Result-cap and id guesses seen in the corpus.
    "max_results": ("limit",),
    "turn_id": ("turn",),
}

# List parameters whose items are IDENTIFIERS (session ids, project paths, agent
# ids) — the only ones where a string that looks like a JSON array is parsed
# rather than wrapped. Nothing in an identifier ever needs `[` or `]`, so
# `'["a","b"]'` there can only be a hand-serialized list.
#
# This is the complete set of array-typed parameters across the tools MINUS
# `patterns`, which is regex-valued: `'["error"]'` and `'["\\d+"]'` are valid
# patterns that happen to parse as JSON, and unwrapping them silently searches
# for something the caller never asked for. A regex string is ALWAYS one pattern.
ID_LIST_PARAMS: frozenset[str] = frozenset(
    {"projects", "sessions", "ids", "agent_ids"}
)


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    return schema.get("properties", {}) or {}


def _required(schema: dict[str, Any]) -> list[str]:
    return list(schema.get("required", []) or [])


def _accepts_array(prop_schema: Any) -> bool:
    """True when this parameter's schema admits an array (incl. `list | None`)."""
    if not isinstance(prop_schema, dict):
        return False
    if prop_schema.get("type") == "array":
        return True
    for branch in prop_schema.get("anyOf", ()) or ():
        if isinstance(branch, dict) and branch.get("type") == "array":
            return True
    return False


def _coerce(name: str, value: Any, prop_schema: Any) -> Any:
    """Fix a string handed to a list-typed parameter.

    `projects="foo"` / `patterns="regex"` are the same slip as the name guesses:
    the caller has the right idea and the wrong shape. Pydantic will not coerce
    str -> list[str], so it fails validation; wrapping is unambiguous.

    On an IDENTIFIER list (see ID_LIST_PARAMS) a string that is itself a JSON
    array (`projects='["a","b"]'` — a common shape when the caller
    hand-serializes its arguments) is parsed rather than wrapped, so it doesn't
    become one nonsense id. Everywhere else, and on `patterns` especially, the
    string is wrapped as-is: `'["error"]'` is a perfectly good regex, and
    guessing that it was meant as a list would search for the wrong thing
    without saying so.
    """
    if not isinstance(value, str) or not _accepts_array(prop_schema):
        return value
    text = value.strip()
    if name in ID_LIST_PARAMS and text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        # All-strings only: a list of anything else was never an id list.
        if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
            return parsed
    return [value]


def repair_arguments(
    arguments: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Return `arguments` with alias names and bare-string list values fixed.

    An alias only fires when it is not itself a real parameter of this tool, its
    canonical target IS one, and that target is not already spoken for (neither
    passed directly nor claimed by an earlier alias) — so a caller who passes
    both `query` and `patterns` keeps their explicit `patterns`.
    """
    props = _properties(schema)
    if not props:
        return dict(arguments)

    claimed = {key for key in arguments if key in props}
    repaired: dict[str, Any] = {}
    for key, value in arguments.items():
        canonical = key
        if key not in props:
            for candidate in ALIASES.get(key, ()):
                if candidate in props and candidate not in claimed:
                    canonical = candidate
                    claimed.add(candidate)
                    break
        repaired[canonical] = _coerce(canonical, value, props.get(canonical))
    return repaired


def _suggest(name: str, candidates: list[str]) -> str | None:
    """Nearest real parameter name for a wrong one, or None if nothing is close."""
    for target in ALIASES.get(name, ()):
        if target in candidates:
            return target
    close = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
    return close[0] if close else None


def argument_error_message(
    tool_name: str,
    schema: dict[str, Any],
    arguments: dict[str, Any],
    errors: list[dict[str, Any]],
) -> str:
    """Build the recover-in-one-retry message for a failed argument validation.

    Diagnosis first (what to change), inventory last — the top of the message is
    what survives truncation.
    """
    props = _properties(schema)
    names = list(props)
    required = _required(schema)

    unknown = [key for key in arguments if key not in props]
    missing = [name for name in required if name not in arguments]

    lines = [f"Invalid arguments for {tool_name}."]

    for key in unknown:
        suggestion = _suggest(key, names)
        if suggestion:
            hint = (
                f" — you already passed `{suggestion}`, so drop `{key}`"
                if suggestion in arguments
                else f" — did you mean `{suggestion}`?"
            )
            lines.append(f"  No parameter `{key}` on {tool_name}{hint}")
        else:
            lines.append(
                f"  No parameter `{key}` on {tool_name} — no close match; "
                f"see the parameter list below."
            )

    if missing:
        lines.append(
            "  Missing required parameter(s): " + ", ".join(f"`{m}`" for m in missing)
        )

    # Anything pydantic complained about that is not a name problem (bad type,
    # out-of-range value) — reported verbatim so the caller sees the real limit.
    handled = {"missing_argument", "unexpected_keyword_argument"}
    for err in errors:
        if err.get("type") in handled:
            continue
        loc = ".".join(str(part) for part in err.get("loc", ())) or "(argument)"
        lines.append(f"  `{loc}`: {err.get('msg', 'invalid value')}")

    if len(lines) == 1:
        lines.append("  One or more arguments failed validation.")

    inventory = ", ".join(
        f"`{name}`" + (" (required)" if name in required else "") for name in names
    )
    lines.append(f"Parameters of {tool_name}: {inventory or '(none)'}")
    return "\n".join(lines)
