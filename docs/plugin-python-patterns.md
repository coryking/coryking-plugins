## How Claude Code plugins invoke Python scripts (March 2026 survey)

### Pattern 1: PEP 723 inline script metadata + `uv run --script`

Used by pokutuna/claude-plugins. Scripts declare their own deps inline:

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["runpod"]
# ///
```

SKILL.md invokes with `${CLAUDE_PLUGIN_ROOT}`:
```bash
uv run --script ${CLAUDE_PLUGIN_ROOT}/skills/stocks/scripts/fetch_gpu_stocks.py --min-memory 80
```

`allowed-tools` in SKILL.md frontmatter can restrict to the exact script:
```
Bash(uv run --script ${CLAUDE_PLUGIN_ROOT}/skills/create-pod/scripts/create_pod.py:*)
```

Works when scripts are genuinely self-contained (250-350 lines, one thing each, no shared code). pokutuna's 5 scripts all fit this — they wrap external APIs (RunPod, Vertex AI, GCP Logging).

### Pattern 2: `sys.path.insert` hack for multi-file projects

When plugins outgrow standalone scripts, they use `sys.path.insert(0, str(Path(__file__).parent))` to enable cross-imports between files that are technically "standalone scripts."

**astronomer/agents** (analyzing-data skill): 2,048 lines across 7 files. ABC hierarchy for database connectors (Snowflake, Postgres, DuckDB, SQLite), Click CLI, Jupyter kernel management, pyproject.toml with test deps, full test suite. `cli.py` does `sys.path.insert` then imports from `kernel`, `warehouse`, `cache`.

**anthropics/knowledge-work-plugins**: ~9,400 lines across 26 files. Multiple skills have shared `utils/` packages with `__init__.py`. `ncbi_utils.py` (808 lines) is a full library (rate limiting, GEO metadata, SRA runs, ENA FASTQ URLs, PubMed, downloads). `validate_asm.py` (1,102 lines) is a validation framework. Pinned `requirements.txt`. Repeatedly uses the `sys.path.insert` pattern.

This is real software crammed into the "flat scripts" model because the plugin system doesn't have a better answer.

### Pattern 3: `uv run --project` with pyproject.toml

Used by coryking-plugins/project-mining. cc-explorer is a FastMCP server with Pydantic models and shared modules in `src/cc_explorer/`. The `uv run --project` approach is cleaner than `sys.path.insert` — proper package structure, proper dependency resolution, proper entry points. The MCP server is wired via `.mcp.json` in the plugin directory, so the plugin system starts it automatically.

### Pattern 4: Raw `pip install` + `python scripts/foo.py`

Anthropic's `knowledge-work-plugins` uses `pip install -r requirements.txt --break-system-packages` then `python scripts/foo.py`. This is how they handle deps for their multi-file scripts.

### Pattern 5: `uvx` for published PyPI tools

Astronomer's plugin uses a SessionStart hook to warm the uvx cache, then calls `uvx --from pkg@latest cmd`. Requires publishing to PyPI.

### Key variables

- `${CLAUDE_PLUGIN_ROOT}` — plugin root directory, available in hook command strings and SKILL.md. This is what other plugins use for path references.
- `{baseDir}` — skill directory (where SKILL.md lives). For reaching plugin root from a skill, use `${CLAUDE_PLUGIN_ROOT}` rather than `{baseDir}/../..`.

### Takeaway

The ecosystem splits into two tiers. Simple API wrappers stay as genuine standalone scripts (PEP 723 works). Anything with domain logic immediately grows shared modules and multi-file architecture — and the plugin system has no first-class answer for that. The options are `sys.path.insert` hacks (astronomer, anthropic) or a proper package with `uv run --project` (us). Our approach is architecturally cleaner; we just need stable path references.

---

## Plugin structure, versioning, and publishing (March 2026 survey)

### Monorepo is the dominant layout

All three repos use monorepos with multiple plugins. The root `marketplace.json` registers each plugin via relative `"source"` paths.

**pokutuna/claude-plugins** — 8 independent plugins, each in its own top-level directory with `.claude-plugin/plugin.json`. Root `marketplace.json` lists them all.

```
.claude-plugin/marketplace.json     # root marketplace
allow-until/
  .claude-plugin/plugin.json
  skills/allow-until/SKILL.md
  hooks/hooks.json
  bin/allow-until.sh
