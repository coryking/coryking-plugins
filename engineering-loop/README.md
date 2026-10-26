# engineering-loop

Code review, instruction-file cleanup, research, and session closeout for people maintaining a codebase with agents. It keeps the parts of [EveryInc's compound-engineering plugin](https://github.com/EveryInc/compound-engineering-plugin) useful for solo work, without requiring its full delivery workflow.

## Install

Choose **engineering-loop** from the `coryking-plugins` source in the ChatGPT desktop app's **Plugins** directory, or from the CLI plugin browser. Claude Code can install it from the same repository marketplace. See [installation instructions](../INSTALLATION.md) for adding the source and supported hosts.

## Review code

Ask for a review of the current changes, branch or pull request, or invoke the `el:review` skill. It brings together correctness, testing, maintainability, simplicity, project standards and design-intent reviews, then adds relevant domain perspectives. Findings are merged and filtered before they reach you.

The review can report without edits or apply changes under the selected mode. Pass `mode:report-only` for a strictly read-only review. The [review skill](skills/el-review/SKILL.md) is the authoritative guide to modes, arguments, scope and output.

## Clean up instructions and memory

`el:claude-md-doctor` diagnoses instruction files, rules and the repository's auto memory, writes an evidence bundle, then applies high-confidence fixes. It leaves an uncommitted diff for review and snapshots memory before changing it. Judgement calls remain in the report. Use the `diagnose` argument when you want the map without edits; the [doctor skill](skills/claude-md-doctor/SKILL.md) explains scope and recovery.

## Research before deciding

The plugin includes research agents for prior art and current best practices. Ask for the question you need answered and the evidence or output you want. Availability and delegation depend on the host's agent interfaces.

## Close an individual session

Ask whether the conversation is ready to close, or use `/engineering-loop:end-session` in Claude Code or `$engineering-loop:end-session` in Codex. The skill preserves the work and handles closeout obligations. See [end-session](skills/end-session/SKILL.md) for what it does.

## Contribute

[CONTRIBUTING.md](../CONTRIBUTING.md) covers development, tests, local loading and releases. [NOTICE](NOTICE) preserves upstream attribution and the port history.
