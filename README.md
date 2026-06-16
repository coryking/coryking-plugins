# coryking-plugins

**A Claude Code marketplace for people who drive a fleet of agents and want to know what the hell those agents actually did.**

Three plugins, one install command. The one I get the most mileage out of, by a mile, is **cc-explorer** — my window into the pile of Claude Code transcripts I'd otherwise never read back. I use it to:

- **Carry session context across conversations** — pull what one session knew into wherever I'm working now.
- **Run design and code reviews with a session that lived the work** — clone a past session into a subagent and put it on the review.
- **Mine my chat history for insights and data** — find the decision, the realization, the moment things turned, even when I've forgotten which project it happened in.
- **Watch how agents actually use the tools I write** — see which MCP tools land, which fail, and where a subagent went sideways.
- **Interrogate old sessions instead of grepping their transcripts** — ask a past conversation what it meant, not just what it said.

The rest of the toolbox grew out of the same itch: tools to mine a project's history for evidence, to review the code these agents write, and to write better MCP servers.

```
/plugin marketplace add coryking/coryking-plugins
```

That registers the marketplace. Then enable whichever plugins you want (`/plugin`). Jump to **[Install](#install)** for the full path — including the one env var the session-interrogation feature genuinely needs.

---

## What's in the box

| Plugin | What it's for | The headline thing |
|---|---|---|
| **project-mining** | Read your own history back | **`cc-explorer`** (the star) + an evidence-mining orchestrator + an attention-reflection report |
| **engineering-loop** | Review code before you ship it | Parallel multi-persona code review, slimmed down for solo work |
| **mcp-authoring** | Write MCP tools the model actually picks up | An opinionated reference on tool descriptions |

> cc-explorer ships *inside* the project-mining plugin, but it's the headliner — enable that plugin for cc-explorer alone and ignore the rest if that's all you want.

---

## cc-explorer — talk to your chat history instead of grepping it

**The big one. The tool I get the most mileage out of, period.**

`cc-explorer` is a FastMCP server that parses the JSONL conversation logs in `~/.claude/projects/` and exposes them as MCP tools. When the plugin is enabled, Claude Code starts it automatically — the tools just show up in the palette, no shell commands, no `pip install`, `uv` handles the deps.

Here's the thing about chat history: keyword search finds what was *said*, but the thing you actually want is usually *why*. Those are different problems. cc-explorer is built for both.

**Find a conversation when you've forgotten where it happened.** Search every project at once — worktrees flattened, subagent bodies included — narrow to a session, read the exact turn at full fidelity. The progression is `search_projects` → `grep_session` → `read_turn`, coarse to fine. I reach for this constantly with questions like *"when did we realize the cat-gym was hollow?"* — a *why* question that plain grep keeps whiffing on because the realization never used the words I'd search for.

**See what your subagents actually did.** This is the part nobody else gives you. `list_session_agents` shows every agent a session dispatched; `get_agent_detail` hands you one agent's full prompt, result, and tool trace; `audit_session_tools` rolls up per-tool counts, error rates, and retries across the whole session so you can answer *"are my agents using my tools right?"* — and catch the build/review/integration subagent that quietly failed and lied about it.

**Reconstruct where your attention went.** `get_activity_timeline` rolls every project's transcripts over a time window into a turn-count grid plus pre-computed rollups — how many sessions ran at once, the peaks, hands-on vs. autonomous time. It's the deterministic backbone the attention report (below) reads on top of.

**Convert a past session into a subagent and *put it to work*.** This is the one I'm proudest of, and it's the thing I tell colleagues about first: cc-explorer can take a prior session, *clone it*, and reconnect it as a subagent — and then you talk to it. Ask it what it meant. Ask it to synthesize its own arc. Or hand it a job: it still carries everything that session knew, so it makes a sharp reviewer for a design or code review on the work it lived through. The way I described it when I was inventing it: *"a converter that can take something and move between an agent you can latch onto and interrogate and an agent that can be converted into a session I can attach to... the reason I want this is to let an agent attach to some other one and get it to answer questions about itself."* Use it as an **oracle, not just keyword grep** — pull the why from the horse's mouth. (`rewind_transcript` truncates a cloned artifact back to an earlier turn so you can replay from a known point; `delete_conversions` cleans up the forks when you're done.)

> ⚠️ **Session interrogation requires agent teams.** Cloning a session is harmless without it, but *resuming* the cloned subagent (`SendMessage`) only exists when the calling session was started with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. Without it you get *"no transcript to resume."* Quick check: if `SendMessage` is in your toolset, you're good. See **[Install](#install)** for the one-line settings change.

Every cc-explorer tool is read-only except the conversion tools, and the conversion tools refuse to touch anything but their own artifacts — a real session or a dispatched subagent is never mutated, period.

## The rest of the project-mining plugin

cc-explorer is the spine, but the same plugin ships two higher-level tools built on top of the idea that your transcripts are a corpus worth mining.

### project-mining — turn a project's history into evidence

