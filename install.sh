#!/usr/bin/env bash
# Installs the shared Claude Code config into ~/.claude (agents, skills, hooks, delegation policy).
# Existing files are backed up as <file>.bak-<timestamp> before being replaced.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/claude" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}"
STAMP="$(date +%Y%m%d-%H%M%S)"
MARKER="# Delegation policy"

# Print the first symlink on the way from $DEST to $DEST/<rel>, if any. A linked skill or hook
# (for example one from ~/.agents/skills) belongs to someone else; writing through it would overwrite it.
linked_path() {
  local path="$DEST" part parts
  IFS=/ read -ra parts <<< "$1"
  for part in "${parts[@]}"; do
    path="$path/$part"
    if [ -L "$path" ]; then
      echo "${path#"$DEST"/}"
      return 0
    fi
  done
  return 1
}

install_file() {
  local rel="$1" link
  if link="$(linked_path "$rel")"; then
    echo "warning: skipped $rel ($link is a symlink, left untouched)" >&2
    return
  fi
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
done < <(find "$SRC/agents" "$SRC/skills" "$SRC/hooks" -type f -print0)

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

# Register the PR review gate in settings.json (idempotent; other settings and hooks untouched).
PY=python3; command -v python3 >/dev/null 2>&1 && python3 -c 'import sys' 2>/dev/null || PY=python
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) WIN=1; GATE="$(cygpath -m "$DEST")/hooks/review-gate.py";; *) WIN=0; GATE="$DEST/hooks/review-gate.py";; esac
[ -e "$DEST/settings.json" ] && cp "$DEST/settings.json" "$DEST/settings.json.bak-$STAMP"
"$PY" - "$DEST/settings.json" "$GATE" "$WIN" <<'PYEOF'
import json, os, sys
path, gate, win = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
cfg = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
hook = {"type": "command", "timeout": 30,
        "command": f'python "{gate}"' if win else f'python3 "{gate}"'}
if win:
    hook["shell"] = "powershell"
entry = {"matcher": "Bash|PowerShell|mcp__.*create_pull_request", "hooks": [hook]}
pre = cfg.setdefault("hooks", {}).setdefault("PreToolUse", [])
pre[:] = [e for e in pre if not any("review-gate.py" in h.get("command", "") for h in e.get("hooks", []))]
pre.append(entry)
json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("registered review-gate hook in settings.json")
PYEOF

if command -v claude >/dev/null 2>&1; then
  echo "Claude Code version: $(claude --version) (Haiku 5.5 needs 2.1.293 or later)"
fi
echo "Done. Run /agents in a new session to confirm."
