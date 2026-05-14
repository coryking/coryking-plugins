# Decompose pass — CLAUDE.md @ HEAD (worktree-agent-a9e36b32ac793ce5c)

Target: 217 lines. No prior `<!-- last-decomposed -->` marker.

The file is doing **three jobs at once**: (1) session-shaping behavioral guidance, (2) plugin index / structural map, (3) architectural decision record for past choices. Only (1) earns auto-load priority every session. (2) drifts every commit. (3) is reference material that informs future edits but doesn't need to be in context to do the next task.

## PRESENT — session-shaping (this is what CLAUDE.md is for)

### Cluster: framing
- [L3-5] Commitment: this file's audience is sessions working *on* the plugins, not *with* them. Operational docs belong in skill dirs.

### Cluster: engineering ownership
- [L9] Commitment: when you spot tech debt while implementing a feature, fix at the root rather than adapter on top.
- [L11] Commitment: act as the engineer — push back on debt-creating approaches; question why a bridge function is needed.
- [L13] Commitment: read existing models before adding functionality; evolve the model rather than working around it.

### Cluster: GitHub workflow (concrete)
- [L17] Commitment: public repo — never include conversation content, personal data, or export files in issues or PRs.
- [L20] Commitment: every session start, `gh issue list --state open`.
- [L21] Commitment: file issues immediately when noticed, don't batch.
- [L22] Commitment: scan queue before starting a ticket to detect foundational tickets.
- [L24] Commitment: label vocabulary is fixed (7 labels).

### Cluster: tooling discipline
- [L28] Commitment: use cc-explorer MCP tools, don't shell out to JSONL files.
- [L28] Commitment: every session is a field test of cc-explorer; file fixes immediately.

### Cluster: principles
- [L32] Commitment: top-of-output for summary info.
- [L33] Commitment: skills are self-contained.
- [L34] Commitment: wait for three variants before abstracting.
- [L35] Commitment: one authoritative source.
- [L36] Commitment: toolbox not product — prefer clean rewrites over retrofitting.

## PRESENT — index / structural (not session-shaping)

### Cluster: repo-structure map
- [L42-76] Commitment: documents the directory tree. **Goes stale on every plugin change.** This very run had to edit it to include the new skill dir.

### Cluster: MCP-server-internals reference
- [L80-97] Commitment: documents how cc-explorer is wired via `.mcp.json`. Belongs in `project-mining/CLAUDE.md` or `project-mining/AGENTS.md` (or `project-mining/skills/cc-explorer/SKILL.md`), where the wiring actually lives.
- [L99-106] Commitment: documents plugin internals (named agents, skills frontmatter, `${CLAUDE_PLUGIN_ROOT}`). Anthropic-reference-class content; already covered in the Anthropic skills docs.

### Cluster: plugin catalog
- [L110-126] Commitment: documents what `project-mining` is. Duplicates `project-mining/.claude-plugin/plugin.json` description + per-skill SKILL.md descriptions.
- [L128-148] Commitment: documents what `engineering-loop` is. Duplicates `engineering-loop/.claude-plugin/plugin.json` description + NOTICE.

## PRESENT — architectural decision record

### Cluster: design decisions (ADR-style)
- [L152-160] "Architecture astronaut" scoping discussion. Historical reasoning about an abstraction boundary.
- [L162-169] Plugin conversion from standalone skill to plugin-with-named-agent. Historical.
- [L171-179] IDE chat mining reorganization. Historical.
- [L181-189] Why cc-explorer replaced strip_chat.py. Historical (and the predecessors don't exist anymore).
- [L191-203] The `agent_content` display parameter. Historical (this is feature-design rationale).
- [L205-213] Engineering-loop fork deltas. Historical (also documented in `engineering-loop/NOTICE`).
- [L215] Pointer to GitHub Issues for refactor backlog.
- [L217] **`claude-md-doctor` skill** — just-added; same shape as the others.

## MISSING

- A commitment about **CLAUDE.md hygiene** itself. The file says "skills are self-contained" but doesn't say what this file is *not* for. The introductory paragraph at L3-5 hints at it but doesn't make it a rule. Net result: the file accumulated index + ADR content over time. Adding an explicit "this file is for session-shaping behavioral guidance only; structure goes in plugin metadata, history goes in `docs/`" rule would prevent recurrence.
- A commitment about **honoring the `<!-- last-decomposed -->` marker** now that `claude-md-doctor` exists. (Self-referential, but earned: the marker stamp is a real mechanism.)

## DROP-CANDIDATES

- **[L40-76] Repo-structure ASCII tree.** Reason: index content that goes stale every commit; the harness can `ls` if needed. Move a one-line pointer to `docs/repo-structure.md` (new file) or just delete — the existing per-plugin layout is discoverable.
- **[L78-97] MCP server architecture section.** Reason: belongs in `project-mining/`'s own docs. Off-key for the root CLAUDE.md.
- **[L99-106] Plugin internals section.** Reason: Anthropic-reference content (frontmatter shape, `${CLAUDE_PLUGIN_ROOT}`). Available in the official skills docs. Off-key.
- **[L108-148] Plugins section (project-mining, engineering-loop).** Reason: duplicates plugin.json + NOTICE content. Each plugin should own its own description. Replace with one-line pointers ("see `<plugin>/`").
- **[L150-217] Design Decisions section.** Reason: ADR-class content. Belongs in `docs/design-decisions.md` (or `docs/adr/` if we ever want one-file-per-decision). Doesn't need to be in every session's context.

## Tensions and clusters

- **Tension:** L3-5 says operational docs belong in skill dirs — but L42-76 puts structural information in the root CLAUDE.md anyway. The file contradicts its own stated audience. This is the strongest single signal that the index content should move.
- **Cluster:** the five "Principles" bullets (L32-36) are well-suited to staying in CLAUDE.md. Each is a stance a session should hold throughout its work. None of them belongs in a `paths:`-scoped rule.
- **Cluster:** "Engineering ownership" (L9-13) and "Principles" (L32-36) overlap but don't contradict. Keep both; consider consolidating into a single "Stance" section if the next pass agrees.

## Alternative reading

One could read the file as primarily a **project memory document** — a place to remember *why* the codebase is shaped this way, mainly for the human reader. Under that reading, the Design Decisions section is the main content and the principles are scaffolding. We're explicitly rejecting that reading: CLAUDE.md is **always** auto-loaded, so its content costs every session some token budget. ADRs should be reference material the session opens on demand, not auto-load.

## Self-reference flags

- **L33: "Skills are self-contained. ... No references to external files, global CLAUDE.md, or other skills. If you need something, inline it."** Combined with L36's "one authoritative source" principle — both rules cut against this CLAUDE.md duplicating per-plugin content. The file's own rules suggest the duplication should not exist. Strong signal that the Plugins section should drop to one-liners.
- **L34: "Wait for three variants before abstracting."** Self-relevant: each Design Decisions subsection is one variant of "we made a decision, here's why." Five subsections = past the abstraction threshold. The decisions should live in a shared format (`docs/design-decisions.md`), not as ad-hoc subsections of CLAUDE.md.

## Mechanism re-routing summary

| Current location | Right mechanism | Action |
|------------------|-----------------|--------|
| L40-76 (repo tree) | maintainer/reference doc | move to `docs/repo-structure.md` or delete |
| L78-106 (MCP + plugin internals) | per-plugin doc + Anthropic skills docs | drop; link if needed |
| L108-148 (plugin catalog) | per-plugin `plugin.json` + `NOTICE` | replace with one-liners |
| L150-217 (design decisions) | `docs/design-decisions.md` | move wholesale |