Point it at a project and a *lens* — `"find where I demonstrated technical leadership"`, `"find struggles that became breakthroughs"`, `"what does this thing actually do"` — and it produces a rich, source-cited narrative you can carve into resume bullets, interview stories, performance-review evidence, or just self-understanding.

The trick is that evidence is rarely labeled with the lens's vocabulary. *"Systems thinking at scale"* isn't found by grepping `"systems thinking"` — it's the architecture decision in `dispatcher.py` plus the session where you said *"wait, this won't hold at 50k events an hour."* Making that inferential leap — from abstract frame to concrete observable — is the whole point, and it's why you ask a model instead of grep.

Under the hood it's an Opus orchestrator that runs an alignment conversation with you first (it *will* ask questions before dispatching — that's a hard gate, because a sharp lens is where all the quality lives), then fans out researcher agents across three corpora: the **process** (chat + git), the **codebase** (source + docs), and the **output** (what the running system actually produces). Findings get woven, not stapled.

### activity-reflection — the herding tax

A report on how your attention actually got spent across the fleet over a window: pulled-in vs. delegated, and *what kind* of pulled-in — drift-response babysitting vs. genuine design vs. clean dispatch. It dispatches an analyst agent with an isolated context (so your priors don't contaminate the classification) that pairs the deterministic `get_activity_timeline` map with calibrated transcript reads, because raw turn-counts systematically over-count herding. Chain it week over week and you get a trend line on whether the fleet is buying you leverage or robbing your attention.

---

## engineering-loop

A deliberately slim fork of [EveryInc's compound-engineering plugin](https://github.com/EveryInc/compound-engineering-plugin) — the parts that compound for a *solo* operator, with the team-scale scaffolding stripped out. Upstream ships ~36k tokens of ambient context shaped for a content-company AI team with issue queues and Slack and a full plan→work→review→compound methodology. Most of that doesn't apply when it's just you and a long-lived codebase, and the token tax is real.

What's left is the stuff that actually earns its weight:

- **`/el:review`** — parallel multi-lens code review. Several reviewer personas run at once (correctness, testing, maintainability, simplicity, security, performance, reliability, an adversarial pass, and more), each returns structured findings, and a merge/dedup pipeline collapses them into one confidence-gated report. Several models looking through different lenses still catch what one pass misses.
- **`/el:claude-md-doctor`** — diagnoses your `CLAUDE.md` / `.claude/rules` instruction surface for bloat, scar tissue, and wrong-mechanism content (a procedure that should be a skill, an "always do X" that should be a hook). It produces a decomposition map and a 10-minute checklist and then *stops* — it does not rewrite your instructions, by design. The thesis it's testing: intervenability, not automation, is the valuable part.
- **Curated research agents** — single-purpose system prompts for "scan the prior art before deciding" and "what are the current best practices for X," with the right epistemics baked in.

See [engineering-loop/README.md](engineering-loop/README.md) for the full persona roster and the slimming ledger (what got dropped, demoted, or freed up vs. upstream), and [engineering-loop/NOTICE](engineering-loop/NOTICE) for provenance.

---

## mcp-authoring

If you build MCP servers, you've hit this: the model ignores your tool, or picks the wrong sibling, and you can't tell why. Usually it's the description — which is simultaneously a spec *and* a prompt, and deserves the same attention as a system prompt. (97.1% of surveyed MCP tools fail at least one component of a good description. It's not just you.)

The **`tool-descriptions`** skill is an opinionated reference distilled from the MCP spec, Anthropic's tool-use guidance, FastMCP idioms, and the empirical "smelly descriptions" research — covering descriptions, server `instructions`, parameter schemas, output schemas, annotations, and recoverable error design. It's eat-your-own-dogfood: it's how cc-explorer's own tools got written.

---

## Install

Inside Claude Code, register the marketplace:

```
/plugin marketplace add coryking/coryking-plugins
```

Then open `/plugin`, pick the plugins you want, and enable them. Restart Claude Code so the plugin content and MCP server load.

That's the whole story for everything except one feature.

### Turning on session interrogation (one env var)

cc-explorer's marquee trick — cloning a past session into a subagent you can *talk to* — needs the agent-teams runtime, which is gated behind an experimental flag. Add it to `settings.json` (project `.claude/settings.json`, `.claude/settings.local.json`, or `~/.claude/settings.json`) and **restart** (env is read at session start):

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  }
}
```

Everything else — search, grep, agent forensics, the activity timeline, the converter's *write* side — works without it. Only the resume path (`SendMessage`) needs it. If `SendMessage` shows up in your toolset, you're set.

### Requirements

- **Claude Code CLI**
- **`uv`** — for the cc-explorer MCP server's Python deps. Claude Code launches the server via `uv run`, which resolves everything; no manual venv, no `pip install`.

### Going deeper

[INSTALLATION.md](INSTALLATION.md) covers manual installation, local development against a live repo checkout (there's no native editable-install for plugins yet — it documents the workaround), version-bump bookkeeping, and the gotchas that'll bite you.

---

## License

MIT. The `engineering-loop` plugin includes content derived from EveryInc's compound-engineering plugin, also MIT — see [engineering-loop/NOTICE](engineering-loop/NOTICE).
