# Decompose — ./CLAUDE.md

Framing: an LLM session loading this instruction surface for the first time. Conservation
of mass — adding requires removing or merging. Detail decays; abstractions survive.

## PRESENT

- **Repo purpose & audience framing** — what the repo is and that CLAUDE.md is for plugin
  authors, not executors — [L1–5]
- **Engineering ownership stance** — root-cause fixes, push-back posture, evolve upstream
  models rather than adding bridges — [L7–13]
- **GitHub Issues is the backlog** — start-of-session check, file-as-you-see, scan-for-
  foundational-issue discipline — [L15–24]
- **cc-explorer dogfooding** — use MCP tools, propose fixes on friction — [L26–28]
- **Five durable principles** — summary-at-top, skills self-contained, three-variants-
  before-abstracting, one authoritative source, toolbox-not-product — [L30–36]
- **Plugin infrastructure facts** — `{baseDir}` resolves to skill dir, `${CLAUDE_PLUGIN_ROOT}`
  in `.mcp.json`/hooks, frontmatter shapes for agents/skills — [L94–101]
- **Plugin existence and high-level purpose** (project-mining, engineering-loop, mcp-authoring)
  — [L105–109, L123–127]
- **Severity-scale source-of-truth pointer** — [L143]
- **el:review naming trick** — colon in frontmatter `name:`, directory uses dash; durable
  pattern for any future plugin skill that wants a namespace prefix — [L125, L205]

### Clusters & tensions

- L33 ("Skills are self-contained. No references to external files, global CLAUDE.md, or
  other skills.") is in tension with the file's own structure: ~60% of the body is meta-
  inventory and decision narrative that the skills themselves should own.
- L36 ("toolbox not a product … prefer clean rewrites over incremental patches") is in direct
  tension with the Design Decisions section — that section *is* preserved retrofit narrative.
- User-global CLAUDE.md L24 forbids chronological language ("recently", "now uses", "currently
  set to") in documentation; the entire Design Decisions section violates this.

## MISSING

- (Hypothesis — one instance) An explicit pointer to `engineering-loop/NOTICE` for fork
  provenance, replacing the inlined fork-deltas. Earns its tokens only if NOTICE is the
  durable source. Decide via PR comment.

(No N≥2 MISSING rules surfaced. The framing's contract is well-stated.)

## DROP CANDIDATES

- **`ls`-able directory tree (L42–71)** — discoverable junk. Right-mechanism table flags
  exactly this pattern. Codebase grew/will grow; the tree drifts; reader runs `ls -R` or
  reads `marketplace.json` for ground truth.
- **MCP server wiring section (L73–92)** — restates `project-mining/.mcp.json` contents +
  three bullets that re-express the skill-vs-tool boundary already in Principles (L33, L35).
  Violates "one authoritative source." Net: the JSON block belongs to the file it
  configures; the bullets are duplicate principle.
- **Plugin component inventories (L111–119 project-mining, L129–139 engineering-loop)** —
  one-line-per-file inventory lists. Discoverable via `ls plugin/agents` + reading
  frontmatter. Bullets like "`.claude-plugin/plugin.json` — plugin metadata" carry no
  information beyond the filename.
- **"Output: user-confirmed during alignment" (L121)** — operational. The skill's own
  `SKILL.md` is the source of truth.
- **Entire "Design Decisions" section (L145–208)** — ADR history. Multi-paragraph
  narratives with banned chronological language ("During initial development", "We later
  hit", "started as a raw brain dump", "We reorganized it"). Right-mechanism routing:
  `docs/decisions/<date>-<slug>.md` or trust `git log`. Specific drops:
  - L147–155 architecture-astronaut narrative — the principle ("wait for three variants")
    already survives at L34. The retelling is biography.
  - L157–164 plugin-conversion narrative — completed-decision history. The plugin shape
    *is* the answer; `git log` carries the why.
  - L166–174 IDE-mining-framework narrative — completed decision; the framework lives in
    `mining-researcher.md`.
  - L176–184 cc-explorer-vs-strip_chat.py evolution — superseded code is gone from the
    tree. Trust `git log` for the why.
  - L186–198 `agent_content` parameter design notes — parameter behavior is documented in
    the MCP tool description, where future readers will look. Auto-loaded surface is the
    wrong place.
  - L200–208 engineering-loop fork notes — duplicates `engineering-loop/NOTICE`.
- **L210 "Things to refactor: see GitHub Issues"** — redundant with L20 ("Check
  `gh issue list --state open`").

## ALTERNATIVE READING

A defensible alternative: keep the Design Decisions section as a single tight "decisions
this codebase has lived through" block, on the theory that a new session benefits from
knowing the shape of past abandoned approaches. Trade: ~50 lines of auto-loaded surface
to give a new session something `git log -p` already gives them on demand. Not worth it
under the framing's token-budget pressure.

## QUESTIONS

- Should the discarded DD content land in `docs/decisions/<date>-<slug>.md` before
  deletion, or is `git log` retrieval sufficient? Defaulting to `git log` (cleaner, no new
  surface area to maintain); flagging in proposed-changes for PR-comment override.
- The `mcp-authoring/` plugin is mentioned only in the tree (L62) and not described in
  "Plugins". Either describe it briefly or delete the mention. Defaulting to a one-line
  description (parity with the other two).

## Self-reference flag

The file contains a principle ("Skills are self-contained … no references to … global
CLAUDE.md, or other skills") in a file that is itself the global CLAUDE.md and contains
extensive references to skills. The principle is sound; the document violating it is the
recursion to break.
