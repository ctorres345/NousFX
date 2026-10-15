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

# Keep the delegation policy in CLAUDE.md current. The section starts at the marker line and
# ends at the next "# " heading (or end of file); everything else in the file is left alone.
touch "$DEST/CLAUDE.md"
NEW="$(mktemp)"
awk -v m="$MARKER" '
  $0 == m { skip = 1; next }
  skip && /^# / { skip = 0 }
  !skip { print }
' "$DEST/CLAUDE.md" | awk '{ a[NR] = $0 } END { n = NR; while (n > 0 && a[n] == "") n--; for (i = 1; i <= n; i++) print a[i] }' > "$NEW"
{ cat "$NEW"; [ -s "$NEW" ] && echo; cat "$SRC/CLAUDE.delegation.md"; } > "$NEW.out"
if cmp -s "$NEW.out" "$DEST/CLAUDE.md"; then
  echo "CLAUDE.md delegation policy already current"
else
  cp "$DEST/CLAUDE.md" "$DEST/CLAUDE.md.bak-$STAMP"
  cp "$NEW.out" "$DEST/CLAUDE.md"
  echo "updated delegation policy in CLAUDE.md"
fi
rm -f "$NEW" "$NEW.out"

if command -v claude >/dev/null 2>&1; then
  echo "Claude Code version: $(claude --version) (Haiku 5.5 needs 2.1.293 or later)"
fi
echo "Done. Run /agents in a new session to confirm."
