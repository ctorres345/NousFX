"""nousfx_outside: fetch the pinned outside skills (harness/skills.lock.json). Used by nousfx_sync.py.

The lock has the same format as plugins/via/skills.lock.json: for each skill a repo, a path in that
repo, a 40-character commit and the sha256 of the skill folder. This module reuses the checks of
plugins/via/scripts/via_skills.py (https only, pinned commit, tree read from git objects, size limits,
files written as 0644, nothing run). A skill is fetched at most once:

  <state>/outside-skills/<name>-<first 12 of sha256>/   the verified files

Linking goes through nousfx_skills (manifest, collisions, backups, --restore, Windows junctions): the
cache folder is just one more skill source. A skill that fails (fetch error, checksum mismatch, unsafe
tree) is reported and left out. Nothing is written for it, and the links it already has stay as they are.
A cache folder is never deleted, because --restore may link back to an older pin. A cache folder that was
edited (it is the target of the links) is moved to <state>/outside-skills/.edited-*/ before the pinned files
are put back.

NOUSFX_ALLOW_FILE_URLS=1 accepts file:// repos. It is test-only.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "plugins", "via", "scripts"))
import via_skills  # noqa: E402

LOCK = os.path.join("harness", "skills.lock.json")


def allow_file():
    return os.environ.get("NOUSFX_ALLOW_FILE_URLS") == "1"


def load(repo, shipped, error):
    """name -> lock entry. No lock file means no outside skills. `error` is raised (with a message)
    for a bad lock or for a name NousFX ships itself."""
    path = os.path.join(repo, LOCK)
    if not os.path.isfile(path):
        return {}
    try:
        skills = via_skills.load_lock(path, allow_file())["skills"]
    except via_skills.Fail as e:
        raise error(str(e))
    shipped_keys = {via_skills.fold(n) for n in shipped}  # two names that differ only by case are one folder on macOS and Windows
    clash = sorted(n for n in skills if via_skills.fold(n) in shipped_keys)
    if clash:
        raise error("%s lists %s, which NousFX ships itself; remove it from the lock" % (LOCK, ", ".join(clash)))
    return skills


def cache_dir(state, name, entry):
    return os.path.join(state, "outside-skills", "%s-%s" % (name, entry["sha256"][:12]))


JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _filtered(files):
    return {rel: v for rel, v in files.items()
            if not (set(rel.split("/")) & (JUNK_NAMES | {"__pycache__"}) or rel.endswith(".pyc"))}


def matches(files, sha256):
    """True when the files hold the pin. Junk the OS or a skill's own scripts add (.DS_Store, __pycache__,
    *.pyc) does not count as an edit; a pin that really ships such files matches on the full tree. This guards
    the fetch, not the local disk: someone who can write here can edit the skill's scripts as well."""
    return files is not None and sha256 in (via_skills.checksum(files), via_skills.checksum(_filtered(files)))


def status(state, lock):
    """name -> 'ok', 'missing' or 'edited' (the cache folder no longer matches the pin)."""
    out = {}
    for name, e in lock.items():
        folder = cache_dir(state, name, e)
        try:
            files = via_skills.disk_tree(folder)
        except OSError:  # an unreadable file: the folder cannot be trusted
            out[name] = "edited"
            continue
        if files is None and os.path.lexists(folder):  # a link, a file or a folder with a link in it
            out[name] = "edited"
            continue
        out[name] = ("missing" if files is None else
                     "ok" if matches(files, e["sha256"]) else "edited")
    return out


def same_as_lock(path, sha256):
    """True when the folder at `path` (a link is followed) holds exactly the pinned files."""
    try:
        return matches(via_skills.disk_tree(os.path.realpath(path)), sha256)
    except OSError:
        return False


def _stage(sources):
    """{name: files} for [(name, repo, path, commit)]. A group that shares a repo and commit is fetched once;
    when that fails, each skill is tried alone so one bad skill does not stop the rest."""
    done, failed = {}, {}
    groups = {}
    for s in sources:
        groups.setdefault((s[1], s[3]), []).append(s)
    for group in groups.values():
        try:
            done.update(via_skills.stage_many(group, allow_file()))
            continue
        except (via_skills.Fail, OSError):
            pass
        for s in group:
            try:
                done.update(via_skills.stage_many([s], allow_file()))
            except (via_skills.Fail, OSError) as e:
                failed[s[0]] = str(e)
    return done, failed


def ensure(state, lock, note=lambda message: None):
    """Fetch and verify every skill whose cache folder is missing or edited. Returns {name: reason}
    for the ones that failed; those have nothing new on disk. note(message) is told where an edited
    folder was moved to."""
    todo = [n for n, s in status(state, lock).items() if s != "ok"]
    staged, failed = _stage([(n, lock[n]["repo"], lock[n]["path"], lock[n]["commit"]) for n in todo])
    root = os.path.join(state, "outside-skills")
    for name in todo:
        if name in failed:
            continue
        e = lock[name]
        got = via_skills.checksum(staged[name])
        if got != e["sha256"]:
            failed[name] = ("checksum mismatch: the lock says %s, commit %s gives %s; nothing installed"
                            % (e["sha256"], e["commit"], got))
            continue
        final = cache_dir(state, name, e)
        scratch = None
        try:
            os.makedirs(root, exist_ok=True)
            scratch = tempfile.mkdtemp(prefix=".stage-%s-" % name, dir=root)  # its own folder: runs do not share it
            tmp = os.path.join(scratch, "files")
            via_skills.write_skill(tmp, staged[name])
            on_disk = via_skills.disk_tree(tmp)  # what this disk really holds must still match the pin
            if not matches(on_disk, e["sha256"]):
                failed[name] = "the files written do not match the lock; nothing installed"
                continue
            aside = None
            if os.path.lexists(final):  # an edited copy: keep it, the user may have worked in it through a link
                holder = tempfile.mkdtemp(prefix=".edited-%s-" % os.path.basename(final), dir=root)
                aside = os.path.join(holder, "old")
                try:
                    os.replace(final, aside)
                except OSError:
                    os.rmdir(holder)
                    raise
            try:
                os.replace(tmp, final)
            except OSError:
                if aside:
                    os.replace(aside, final)
                    os.rmdir(holder)
                raise
            if aside:
                note("outside skill %s was edited; your copy is kept at %s (the pinned files are back)" % (name, aside))
        except (via_skills.Fail, OSError) as err:
            failed[name] = str(err)
        finally:
            if scratch:
                shutil.rmtree(scratch, ignore_errors=True)
    return failed
