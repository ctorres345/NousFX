#!/usr/bin/env python3
"""Tests for pr_evidence.py, with a fake `gh` on PATH. No network."""
import getpass
import re
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "pr_evidence.py")
VIA_RUN = os.path.join(HERE, "..", "plugins", "via", "scripts", "via_run.py")

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, shutil, sys
state, args = os.environ["FAKE_GH_STATE"], sys.argv[1:]
mode = os.environ.get("FAKE_GH_MODE", "")
with open(os.path.join(state, "log"), "a") as f:
    f.write(json.dumps(args) + "\n")
assets = os.path.join(state, "assets")
if args[:2] == ["repo", "view"]:
    print("owner/repo"); sys.exit(0)
if mode == "forbidden" or (mode == "no-upload" and args[:2] == ["release", "upload"]):
    sys.stderr.write("HTTP 403: Resource not accessible by integration\n"); sys.exit(1)
if args[:2] == ["release", "view"]:
    if os.path.isdir(assets):
        print("{}"); sys.exit(0)
    sys.stderr.write("release not found\n"); sys.exit(1)
if args[:2] == ["release", "create"]:
    os.makedirs(assets); sys.exit(0)
if args[:2] == ["release", "upload"]:
    n = len(os.listdir(assets))
    if mode == "fail-second" and n == 1:
        sys.stderr.write("HTTP 502: Bad Gateway\n"); sys.exit(1)
    if os.path.exists(os.path.join(assets, os.path.basename(args[3]))) and "--clobber" not in args:
        sys.stderr.write("asset under the same name already exists\n"); sys.exit(1)
    shutil.copy(args[3], assets); sys.exit(0)
