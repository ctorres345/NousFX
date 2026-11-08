"""One MCP server list for Claude Code and agy. Computes {abs path: new text}; sync does the writing.

Shared list: <repo>/harness/mcp.toml plus optional <state>/mcp.toml (state wins on a name clash).
Project mode reads only <project>/.nousfx/mcp.toml and keeps its ownership record in
<project>/.nousfx/mcp-owned.json (docs/sync.md, MCP). No secret is ever written: env values must be
exactly "${VAR}", and every string is scanned. Env references are NOT expanded here. Whether agy
expands ${VAR} in env is unverified (docs are silent); that is a manual check.
"""
import hashlib
import json
import os
import re
import tomllib
from urllib.parse import parse_qsl, urlsplit

CLAUDE_JSON_REL = ".claude.json"
AGY_MCP_REL = os.path.join(".gemini", "config", "plugins", "nousfx", "mcp_config.json")
PROJECT_CLAUDE_REL = ".mcp.json"
PROJECT_AGY_REL = os.path.join(".agents", "plugins", "nousfx", "mcp_config.json")
OWNED_NAME = "mcp-owned.json"
TOOLS = ("claude", "agy")
NAME = re.compile(r"[A-Za-z0-9_-]+")
ENV_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")
KEYS = {"tools", "url", "command", "args", "env"}
SECRET_QUERY_KEY = re.compile(r"(?i)token|key|secret|passw|auth|sig|credential")
# A credential inside one argument ("Authorization: Bearer x", "X-Api-Key: x", "token=x"), checked after
# any ${VAR} reference is removed, so "Authorization: Bearer ${TOKEN}" passes.
ARG_CREDENTIAL = re.compile(
    r"(?i)\bbearer\s+\S{4,}|\bbasic\s+[A-Za-z0-9+/=]{8,}"
    # name (optionally quoted), then ":" or "=", then a value; a lone scheme word left once ${VAR} is
    # removed ("token ", "Digest ") is not a credential
    r"|(?<![A-Za-z0-9])(?:authorization|cookie|(?:api|access|secret|auth)[-_]?key|token|secret|passw\w*)[\"']?\s*[:=]\s*[\"']?"
    r"(?!(?:bearer|basic)\b)(?!(?:digest|token|ssws|apikey|negotiate)\s+$)\S{4,}")
FLAG_LIKE = re.compile(r"-{1,2}[a-z][a-z-]*(?:=.*)?")  # "--port", "-y"; "-Literal123", "-abc123def" are values
# Exact flag names that take a secret ("--api_key", "--personal-access-token"); "--max-tokens", "--token-file",
# "--no-auth" and key-file flags such as "--private-key" do not.
SECRET_FLAG = re.compile(
    r"(?i)-{1,2}(?:[a-z0-9]+[-_])*(?:token|secret|password|passwd|api[-_]?key|apikey|access[-_]?key|secret[-_]?key|bearer|credentials?)")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _strings(entry):
    yield from (entry.get(k) for k in ("url", "command"))
    yield from entry.get("args", [])


