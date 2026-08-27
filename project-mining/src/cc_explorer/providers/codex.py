"""Direct-filesystem provider for Codex rollout transcripts."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import orjson

from ..models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    HumanEntry,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    UserMessageModel,
)
from ..utils import PrefixId
from .base import Harness, ProviderSession


@dataclass(frozen=True)
class _RolloutMeta:
    rollout_id: str
    thread_id: str
    path: Path
    cwd: str
    history_base_id: str | None
    history_base_offset: int | None
    history_start_ordinal: int | None


class CodexProvider:
    """Discover and parse Codex rollouts without starting Codex itself."""

    harness = Harness.codex

    def __init__(self, codex_home: Path | None = None) -> None:
        configured = os.environ.get("CODEX_HOME")
        self.home = (codex_home or (Path(configured) if configured else Path.home() / ".codex"))

    def _rollout_paths(self) -> Iterable[Path]:
        for root_name in ("sessions", "archived_sessions"):
            root = self.home / root_name
            if root.is_dir():
                yield from root.rglob("*.jsonl")

    @staticmethod
    def _read_meta(path: Path) -> _RolloutMeta | None:
        try:
            with path.open("rb") as stream:
                line = stream.readline()
            data = orjson.loads(line)
            if data.get("type") != "session_meta":
                return None
            payload = data.get("payload") or {}
            thread_id = payload.get("id") or payload.get("session_id")
            cwd = payload.get("cwd")
            if not isinstance(thread_id, str) or not isinstance(cwd, str):
                return None
            history = payload.get("history_base") or {}
            return _RolloutMeta(
                # Rollout id is the filename UUID. SessionMeta.id is the stable
                # logical thread id and can survive a revert to a new rollout.
                rollout_id=path.stem[-36:],
                thread_id=thread_id,
                path=path,
                cwd=str(Path(cwd).expanduser().resolve()),
                history_base_id=history.get("thread_id"),
                history_base_offset=history.get("end_byte_offset"),
                history_start_ordinal=payload.get("subagent_history_start_ordinal"),
            )
        except (OSError, orjson.JSONDecodeError, AttributeError):
            return None

    def discover_sessions(self, projects: Sequence[str] | None = None) -> list[ProviderSession]:
        from .._claude_paths import _get_worktree_paths

        wanted = (
            {str(Path(p).expanduser().resolve()) for p in projects}
            if projects
            else None
        )
        metas = [meta for path in self._rollout_paths() if (meta := self._read_meta(path))]
        by_id = {meta.rollout_id: meta for meta in metas}

        def lineage(meta: _RolloutMeta) -> tuple[Path, ...]:
            chain: list[Path] = []
            seen: set[str] = set()
            current: _RolloutMeta | None = meta
            while current is not None and current.rollout_id not in seen:
                seen.add(current.rollout_id)
                chain.append(current.path)
                current = by_id.get(current.history_base_id) if current.history_base_id else None
            chain.reverse()
            return tuple(chain)

        # A revert can write a newer rollout for the same stable thread id.
        # Present one logical session, selecting its newest physical head while
        # retaining every rollout in `by_id` for history-base traversal.
        heads: dict[str, _RolloutMeta] = {}
        for meta in metas:
            prior = heads.get(meta.thread_id)
            if prior is None or meta.path.stat().st_mtime > prior.path.stat().st_mtime:
                heads[meta.thread_id] = meta

        @lru_cache(maxsize=None)
        def project_identity(cwd: str) -> tuple[str, str | None]:
            worktrees = _get_worktree_paths(cwd)
            if not worktrees:
                return cwd, None
            main = str(Path(worktrees[0]).expanduser().resolve())
            return main, (None if cwd == main else Path(cwd).name)

        refs = [
            ProviderSession(
                session_id=PrefixId(meta.thread_id),
                paths=lineage(meta),
                project_path=project_identity(meta.cwd)[0],
                worktree=project_identity(meta.cwd)[1],
                harness=self.harness,
            )
            for meta in heads.values()
            if wanted is None or project_identity(meta.cwd)[0] in wanted
        ]
        return refs

    def load_transcript(self, paths: Sequence[Path]) -> list[TranscriptEntry]:
        if not paths:
            return []
        metas = [self._read_meta(path) for path in paths]
        if metas and metas[-1] is not None and metas[-1].history_start_ordinal is not None:
            # A subagent rollout may point at its parent's history for model
            # context. That inherited prefix is not part of the child session.
            paths = paths[-1:]
            metas = metas[-1:]
        limits: list[int | None] = [None] * len(paths)
        for index in range(1, len(paths)):
            child = metas[index]
            if child is not None:
                limits[index - 1] = child.history_base_offset

        thread_id = next((m.thread_id for m in reversed(metas) if m), "")
        cwd = next((m.cwd for m in reversed(metas) if m), "")
        entries: list[TranscriptEntry] = []
        for path, limit, meta in zip(paths, limits, metas):
            entries.extend(self._parse_file(
                path,
                thread_id,
                cwd,
                limit,
                meta.history_start_ordinal if meta is not None else None,
            ))
        return entries

    def _parse_file(
        self,
        path: Path,
        thread_id: str,
        cwd: str,
        byte_limit: int | None,
        start_ordinal: int | None,
    ) -> list[TranscriptEntry]:
        entries: list[TranscriptEntry] = []
        has_turn_context = False
        with path.open("rb") as probe:
            while byte_limit is None or probe.tell() < byte_limit:
                line = probe.readline()
                if not line:
                    break
                if b'"type":"turn_context"' in line or b'"type": "turn_context"' in line:
                    has_turn_context = True
                    break
        turn_context_seen = not has_turn_context
        with path.open("rb") as stream:
            while byte_limit is None or stream.tell() < byte_limit:
                line = stream.readline()
                if not line:
                    break
                try:
                    data = orjson.loads(line)
                except orjson.JSONDecodeError:
                    continue
                if start_ordinal is not None and data.get("ordinal", 0) < start_ordinal:
                    continue
                if data.get("type") == "turn_context":
                    turn_context_seen = True
                    continue
                entry = self._normalize(
                    data, thread_id, cwd, allow_user=turn_context_seen
                )
                if entry is not None:
                    entries.append(entry)
        return entries

    @staticmethod
    def _text_blocks(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        )

    @staticmethod
    def _uuid(thread_id: str, data: dict[str, Any]) -> PrefixId:
        payload = data.get("payload") or {}
        identity = payload.get("id") or payload.get("call_id") or data.get("ordinal")
        return PrefixId(str(uuid.uuid5(uuid.NAMESPACE_URL, f"codex:{thread_id}:{identity}")))

    @staticmethod
    def _tool_input(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {"input": parsed}
            except json.JSONDecodeError:
                return {"input": value}
        return {"input": value}

    def _base(self, data: dict[str, Any], thread_id: str, cwd: str) -> dict[str, Any]:
        payload = data.get("payload") or {}
        return {
            "uuid": self._uuid(thread_id, data),
            "timestamp": data.get("timestamp") or datetime.min.isoformat(),
            "sessionId": thread_id,
            "cwd": cwd,
            "entrypoint": "sdk-cli" if payload.get("source") == "exec" else "cli",
        }

    def _normalize(
        self,
        data: dict[str, Any],
        thread_id: str,
        cwd: str,
        *,
        allow_user: bool = True,
    ) -> TranscriptEntry | None:
        if data.get("type") != "response_item":
            return None
        payload = data.get("payload") or {}
        kind = payload.get("type")
        base = self._base(data, thread_id, cwd)

        if kind == "message":
            role = payload.get("role")
            text = self._text_blocks(payload.get("content"))
            if not text or role not in {"user", "assistant"}:
                return None
            if role == "user":
                if not allow_user:
                    return None
                return HumanEntry(
                    **base,
                    type="user",
                    message=UserMessageModel(
                        role="user", content=[TextContent(type="text", text=text)]
                    ),
                )
            return self._assistant(base, [TextContent(type="text", text=text)])

        if kind == "agent_message":
            text = self._text_blocks(payload.get("content"))
            if not text:
                return None
            author = payload.get("author") or "agent"
            recipient = payload.get("recipient") or "agent"
            wrapped = (
                f'<teammate-message teammate_id="{author}">'
                f"{text}</teammate-message>"
            )
            return HumanEntry(
                **base,
                type="user",
                agentName=str(recipient),
                message=UserMessageModel(
                    role="user", content=[TextContent(type="text", text=wrapped)]
                ),
            )

        if kind == "reasoning":
            summary = self._text_blocks(payload.get("summary"))
            if not summary and isinstance(payload.get("summary"), str):
                summary = payload["summary"]
            return self._assistant(
                base, [ThinkingContent(type="thinking", thinking=summary)]
            ) if summary else None

        if kind in {"function_call", "custom_tool_call", "local_shell_call"}:
            name = payload.get("name") or kind
            raw_input = payload.get("arguments", payload.get("input", payload.get("action", {})))
            return self._assistant(base, [ToolUseContent(
                type="tool_use",
                id=PrefixId(str(payload.get("call_id") or base["uuid"].full)),
                name=str(name),
                input=self._tool_input(raw_input),
            )])

        if kind in {"function_call_output", "custom_tool_call_output", "local_shell_call_output"}:
            output = payload.get("output", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            is_error = payload.get("is_error") or payload.get("status") == "failed"
            return ToolResultEntry(
                **base,
                type="user",
                message=UserMessageModel(role="user", content=[ToolResultContent(
                    type="tool_result",
                    tool_use_id=PrefixId(str(payload.get("call_id") or "")),
                    content=output,
                    is_error=bool(is_error),
                )]),
                toolUseResult=output,
            )
        return None

    @staticmethod
    def _assistant(base: dict[str, Any], content: list[Any]) -> AssistantTranscriptEntry:
        return AssistantTranscriptEntry(
            **base,
            type="assistant",
            message=AssistantMessageModel(
                id=base["uuid"].full,
                type="message",
                role="assistant",
                model="codex",
                content=content,
            ),
        )
