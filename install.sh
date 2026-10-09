#!/usr/bin/env bash
# Installs the shared Claude Code config into ~/.claude (agents, skills, delegation policy).
# Existing files are backed up as <file>.bak-<timestamp> before being replaced.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/claude" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
STAMP="$(date +%Y%m%d-%H%M%S)"
MARKER="# Delegation policy"

install_file() {
  local rel="$1"
  mkdir -p "$(dirname "$DEST/$rel")"
  if [ -e "$DEST/$rel" ] && ! cmp -s "$SRC/$rel" "$DEST/$rel"; then
    cp "$DEST/$rel" "$DEST/$rel.bak-$STAMP"
    echo "backed up $rel"
  fi
  cp "$SRC/$rel" "$DEST/$rel"
  echo "installed $rel"
}

while IFS= read -r -d '' f; do
  install_file "${f#"$SRC"/}"
done < <(find "$SRC/agents" "$SRC/skills" -type f -print0)

# Append the delegation policy to CLAUDE.md once, without touching existing content.
touch "$DEST/CLAUDE.md"
if grep -qxF "$MARKER" "$DEST/CLAUDE.md"; then
  echo "CLAUDE.md already has the delegation policy (left unchanged)"
else
  cp "$DEST/CLAUDE.md" "$DEST/CLAUDE.md.bak-$STAMP"
  { echo; cat "$SRC/CLAUDE.delegation.md"; } >> "$DEST/CLAUDE.md"
  echo "appended delegation policy to CLAUDE.md"
fi

if command -v claude >/dev/null 2>&1; then
  echo "Claude Code version: $(claude --version) (Haiku 5.5 needs 2.1.293 or later)"
fi
echo "Done. Run /agents in a new session to confirm."
