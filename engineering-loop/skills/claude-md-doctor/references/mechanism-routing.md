# Mechanism Routing Cheat-Sheet

When a line in CLAUDE.md doesn't belong in CLAUDE.md, route it to the right mechanism. Below is the full decision table; the SKILL.md inlines the short form.

## The four extension mechanisms

| Mechanism | Loads | Use for |
|-----------|-------|---------|
| **CLAUDE.md** | Every session, concatenated into context | Persistent facts: build/test commands, conventions, project layout, "always do X" |
| **`.claude/rules/*.md`** | Every session OR only when matching files are opened (with `paths:` frontmatter) | Topic-organized rules; instructions that only matter for part of the codebase |
| **Skills** (`.claude/skills/`) | Only when invoked or when Claude decides relevant | Multi-step procedures, repeatable workflows |
| **Settings** (`settings.json`) | Always; **enforced by the client** | Permissions, env vars, hooks, sandbox, model — *technical enforcement*, not behavioral guidance |

Settings are a *hard* enforcement layer; CLAUDE.md is a *soft* shaping layer. Anything you actually need to enforce belongs in settings or hooks.

## Decision rules

For each line, ask in order:

1. **Is this a multi-step procedure?** → move to a **skill**. (Skills load on demand and can be long.)
2. **Is it only relevant to one part of the tree?** → move to `.claude/rules/<topic>.md` with `paths:` frontmatter. (Saves context until that part of the tree is touched.)
3. **Does it say "must run at X point"** (e.g. before every commit, after every PR, on every test failure)? → write a **hook**, not a CLAUDE.md instruction. The harness can enforce; CLAUDE.md cannot.
4. **Is it a permission, env var, model choice, or sandbox concern?** → **settings.json**. Don't ask Claude to police itself when the harness can refuse.
5. **Is it a maintainer note for humans reading the file?** → HTML block comment `<!-- ... -->`. Stripped before injection, costs nothing in context.
6. **Is it a fact needed in every session?** → CLAUDE.md.

If the answer to all of 1–6 is "no," the line is probably scar tissue. Drop it or convert to a maintainer comment with the original rationale preserved.

## Path-scoped rules — the big context-saver

A rule with `paths:` frontmatter only enters context when Claude reads a matching file:

```markdown
---
paths:
  - "src/api/**/*.ts"
  - "src/**/*.{ts,tsx}"
---

# API conventions

- Always use the typed client from `src/api/client.ts`.
- Never throw raw `Error`; use `ApiError`.
```

When proposing to move a large CLAUDE.md cluster to `.claude/rules/<topic>.md`, **always** include `paths:` frontmatter unless the rule truly belongs at-launch.

## File size targets

- CLAUDE.md: under 200 lines.
- Any single `.claude/rules/<topic>.md`: under 150 lines as a working target. Past that, split by sub-topic.
- Maintainer comments (HTML) don't count toward either budget.

## Concrete-verifiability test

Every retained rule must pass: *can a reader determine from looking at code/output/commits whether this rule was followed?* Examples:

- ✅ "Use `ripgrep` instead of `grep`" — verifiable (grep -r `grep ` in commits).
- ✅ "Target under 200 lines per CLAUDE.md" — verifiable (`wc -l`).
- ❌ "Be a good engineer" — vibes; drop or rewrite.
- ❌ "Care about quality" — vibes; drop or rewrite.

Vibes-rules don't shape behavior. They take context budget and produce nothing.
