#!/usr/bin/env bash
DANGEROUSLY_OMIT_AUTH=true npx @modelcontextprotocol/inspector uv run --project "$(dirname "$0")/../project-mining" cc-explorer
