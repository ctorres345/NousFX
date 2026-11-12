#!/usr/bin/env python3
"""Run a command; on failure, summarize its output with a local Ollama model.

Usage: python3 -I -B local_summary.py [--state-home DIR] -- <cmd...>

PASS            exit 0, prints "PASS <cmd>", reads no config, makes no request.
FAIL exit=N     prints it plus a summary of at most 120 words, exits N.
unavailable     prints one line "local-assist unavailable: <reason>" and exits 3.

Config: <state home>/local.toml, table [local_assist] with url and model. State home defaults to
~/.config/nousfx; --state-home or NOUSFX_STATE_HOME overrides it. The URL must resolve only to
loopback or private addresses, checked before any request. Needs Python 3.11+.
"""
import http.client
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True

NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
MAX_LOG = 12000
BLOCK_BUDGET = 7500
TAIL = 4000
MAX_WORDS = 120
KEYS = re.compile(r"FAIL|ERROR|Error|Traceback|assert|panic|error:")


class Unavailable(Exception):
    pass


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def load_config(state_home):
    path = os.path.join(state_home, "local.toml")
    try:
        with open(path, "rb") as f:
            table = tomllib.load(f).get("local_assist")
    except FileNotFoundError:
        raise Unavailable("no local.toml")
    except (OSError, ValueError):  # ValueError: bad TOML or bytes that are not UTF-8
        raise Unavailable("local.toml unreadable")
    if not isinstance(table, dict):
        raise Unavailable("no [local_assist] in local.toml")
    url, model = table.get("url"), table.get("model")
    if not (isinstance(url, str) and url and isinstance(model, str) and model):
        raise Unavailable("[local_assist] needs url and model")
    return url.rstrip("/"), model


def check_url(url):
    """Return url with its host replaced by the address checked here, so the requests cannot resolve
    the name again to a different (public) address. Raises Unavailable unless every address is
    loopback or private."""
    try:
        parts = urllib.parse.urlsplit(url)
        host_name, port_number = parts.hostname, parts.port
    except ValueError:
        raise Unavailable("url is invalid")
    if parts.scheme != "http" or not host_name:  # the checked IP replaces the host, which would break TLS
        raise Unavailable("url must be http")
    try:
        infos = socket.getaddrinfo(host_name, port_number or 80, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError):
        raise Unavailable("url host does not resolve")
    if not infos:
        raise Unavailable("url host does not resolve")
    for info in infos:
        addr = ipaddress.ip_address(info[4][0].split("%")[0])
        if not any(addr in net for net in NETWORKS):
            raise Unavailable("url is not loopback or private")
    pick = next((i for i in infos if i[0] == socket.AF_INET), infos[0])[4][0].split("%")[0]
    host = "[%s]" % pick if ":" in pick else pick
    port = ":%d" % port_number if port_number else ""
    return urllib.parse.urlunsplit((parts.scheme, host + port, parts.path, "", "")).rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def request(opener, url, timeout, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def trim(log):
    if len(log) <= MAX_LOG:
        return log
    lines = log.splitlines()
    keep = set()
    for i, line in enumerate(lines):
        if KEYS.search(line):
            keep.update(range(max(0, i - 2), min(len(lines), i + 6)))
    out, size, prev = [], 0, -2
    for i in sorted(keep):
        if size + len(lines[i]) + 1 > BLOCK_BUDGET:
            continue  # one huge line must not hide the failure blocks after it
        if i != prev + 1:
            out.append("[...]")
        out.append(lines[i])
        size += len(lines[i]) + 1
        prev = i
    return "\n".join(out) + "\n[... tail ...]\n" + log[-TAIL:]


def summarize(state_home, log):
    url, model = load_config(state_home)
    url = check_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)
    try:
        request(opener, url + "/api/version", env_float("LOCAL_ASSIST_VERSION_TIMEOUT", 2))
        tags = request(opener, url + "/api/tags", env_float("LOCAL_ASSIST_VERSION_TIMEOUT", 2))
        names = set()
        for m in tags.get("models", []):
            names.update(str(m.get(k)) for k in ("name", "model") if m.get(k))
        if model not in names and model + ":latest" not in names:
            raise Unavailable("model %s not on the host" % model)
        prompt = ("A command failed. Below is the end of its output. In at most 120 words say: "
                  "which tests or steps failed, the likely cause, and which file to look at first. "
                  "No preamble.\n\n" + trim(log))
        body = {"model": model, "prompt": prompt, "stream": False, "think": False,
                "options": {"num_ctx": 12288, "temperature": 0}}
        reply = request(opener, url + "/api/generate", env_float("LOCAL_ASSIST_GENERATE_TIMEOUT", 120), body)
    except Unavailable:
        raise
    except (OSError, http.client.HTTPException, urllib.error.URLError, TimeoutError, ValueError,
            AttributeError, TypeError) as e:
        raise Unavailable("%s: %s" % (type(e).__name__, str(getattr(e, "reason", e))[:80]))
    text = reply.get("response") if isinstance(reply, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise Unavailable("empty reply")
    return " ".join(text.split()[:MAX_WORDS])


def main(argv):
    sys.stdout.reconfigure(errors="replace")  # -I ignores PYTHONIOENCODING; a cp1252 console must not crash on a symbol
    state_home = os.environ.get("NOUSFX_STATE_HOME") or os.path.expanduser("~/.config/nousfx")
    args = list(argv)
    cut = args.index("--") if "--" in args else None
    head, cmd = (args[:cut], args[cut + 1:]) if cut is not None else ([], [])
    if head[:1] == ["--state-home"] and len(head) == 2:
        state_home = head[1]
        head = []
    if head or not cmd:
        print("usage: local_summary.py [--state-home DIR] -- <cmd...>", file=sys.stderr)
        return 2
    shown = " ".join(cmd)
    cmd[0] = shutil.which(cmd[0]) or cmd[0]  # Windows: finds npm.cmd and other shims
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        print("FAIL exit=127\ncannot run %s: %s" % (cmd[0], e.strerror or e))
        return 127
    code = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode  # killed by signal: shell style
    if code == 0:
        print("PASS " + shown)
        return 0
    log = proc.stdout.decode("utf-8", "replace")
    try:
        summary = summarize(state_home, log)
    except Unavailable as e:
        print("local-assist unavailable: %s" % e)
        return 3
    print("FAIL exit=%d\n%s" % (code, summary))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