def _check(name, e, SyncError, patterns):
    if not NAME.fullmatch(name):
        raise SyncError(f"mcp: server name {name!r} must match [A-Za-z0-9_-]+")
    if not isinstance(e, dict) or set(e) - KEYS:
        raise SyncError(f"mcp: server {name}: only {sorted(KEYS)} are allowed")
    tools = e.get("tools")  # required: a default of "both" could duplicate Claude's built-in connectors
    if (not isinstance(tools, list) or not tools
            or any(t not in TOOLS for t in tools)):
        raise SyncError(f"mcp: server {name}: tools is required, a non-empty subset of {list(TOOLS)}")
    if ("url" in e) == ("command" in e):
        raise SyncError(f"mcp: server {name}: give exactly one of url or command")
    args, env = e.get("args", []), e.get("env", {})
    if (not isinstance(args, list) or not isinstance(env, dict)
            or not all(isinstance(a, str) for a in args)
            or not all(isinstance(e.get(k, ""), str) for k in ("url", "command"))):
        raise SyncError(f"mcp: server {name}: url, command, args must be strings and env a table")
    for key, val in env.items():
        if not isinstance(val, str) or not ENV_REF.fullmatch(val):
            raise SyncError(f"mcp: server {name}: env {key} must be exactly ${{VAR}}, "
                            "never a literal value")
    for s in _strings(e):
        if s is not None and any(p.search(s) for p in patterns):
            raise SyncError(f"mcp: server {name}: secret scan failed, an entry looks like a secret")
    for a in args:
        if ARG_CREDENTIAL.search(ENV_REF.sub("", a)):
            raise SyncError(f"mcp: server {name}: an argument carries a credential; "
                            "write the secret part as ${VAR}")
    for i, a in enumerate(args):  # a secret flag must take ${VAR}: "--token", "x" and "--token=x" both fail
        flag, eq, val = a.partition("=")
        if SECRET_FLAG.fullmatch(flag):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            value = val if eq else ("" if FLAG_LIKE.fullmatch(nxt) else nxt)
            if value and not ENV_REF.fullmatch(value):
                raise SyncError(f"mcp: server {name}: argument {flag} must be given as ${{VAR}}, "
                                "never a literal value")
    if "url" in e:
        try:
            parts = urlsplit(e["url"])
            pairs = [kv for chunk in (parts.query, parts.fragment)
                     for kv in parse_qsl(chunk.replace(";", "&"), keep_blank_values=True)]
        except ValueError:
            raise SyncError(f"mcp: server {name}: url is not valid")
        if parts.username is not None or parts.password is not None or any(
                v and SECRET_QUERY_KEY.search(k) for k, v in pairs):
            raise SyncError(f"mcp: server {name}: url carries credentials (user info or token query)")
    out = {"tools": list(tools)}
    if "url" in e:
        out["url"] = e["url"]
    else:
        out.update(command=e["command"], args=list(args), env=dict(env))
    return out


def _load_file(path, SyncError, patterns, required=False):
    text = _read(path)
    if text is None:
        if required:  # an empty list would make sync delete every server it owns
            raise SyncError(f"mcp: missing shared list: {path}", 2)
        return {}
    try:
        servers = tomllib.loads(text).get("servers", {})
    except tomllib.TOMLDecodeError as exc:
        raise SyncError(f"mcp: {path} is not valid TOML: {exc}")
    if not isinstance(servers, dict):
        raise SyncError(f"mcp: {path}: [servers.<name>] tables expected")
    return {n: _check(n, e, SyncError, patterns) for n, e in servers.items()}


def load(repo, state, SyncError, patterns, project=None):
    if project:
        return _load_file(os.path.join(project, ".nousfx", "mcp.toml"), SyncError, patterns)
    got = _load_file(os.path.join(repo, "harness", "mcp.toml"), SyncError, patterns, required=True)
    got.update(_load_file(os.path.join(state, "mcp.toml"), SyncError, patterns))
    return got


def _shape(e, tool):
    if "url" in e:
        return {"url": e["url"]} if tool == "agy" else {"type": "http", "url": e["url"]}
    out = {"command": e["command"]}
    if tool == "claude":
        out = {"type": "stdio", **out}
    for k in ("args", "env"):
        if e[k]:
            out[k] = e[k]
    return out


def _dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def unmerge(live_text, saved_text, names):
    """Undo sync's change to a merged file (~/.claude.json, .mcp.json) without touching anything else
    in it: each name goes back to its saved entry, or is dropped if it was not there. Returns the new
    text, or None when sync created the file and nothing else is in it (the caller deletes it).
    Raises ValueError if the live file is not usable JSON."""
    live = json.loads(live_text)
    before = json.loads(saved_text) if saved_text is not None else {}
    if not isinstance(live, dict) or not isinstance(before, dict):
        raise ValueError("not a JSON object")
    old, servers = before.get("mcpServers", {}), live.get("mcpServers", {})
    if not isinstance(servers, dict) or not isinstance(old, dict):
        raise ValueError("mcpServers is not an object")
    for n in names:
        if n in old:
            servers[n] = old[n]
        else:
            servers.pop(n, None)
    if servers or "mcpServers" in before:
        live["mcpServers"] = servers
    else:
        live.pop("mcpServers", None)
    return None if saved_text is None and not live else _dump(live)


