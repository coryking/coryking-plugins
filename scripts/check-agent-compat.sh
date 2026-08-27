#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

fail=0
while IFS= read -r claude_file; do
  directory="$(dirname "$claude_file")"
  agents_file="$directory/AGENTS.md"

  if [[ ! -f "$agents_file" ]]; then
    echo "missing Codex instructions: $agents_file" >&2
    fail=1
    continue
  fi

  expected='@AGENTS.md'
  actual="$(<"$claude_file")"
  if [[ "$actual" != "$expected" ]]; then
    echo "$claude_file must contain only $expected" >&2
    fail=1
  fi

  if ! git ls-files --error-unmatch "$agents_file" >/dev/null 2>&1; then
    echo "untracked canonical instructions: $agents_file" >&2
    fail=1
  fi
done < <(find . -name CLAUDE.md -not -path './.git/*' -not -path '*/evals/fixtures/*' -print | sort)

exit "$fail"
