# Shared safety checks for review.sh and ui-review's argon-review.sh. Source it, then call the functions.
unset CDPATH
cli_version="0.4.5"
cli="$HOME/.local/bin/gemini-api"

check_cli() {
  [ -x "$cli" ] || { echo "gemini-api is not installed" >&2; exit 3; }
  "$cli" version 2>/dev/null | grep -Eq '(^|[^0-9.])0\.4\.5($|[^0-9.])' || {
    echo "Stopped: gemini-api is not the pinned $cli_version. Nothing was sent." >&2
    exit 3
  }
}

go_to_root() {
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "Stopped: not in a git repo. Nothing was sent." >&2; exit 3; }
  cd "$root"
}

# Reads origin on purpose: a bare `gh repo view` prefers `upstream`, so a private fork would read as public.
read_repo() {
  repo=""; visibility=""
  local url
  url="$(git remote get-url origin 2>/dev/null || true)"
  if [ -n "$url" ]; then
    read -r repo visibility <<<"$(gh repo view "$url" --json nameWithOwner,visibility -q '.nameWithOwner + " " + .visibility' 2>/dev/null || true)"
  fi
  visibility="${visibility:-UNKNOWN}"
}

check_range() {
  case "$1" in -*) echo "Stopped: bad range $1. Nothing was sent." >&2; exit 3 ;; esac
}

excludes=()
for p in '.env*' '*.env' '*.env.*' '*password*' '.pgpass' '.pypirc' '.dockercfg' 'kubeconfig' '*.ks' '*.keystore' '*.jks' '*.pem' '*.p12' '*.p8' '*.pfx' '*.key' '*.tfvars' '*.mobileprovision' \
  'key.properties' 'local.properties' 'google-services.json' 'GoogleService-Info.plist' \
  'id_rsa*' 'id_ed25519*' '.npmrc' '.netrc' '*secret*' '*credential*'; do
  excludes+=(":(exclude,icase,glob)**/$p")
done
excludes+=(':(exclude,icase,glob)**/.env*/**' ':(exclude,icase,glob)**/*secret*/**' ':(exclude,icase,glob)**/*credential*/**')

# A here-string, not a pipe: with pipefail, grep -q closing a pipe early reads as no match on large input.
# LC_ALL=C: in UTF-8, grep misses a secret on a line with invalid bytes. Any grep error also stops.
secret_re='(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{20,}|glpat-[0-9A-Za-z_-]{20,}|(^|[^0-9A-Za-z_-])sk-[0-9A-Za-z_-]{20,}|[sr]k_live_[0-9A-Za-z]{10,}|xox[baprs]-[0-9A-Za-z-]{10,}|eyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY)'
stop_on_secret() {
  local rc=0
  LC_ALL=C grep -Eq "$secret_re" <<<"$1" || rc=$?
  if [ "$rc" -ne 1 ]; then
    echo "Stopped: the text looks like it contains a secret, or the scan failed. Nothing was sent." >&2
    exit 7
  fi
}