def _entry_hash(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _owned_map(value):
    """{name: hash of the entry sync wrote} from one record value. An older record is a plain list of
    names (hash None: no content check). ValueError if the shape is wrong."""
    if isinstance(value, list):
        value = {n: None for n in value}
    if not isinstance(value, dict) or not all(
            isinstance(n, str) and NAME.fullmatch(n) and (h is None or isinstance(h, str))
            for n, h in value.items()):
        raise ValueError("bad record shape")
    return value


def _record_claude(record_text):
    """The record's per-file owned maps, validated. {} when there is no record; ValueError if unusable."""
    if not record_text or not record_text.strip():
        return {}
    try:
        claude = json.loads(record_text).get("claude", {})
        if not isinstance(claude, dict):
            raise ValueError("bad record shape")
        return {p: _owned_map(v) for p, v in claude.items()}
    except (ValueError, TypeError, AttributeError):
        raise ValueError("ownership record is not usable")


def servers_slice(text):
    """What a backup keeps of a merged file: only its mcpServers (JSON text), or {} when it has none.
    An unusable file is saved whole, so nothing is lost; restore then reports it the same way."""
    try:
        data = json.loads(text)
        return _dump({"mcpServers": data["mcpServers"]} if "mcpServers" in data else {})
    except (ValueError, TypeError):
        return text


def owned_names(record_text, key):
    """Names the ownership record lists under `key` (see record_keys). Empty if there is no record; ValueError
    if there is one and it is unusable (restore must not guess which servers sync added). A repo record (key
    ".mcp.json") must have hashes: a cloned repo must not claim a server the user added."""
    owned = _record_claude(record_text).get(key, {})
    if key == PROJECT_CLAUDE_REL and any(h is None for h in owned.values()):
        raise ValueError("repo record without hashes")
    return list(owned)


def project_record(project):
    return os.path.join(project, ".nousfx", OWNED_NAME)


def record_keys(state, path):
    """(record path, key) pairs that may list the servers sync added to a merged file: the state record by
    absolute path (global files, and project files synced before NFX-99), plus the repo's own record for a
    project .mcp.json."""
    keys = [(os.path.join(state, OWNED_NAME), path)]
    if os.path.basename(path) == PROJECT_CLAUDE_REL:
        keys.append((project_record(os.path.dirname(path)), PROJECT_CLAUDE_REL))
    return keys


def _load_record(rec_path, SyncError):
    """(old text, record dict, its "claude" maps) for one ownership record; an absent or blank record is empty
    (old text None)."""
    rec_old = _read(rec_path)
    if rec_old is not None and not rec_old.strip():
        rec_old = None
    try:
        record = json.loads(rec_old) if rec_old else {}
        maps = _record_claude(rec_old)
        record["claude"] = dict(maps)
    except (ValueError, AttributeError, TypeError):
        raise SyncError(f"mcp: {rec_path} is not usable JSON; delete it only if you know what "
                        "sync owns", code=2)
    return rec_old, record, record["claude"]


def _merge_claude(path, entries, owned, SyncError, hint=""):
    """Merge sync-owned names into the file's mcpServers. `owned` is {name: hash of what sync wrote}.
    Returns (text or None, {name: hash} now owned)."""
    old = _read(path)
    try:
        data = json.loads(old) if old is not None else {}
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcpServers is not an object")
    except (ValueError, AttributeError) as exc:
        raise SyncError(f"mcp: {path} is not usable JSON ({exc}); fix it by hand", code=2)
    names = sorted(entries)
    for n in names:
        if n in servers and n not in owned:
            raise SyncError(f"mcp: {path} already has a server named {n}: not mine, left untouched. "
                            f"Rename it in the shared list or remove it by hand.{hint}")
    for n, h in owned.items():  # a name in the record is mine only while its content is still what I wrote
        # "new+old": a run stopped between the record and the file leaves the old entry, which is mine too
        if n in servers and h is not None and _entry_hash(servers[n]) not in h.split("+") \
                and servers[n] != entries.get(n):
            raise SyncError(f"mcp: {path}: server {n} is not what sync wrote (changed by hand or by Claude), "
                            "left untouched. Remove or restore it by hand, or run --restore.")
    new_owned = {}
    for n in names:  # "new+old" only while the file still holds something else that sync wrote
        h = _entry_hash(entries[n])
        on_disk = _entry_hash(servers[n]) if n in servers else h
        new_owned[n] = h if on_disk == h or owned.get(n) is None else f"{h}+{on_disk}"
    merged = {k: v for k, v in servers.items() if k not in owned or k in entries}
    merged.update({n: entries[n] for n in names})
    if merged == servers and (old is not None or not merged):
        return old, new_owned
    if merged or "mcpServers" in data:
        data["mcpServers"] = merged
    return _dump(data), new_owned


def plan_mcp(repo, home, state, tools, SyncError, patterns, project=None):
    servers = load(repo, state, SyncError, patterns, project)
    result = {}
    if "agy" in tools:
        path = os.path.join(project, PROJECT_AGY_REL) if project else os.path.join(home, AGY_MCP_REL)
        result[path] = _dump({"mcpServers": {n: _shape(e, "agy") for n, e in servers.items()
                                             if "agy" in e["tools"]}})
    if "claude" in tools:
        if project:
            path = os.path.join(project, PROJECT_CLAUDE_REL)
            rec_path, key = project_record(project), PROJECT_CLAUDE_REL
            hint = (" If another checkout of this repo synced it, commit that checkout's "
                    ".nousfx/mcp-owned.json with .mcp.json.")
        else:
            path = os.path.join(home, CLAUDE_JSON_REL)
            rec_path, key, hint = os.path.join(state, OWNED_NAME), path, ""
        rec_old, record, claude = _load_record(rec_path, SyncError)
        owned = claude.get(key, {})
        records = [(rec_path, rec_old, record)]
        if project:
            if any(h is None for h in owned.values()):  # a repo record never predates hashes
                raise SyncError(f"mcp: {rec_path} lists servers without hashes, so sync cannot tell its own "
                                "entries from yours. Delete that file only if you know what sync owns.", code=2)
            # before NFX-99 the state record held this file by absolute path: move it into the repo
            st_path = os.path.join(state, OWNED_NAME)
            try:
                st_old, st_record, st_claude = _load_record(st_path, SyncError)
            except SyncError:
                if rec_old is None:  # nothing else says what sync owns here
                    raise
                # the repo record is enough; leave the unusable state record alone
            else:
                legacy = st_claude.pop(path, None)
                if legacy is not None:  # the state record is mine (home folder), so its unhashed entries are fine
                    owned = {**legacy, **owned}  # the repo record wins on a name both list
                    records.append((st_path, st_old, st_record))
        entries = {n: _shape(e, "claude") for n, e in servers.items() if "claude" in e["tools"]}
        text, names = _merge_claude(path, entries, owned, SyncError, hint) if entries or owned else (None, {})
        if text is not None:
            result[path] = text
        if names:
            claude[key] = names
        else:
            claude.pop(key, None)
        head = {}  # records first, so a stopped run never leaves a server in the file without its record
        for p, old, rec in records:  # the state record is listed in project mode only when it held an entry
            new = _dump(rec)
            if new != old and (old is not None or names):
                head[p] = new
        result = {**head, **result}
    return result