runpod/
  .claude-plugin/plugin.json
  skills/{create-pod,prepare-model-upload,stocks,volume-storage}/
...
```

**astronomer/agents** — single plugin at repo root (`"source": "./"`, `"strict": false`). ~30 skill directories. Also has a separate Python MCP server package (`astro-airflow-mcp/`) that publishes to PyPI independently.

**anthropics/knowledge-work-plugins** — 19 plugins (13 Anthropic first-party + 6 partner-built under `partner-built/`). These are the "Cowork" plugins. Partner plugins can have their own nested `marketplace.json` for standalone use.

### marketplace.json

Root-level manifest that registers plugins for marketplace discovery:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "pokutuna-plugins",
  "owner": { "name": "pokutuna" },
  "metadata": { "description": "Claude Code plugins by pokutuna" },
  "plugins": [
    { "name": "allow-until", "description": "...", "source": "./allow-until", "homepage": "..." },
    ...
  ]
}
```

Installation via marketplace:
```
/plugin marketplace add https://github.com/pokutuna/claude-plugins
/plugin install <plugin-name>@pokutuna-plugins
```

`"strict": false` (astronomer) means the marketplace entry is the authority instead of plugin.json — lets the entire repo root act as one plugin.

### plugin.json

Minimal metadata. Only `name` is truly required; version, description, author, keywords are optional.

```json
{
  "name": "allow-until",
  "description": "Time-limited auto-approval mode for Bash commands...",
  "version": "1.4.1",
  "author": { "name": "pokutuna" },
  "homepage": "https://github.com/pokutuna/claude-plugins/tree/main/allow-until",
  "license": "MIT",
  "keywords": ["permissions", "automation", "bash", "hooks"]
}
```

Astronomer embeds hooks directly in plugin.json (SessionStart, UserPromptSubmit, Stop) instead of using a separate `hooks/hooks.json`.

### Versioning

Version lives in `plugin.json` and is used for **cache invalidation** — Claude Code won't pick up changes unless the version bumps.

| Repo | Version location | Git tags? | CI/publish? |
|------|-----------------|-----------|-------------|
| pokutuna | `plugin.json` per plugin | No | No |
| astronomer (plugin) | `plugin.json` | One tag (`plugin-0.2.0`) | No |
| astronomer (MCP server) | Git tags via hatch-vcs | Yes (`astro-airflow-mcp-X.Y.Z`) | PyPI publish workflow |
| anthropics/knowledge-work | `plugin.json` per plugin | No | No |
| anthropics/official directory | Optional in `plugin.json` | No | Frontmatter validation only |

Nobody uses automated version bumping for the plugin itself. It's manual edits to `plugin.json`. The only automated versioning is astronomer's MCP server (a separate Python package, not the plugin).

pokutuna's `check-plugin.py` validation script enforces semver format and checks that plugin.json has `name`, `version`, `description`, `author`, and `keywords`.

### No build steps for plugins

Every plugin is pure content (markdown, scripts, JSON). No compilation, no transpilation, no bundling. The only build/publish case is astronomer's MCP server, which is a separate package that happens to share the repo.

### Hooks in the wild

**astronomer** — SessionStart hook warms `uvx` cache (`uvx --from astro-airflow-mcp@latest af --version`). UserPromptSubmit hook does keyword matching to suggest skills. Stop hook runs cleanup. All defined inline in plugin.json, not in a separate `hooks/hooks.json`.

**pokutuna (allow-until)** — hooks in `hooks/hooks.json` (separate file). Implements time-limited auto-approval for Bash commands.

Both patterns work. Inline-in-plugin.json is simpler for few hooks; separate file scales better.

---

## Source repos examined

- pokutuna/claude-plugins — 8 plugins, 5 Python scripts (250-355 lines each), monorepo
- astronomer/agents — 1 plugin + 1 MCP server, 2,048 lines Python, ABC hierarchy, Click CLI, test suite, PyPI publishing
- anthropics/knowledge-work-plugins — 19 plugins (13 + 6 partner), 9,400 lines Python, shared utils packages, monorepo
- anthropics/claude-plugins-official — central registry/directory, frontmatter validation CI only
- anthropics/skills — flat skill repo, no plugin.json versioning
