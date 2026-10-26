# coryking-plugins

These instructions govern agents **developing this repository**. Instructions for running a plugin belong in that plugin's skills and references.

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, checks, local loading and releases. README and INSTALLATION address plugin users; keep maintainer procedures in CONTRIBUTING rather than duplicating them across those files.

## Engineering

- Own the design as well as the implementation. Read existing models and types before adding behaviour; fix weak upstream models and root causes instead of adding adapters.
- This is an agent toolbox with no default backwards-compatibility obligation. Remove superseded approaches when a clean replacement fits the task. Wait for three concrete uses before generalising.
- Keep skills self-contained within their plugin. Link canonical plugin-local guidance instead of duplicating it or depending on global instructions and unrelated repositories.
- Put counts, omitted-output hints and actionable metadata before lengthy details so they survive truncation.
- Use cc-explorer MCP when inspecting conversation history. Report tool defects you encounter; do not bypass it with raw transcript scans.

## Issues and public evidence

The backlog is [GitHub Issues](https://github.com/coryking/coryking-plugins/issues). File confirmed bugs, debt and design questions when discovered; check existing issues to avoid duplicates. Available labels include `cc-explorer`, `project-mining`, `tech-debt`, `enhancement`, `spike`, `needs-cory` and `process`.

This repository is public. Keep private conversation content, personal data, credentials and exports out of commits, issues and PRs. Committed test and evaluation records must be synthetic.