sys.exit(2)
'''


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def png(*extra):
    head = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    return (b"\x89PNG\r\n\x1a\n" + head + b"".join(extra) + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
            + chunk(b"IEND", b""))


def jpeg(*segments, between=b""):
    """A JPEG with two scans; `between` sits between them, as in a progressive file."""
    scan = b"\xff\xda\x00\x02pix\xff\x00els\xff\xd0more"
    return b"\xff\xd8" + b"".join(segments) + scan + between + scan + b"\xff\xd9"


JFIF = b"JFIF\x00\x01\x02\x00\x00\x48\x00\x48"  # identifier, version, units, density; the thumbnail size follows


def seg(marker, body):
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def webp(*chunks):
    body = b"WEBP" + b"".join(k + struct.pack("<I", len(b)) + b + (b"\x00" if len(b) & 1 else b"") for k, b in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif(*blocks):
    return b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x00\x00\x00" + b"".join(blocks) + b"\x3b"


IMAGE = b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02\x02\x44\x01\x00"


def box(kind, body):
    return struct.pack(">I", len(body) + 8) + kind + body


def hdlr(kind, name):
    return box(b"hdlr", b"\x00" * 8 + kind + b"\x00" * 12 + name + b"\x00")


class PrEvidenceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.project = os.path.join(self.tmp, "project")
        os.makedirs(self.project)
        subprocess.run(["git", "init", "-q", self.project], check=True)  # via_run init needs .via/ ignored by git
        self.state = os.path.join(self.tmp, "gh")
        os.makedirs(self.state)
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        gh = os.path.join(bin_dir, "gh")
        with open(gh, "w") as f:
            f.write(FAKE_GH.replace("/usr/bin/env python3", sys.executable, 1))
        os.chmod(gh, 0o755)
        self.env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], FAKE_GH_STATE=self.state)

    def run_dir(self, files, reviews=None, extra=None):
        """A Via run folder with these files; each is kept unless reviews says otherwise."""
        r = subprocess.run([sys.executable, "-I", "-B", VIA_RUN, "init", "--project", self.project],
                           capture_output=True, text=True, check=True)
        run_dir = json.loads(r.stdout)["run_dir"]
        shots = []
        for name, data in files.items():
            with open(os.path.join(run_dir, name), "wb") as f:
                f.write(data)
            review = (reviews or {}).get(name, "kept")
            shots.append({"path": name, "screen": "home", "theme": "light", "size": "phone", "redacted": True,
                          "review": review, **({"public": True} if review == "kept" else {}),
                          **(extra or {}).get(name, {})})
        rec = {"version": 1, "mode": "check", "scope": "all", "platforms": ["web"], "findings": [], "screenshots": shots}
        with open(os.path.join(run_dir, "findings.json"), "w") as f:
            json.dump(rec, f)
        self.p = "NFX-87-%s-" % os.path.basename(run_dir).lower()  # the asset name prefix for this run
        return run_dir

    def publish(self, run_dir, key="NFX-87", mode=""):
        env = dict(self.env, FAKE_GH_MODE=mode)
        return subprocess.run([sys.executable, "-I", "-B", SCRIPT, "publish", key, run_dir, "--project", self.project],
                              capture_output=True, text=True, env=env)

    def log(self):
        path = os.path.join(self.state, "log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]

    def calls(self, verb):
        return [a for a in self.log() if a[:2] == ["release", verb]]

    def uploads(self):
        return self.calls("upload")

    def asset(self, name):
        with open(os.path.join(self.state, "assets", name), "rb") as f:
            return f.read()

    def test_kept_shots_upload_and_print_a_markdown_table(self):
        run = self.run_dir({"home-light-phone.png": png(), "home-dark-phone.png": png(), "home-old.png": png()},
                           {"home-old.png": "dropped"})
        r = self.publish(run)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, "assets"))),
                         [self.p + "home-dark-phone.png", self.p + "home-light-phone.png"])
        self.assertIn("| Shot | Evidence |", r.stdout)
        self.assertIn("![{0}home-light-phone](https://github.com/owner/repo/releases/download/evidence/"
                      "{0}home-light-phone.png)".format(self.p), r.stdout)
        self.assertNotIn("home-old", r.stdout)
        create = self.calls("create")
        self.assertEqual(len(create), 1)
        self.assertIn("--prerelease", create[0])
        for a in self.uploads():
            self.assertIn("--clobber", a)
            self.assertEqual(a[a.index("--repo") + 1], "owner/repo")

    def test_a_rerun_replaces_the_assets_without_duplicates_or_a_second_release(self):
        run = self.run_dir({"home-light-phone.png": png()})
        self.assertEqual(self.publish(run).returncode, 0)
        self.assertEqual(self.publish(run).returncode, 0)
        self.assertEqual(os.listdir(os.path.join(self.state, "assets")), [self.p + "home-light-phone.png"])
        self.assertEqual(len(self.calls("create")), 1)
        self.assertEqual(len(self.uploads()), 2)

    def test_a_file_outside_the_allow_list_stops_everything_before_any_upload(self):
        run = self.run_dir({"home-light-phone.png": png(), "notes.txt": b"hello"})
        r = self.publish(run)
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes.txt", r.stderr)
        self.assertEqual(self.uploads(), [])

    def test_a_folder_that_is_not_a_via_run_folder_is_refused(self):
        other = os.path.join(self.project, "shots")
        os.makedirs(other)
        with open(os.path.join(other, "a.png"), "wb") as f:
            f.write(png())
        r = self.publish(other)
        self.assertEqual(r.returncode, 1)
        self.assertIn("kept shots", r.stderr)
        self.assertEqual(self.uploads(), [])

    def test_a_run_with_no_kept_shot_uploads_nothing(self):
        r = self.publish(self.run_dir({"a.png": png()}, {"a.png": "dropped"}))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.uploads(), [])

    def test_a_shot_name_with_capitals_or_spaces_is_refused(self):
        r = self.publish(self.run_dir({"Home Shot.png": png()}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("lowercase", r.stderr)

    def test_metadata_is_removed_from_every_allowed_format(self):
        secret = b"Author: someone on /Users/QA"
        files = {
            "a.png": png(chunk(b"tEXt", b"Comment\x00" + secret), chunk(b"eXIf", secret), chunk(b"tIME", b"\x07" * 7),
                         chunk(b"prVt", secret), chunk(b"iCCP", b"Display\x00\x00" + zlib.compress(secret))),
            "b.jpg": jpeg(seg(0xE0, JFIF + b"\x01\x01" + b"thumbnail"), seg(0xE1, b"Exif\x00\x00" + secret), seg(0xEC, secret), seg(0xE2, b"MPF\x00" + secret),
                          seg(0xE2, b"ICC_PROFILE\x00" + secret), seg(0xEE, b"Other" + secret),
                          seg(0xFE, secret), between=seg(0xFE, secret) + seg(0xE1, secret)) + secret,
            "c.webp": webp((b"VP8X", b"\x2c" + b"\x00" * 9), (b"ICCP", secret), (b"VP8 ", b"frame"), (b"EXIF", secret),
                           (b"XMP ", secret), (b"ABCD", secret)),
            "d.gif": gif(b"\x21\xfe" + bytes([len(secret)]) + secret + b"\x00",
                         b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00",
                         b"\x21\xff\x0bXMP DataXMP" + bytes([len(secret)]) + secret + b"\x00",
                         b"\x21\x99" + bytes([len(secret)]) + secret + b"\x00", IMAGE),
            "e.mp4": box(b"ftyp", b"isom\x00\x00\x02\x00") + box(b"free", secret)
                     + box(b"moov", box(b"mvhd", b"\x00" * 4 + b"\xe1" * 8 + b"\x00" * 8)
                           + box(b"udta", box(b"\xa9too", secret)) + box(b"GPMF", secret)
                           + box(b"trak", box(b"mdia", hdlr(b"vide", secret)))),
        }
        r = self.publish(self.run_dir(files))
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in files:
            data = self.asset(self.p + "" + name)
            with self.subTest(name=name):
                self.assertNotIn(b"someone", data)
        self.assertEqual(self.asset(self.p + "a.png"), png())
        self.assertEqual(self.asset(self.p + "b.jpg"), jpeg(seg(0xE0, JFIF + b"\x00\x00")))
        self.assertEqual(self.asset(self.p + "c.webp")[20], 0)  # the VP8X EXIF and XMP flags are cleared
        self.assertIn(b"NETSCAPE2.0", self.asset(self.p + "d.gif"))
        mp4 = self.asset(self.p + "e.mp4")
        self.assertEqual(len(mp4), len(files["e.mp4"]))  # same size, so sample offsets still hold
        self.assertIn(b"free", mp4)
        self.assertNotIn(b"\xe1", mp4)  # the recording times are zeroed
        self.assertIn(b"vide", mp4)
        self.assertIn("[{0}e](https://github.com/owner/repo/releases/download/evidence/{0}e.mp4)".format(self.p), r.stdout)
        self.assertNotIn("![%se]" % self.p, r.stdout)

    def test_a_damaged_file_is_refused_not_crashed_on(self):
        files = {"a.png": png()[:-12], "b.webp": b"RIFF\x0c\x00\x00\x00WEBPVP8X\x00\x00\x00\x00",
                 "c.jpg": b"\xff\xd8\xff\xda\x00\x02pix",
                 "d.gif": b"GIF89a\x01\x00\x01\x00\x87\x00\x00" + b"\x00" * 30,  # a colour table longer than the file
                 "e.gif": gif(IMAGE)[:-1]}  # no trailer
        for name, data in files.items():
            with self.subTest(name=name):
                r = self.publish(self.run_dir({name: data}))
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("damaged", r.stderr)

    def test_a_masked_kept_shot_without_public_approval_is_refused(self):
        for extra in ({}, {"public": False}):
            with self.subTest(extra=extra):
                shot = {"redacted": True, "review": "kept", **extra}
                run = self.run_dir({"a.png": png(), "b.png": png()}, extra={"b.png": shot})
                with open(os.path.join(run, "findings.json")) as f:
                    rec = json.load(f)
                if not extra:
                    rec["screenshots"][1].pop("public")
                    with open(os.path.join(run, "findings.json"), "w") as f:
                        json.dump(rec, f)
                r = self.publish(run)
                self.assertEqual(r.returncode, 1)
                self.assertIn("b.png has no public approval", r.stderr)
                self.assertEqual(self.uploads(), [])

    def test_an_unmasked_shot_goes_only_when_it_is_an_emulator_demo_or_preview_shot(self):
        r = self.publish(self.run_dir({"a.png": png(), "b.png": png()}, extra={"b.png": {"redacted": False}}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("b.png is not masked", r.stderr)
        self.assertEqual(self.uploads(), [])
        phone = {"a.png": {"redacted": False, "source": "device", "emulator": False}}
        r = self.publish(self.run_dir({"a.png": png()}, extra=phone))
        self.assertEqual(r.returncode, 1)
        self.assertIn("a.png is not masked", r.stderr)
        self.assertEqual(self.uploads(), [])
        native = {"a.png": {"redacted": False, "source": "device", "emulator": True},
                  "b.png": {"redacted": False, "source": "preview"}}
        r = self.publish(self.run_dir({"a.png": png(), "b.png": png()}, extra=native))
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_an_mp4_with_a_timed_metadata_track_is_refused(self):
        empty = box(b"ftyp", b"isom\x00\x00\x02\x00") + box(b"moov", box(b"mvhd", b""))
        r = self.publish(self.run_dir({"a.mp4": empty}))
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("damaged", r.stderr)
        mp4 = box(b"ftyp", b"isom\x00\x00\x02\x00") + box(b"moov", box(b"trak", box(b"mdia", hdlr(b"meta", b""))))
        r = self.publish(self.run_dir({"a.mp4": mp4}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not video or sound", r.stderr)
        self.assertEqual(self.uploads(), [])

    def test_before_and_after_runs_with_the_same_shot_names_keep_both(self):
        before = self.run_dir({"home-light-phone.png": png()})
        time.sleep(1.1)  # run folders are named by the second
        after = self.run_dir({"home-light-phone.png": png()})
        self.assertNotEqual(os.path.basename(before), os.path.basename(after))
        self.assertEqual(self.publish(before).returncode, 0)
        self.assertEqual(self.publish(after).returncode, 0)
        self.assertEqual(len(os.listdir(os.path.join(self.state, "assets"))), 2)

    def test_a_lowercase_uuid_in_the_name_is_refused(self):
        r = self.publish(self.run_dir({"0f8e2c1a-1b2c-4d3e-8f90-a1b2c3d4e5f6.png": png()}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("device id", r.stderr)

    def test_a_shot_named_after_the_user_is_refused(self):
        user = getpass.getuser().lower()
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", user):
            self.skipTest("the user name is not a valid shot name")
        r = self.publish(self.run_dir({"%s-home.png" % user: png()}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("user, home or host name", r.stderr)
        self.assertEqual(self.uploads(), [])

    def test_a_short_name_is_matched_only_as_a_whole_part_of_a_shot_name(self):
        out = subprocess.run([sys.executable, "-I", "-B", "-c",
                              "import sys; sys.path.insert(0, %r); import pr_evidence as p, socket, getpass, os\n"
                              "getpass.getuser = lambda: 'max'; os.path.expanduser = lambda _: '/home/me'\n"
                              "socket.gethostname = lambda: 'studio-box.local'\n"
                              "names, _ = p.identifiers()\n"
                              "for n in ('run-maxwidth', 'run-max', 'run-xstudio-boxx'): print(bool(p.leak(n.encode(), names)))"
                              % HERE], capture_output=True, text=True)
        self.assertEqual(out.stdout.split(), ["False", "True", "True"], out.stderr)

    def test_an_emulator_serial_in_the_name_is_refused(self):
        r = self.publish(self.run_dir({"emulator-5554.png": png()}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("emulator-5554", r.stderr)

    def test_a_token_without_release_rights_fails_with_the_scope_to_add(self):
        r = self.publish(self.run_dir({"a.png": png()}), "NFX-87", "forbidden")
        self.assertEqual(r.returncode, 3)
        self.assertIn("gh auth refresh -s repo", r.stderr)
        self.assertIn("Contents: read and write", r.stderr)

    def test_a_token_that_can_read_but_not_upload_gets_the_scope_hint(self):
        r = self.publish(self.run_dir({"a.png": png()}), "NFX-87", "no-upload")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("gh auth refresh -s repo", r.stderr)

    def test_a_failed_upload_leaves_only_the_uploaded_assets_in_the_table(self):
        r = self.publish(self.run_dir({"a.png": png(), "b.png": png()}), "NFX-87", "fail-second")
        self.assertEqual(r.returncode, 4)
        self.assertIn(self.p + "a", r.stdout)
        self.assertNotIn(self.p + "b", r.stdout)
        self.assertIn("1 of 2 uploads failed", r.stderr)

    def test_a_key_that_is_not_a_ticket_key_is_refused(self):
        run = self.run_dir({"a.png": png()})
        for key in ("nfx-87", "NFX", "NFX-0", "../NFX-1"):
            with self.subTest(key=key):
                r = self.publish(run, key)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(self.log(), [])

    def test_a_missing_project_folder_is_refused_without_a_traceback(self):
        run = self.run_dir({"a.png": png()})
        self.project = os.path.join(self.project, "no-such-folder")
        r = self.publish(run)
        self.assertEqual(r.returncode, 1)
        self.assertIn("is not a folder", r.stderr)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
