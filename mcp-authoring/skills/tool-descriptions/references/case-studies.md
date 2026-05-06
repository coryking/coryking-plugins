# Case Studies

Real-world MCP tool design choices worth studying.

## Context7: two-tool workflow with mandatory sequencing

Context7 provides library documentation to LLMs. They expose exactly two tools with explicit sequencing.

### The tools

1. **`resolve-library-id`** — "Resolves a package/product name to a Context7-compatible library ID and returns matching libraries."
2. **`query-docs`** — "Retrieves documentation for a library using a Context7-compatible library ID."

### Sequencing stated in the description

`query-docs` includes:
> "You must call 'resolve-library-id' first to obtain the exact Context7-compatible library ID required to use this tool, UNLESS the user explicitly provides a library ID in the format '/org/project'."

The dependency is in the description, not implicit. The escape hatch ("unless the user provides…") prevents the model from doing useless preamble calls when the answer is already in hand.

### Behavioral rate-limiting via description

Both tools include:
> "Do not call this tool more than 3 times per question. If you cannot find what you need after 3 calls, use the best information you have."

This is a behavioral constraint enforced entirely through the description — there's no technical rate limit, just a directive that models follow. Worth noting: this works because the constraint is *concrete and counted* (3 calls), not *vague* ("don't call too often").

### Server instructions as activation criteria

Context7's server `instructions` tell the model when to use the server *at all*:

> "Use this server to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, or cloud service — even well-known ones... Use even when you think you know the answer — your training data may not reflect recent changes."

Then negative boundaries:

> "Do not use for: refactoring, writing scripts from scratch, debugging business logic, code review, or general programming concepts."

**The pattern:** server instructions define the activation envelope ("use this when..."), tool descriptions define the specific operations. The two layers do different jobs and don't duplicate each other.

## What to copy from Context7

- **Two-tool workflow with explicit sequencing** is a strong default for any "lookup → fetch" pattern.
- **Counted behavioral limits** ("max 3 calls") work where vague ones don't.
- **Activation envelope in server instructions, operations in tool descriptions** — clean separation of concerns.
- **Escape-hatch clauses** ("unless X") prevent unnecessary preamble calls.

## What to be careful about

- Server instructions may not reach the model on all clients (see `server-and-architecture.md`). If your activation envelope is load-bearing, duplicate it into the most-called tool's description.
- Behavioral rate-limiting via prose is best-effort. Add real rate limiting at the server if it actually matters.
