# Agent-Researcher Orchestration Patterns

> Reference document for designing skills that fan out research to subagents and synthesize findings.
> Sourced from Anthropic engineering blog, Claude Code docs, Google ADK, AWS prescriptive guidance, and practitioner reports.
> Generated: 2026-02-26

---

## Core Concept: Subagents Are Compression Engines

A researcher subagent might consume 100K tokens exploring and reasoning, but returns a 2-5K token structured summary to the lead agent. The lead agent never sees the raw exploration. This is the fundamental context window management strategy — subagents exist to compress, not just parallelize.

---

## Structuring Subagent Prompts

The single biggest lever is prompt specificity. Vague delegation ("research X") causes duplicate work and misinterpreted scope.

A good subagent prompt contains:

| Element | Purpose |
|---------|---------|
| **Objective** | One sentence — what this subagent is trying to find |
| **Output format** | Exact structure for findings (not "a summary" but "a list of findings, each with: claim, evidence quote, source reference, relevance tag") |
| **Tool/source guidance** | Which tools to use, what data is available, what search strategies to try |
| **Task boundaries** | What is explicitly NOT this subagent's job (prevents overlap with siblings) |
| **Methodology hints** | Start broad then narrow, two-pass triage, etc. |

The evolution Anthropic described: prompts went from `"research X"` to detailed mandates specifying search strategies, source types, and coordination protocols. The specificity prevents duplicate work.

---

## Dividing Work Across Subagents

Three division strategies:

### By data source
Each subagent owns a different corpus (git history, chat logs, project docs). Works when sources are independent and require different access patterns.

### By question/facet (Anthropic's primary pattern)
The lead agent decomposes the query into sub-questions, each subagent answers one. Decomposition quality determines everything downstream.

### By theme
Each subagent looks at the same data through a different lens. Good for analysis tasks where themes cut across all sources.

### Effort scaling
- Simple fact-finding: 1 agent, 3-10 tool calls
- Direct comparisons: 2-4 subagents, 10-15 calls each
- Complex research: 10+ subagents with clearly divided responsibilities
- Let the lead agent decide dynamically based on corpus size — don't prescribe a fixed number

---

## The Synthesis Step

Synthesis is editorial work, not concatenation.

### Structured return format
If every subagent returns findings in the same schema (claim + evidence + source + relevance), the synthesizer can compare and cross-reference. Free-form prose wastes the synthesizer's context on parsing.

### Compression is the contract
Subagents consume lots of tokens exploring. They return compressed findings. The lead agent operates on summaries, not raw data.

### Deduplication and conflict resolution
Multiple subagents finding the same thing from different sources = corroboration (good). Contradictory findings = flag for editorial judgment, don't silently pick one.

### Gap detection and iteration
After first-round synthesis, ask: "What questions did I still not answer?" Optionally dispatch a second wave of subagents to fill gaps. Not always single-pass.

---

## Model Tiering

| Role | Model | Why |
|------|-------|-----|
| **Orchestrator** (alignment, decomposition, synthesis) | Opus | Judgment-heavy: translating vague asks into searchable behaviors, editorial synthesis, gap detection |
| **Researchers** (grep, extract, compress) | Sonnet | Pattern matching, evidence extraction, structured summarization — equally capable, faster, cheaper |

Anthropic's own multi-agent research system uses this pattern. The expensive model does decomposition and synthesis; the cheaper model does volume exploration.

---

## Anti-Patterns

### Context window blowout
Subagents return too much data, filling the orchestrator's context. Fix: enforce structured return format with size expectations. Subagents compress; they don't dump.

### Duplicate work
Without explicit task boundaries, subagents perform the same searches. Fix: "you research X, NOT Y" in every prompt. Specific search strategies per subagent.

### Premature orchestration
Starting with complex multi-agent when a single agent with good tools would suffice. Start simple, add specialists only when domain boundaries justify them.

### Prescribing workflow instead of describing tools
"Use grep, then read with offset/limit, then summarize" is worse than "here's a tool that produces one-line-per-message output, grep works on it, each line is self-contained." Describe the tools and data shapes; let the agent figure out the workflow.

### Sequential chunking as coping mechanism
When data is too large, the naive response is reading in sequential chunks (first 500 lines, then next 500). This fills context with raw data before any analysis. Fix: make data pre-searchable so subagents can target what they need.

### No plan persistence
If the lead agent doesn't save its decomposition plan to a file, context truncation can cause it to forget what it dispatched. Save the plan before dispatching researchers.

---

## Claude Code Specifics

- **Task tool** spawns subagents, each with its own context window and tool access. The main agent sees only the returned result.
- **`.claude/tmp/`** is the shared data directory for any pre-processed files. Subagents are sandboxed and cannot access `/tmp/`. For chat mining, cc-explorer works directly on raw JSONL — no pre-processing needed.
- **`{baseDir}`** resolves script paths relative to the skill directory, not the project directory. Subagent prompts must use this for bundled scripts.
- **No subagent-to-subagent communication.** All coordination goes through the orchestrator. The lead agent is the information bottleneck — be smart about what you pass to each subagent and what you ask for back.

---

## Sources

- [How we built our multi-agent research system — Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Effective context engineering for AI agents — Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)
- [Claude Code Sub-Agents: Parallel vs Sequential Patterns — ClaudeFast](https://claudefa.st/blog/guide/agents/sub-agent-best-practices)
- [Context Management with Subagents in Claude Code — RichSnapp](https://www.richsnapp.com/article/2025/10-05-context-management-with-subagents-in-claude-code)
- [Developer's guide to multi-agent patterns in ADK — Google](https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/)
- [Parallelization and scatter-gather patterns — AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/parallelization-and-scatter-gather-patterns.html)
- [Orchestrating multiple agents — OpenAI Agents SDK](https://openai.github.io/openai-agents-python/multi_agent/)
- [Simon Willison on Anthropic's multi-agent system](https://simonwillison.net/2025/Jun/14/multi-agent-research-system/)
