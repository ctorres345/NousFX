# PR evidence

`gh` cannot attach images to a PR body, so an agent working alone had no way to show screenshots. `scripts/pr_evidence.py` fixes that: it uploads a Via run's kept shots to one public prerelease named `evidence` and prints the markdown table I paste into the PR body.

```bash
python3 -B scripts/pr_evidence.py publish NFX-87 .via/runs/20261118T185619Z --project .
```

- Each file lands as `<KEY>-<run>-<shot>.<ext>`, for example `NFX-87-20261118t185619z-home-light-phone.png`. The run folder's name keeps a "before" run and an "after" run apart, since Via gives both the same shot names. Publishing the same run again replaces its files, so there are no duplicates.
- The links look like `https://github.com/<owner>/<repo>/releases/download/evidence/<file>`. They work without signing in (checked with a plain `curl` on this repo), and they keep working after the branch is deleted. That is why I use a release and not a screenshots branch.
- The release is made on first use and never deleted: merged PR bodies link to it.

## What it refuses

The repo is public and release assets stay, so the script is strict. Any refusal stops the whole run before the first upload.

- **Only kept shots.** It asks `via_run.py outbound-shots` for the shots the run record marks as kept. A folder that is not a Via run folder, or a shot that is dropped or not reviewed yet, never leaves the machine.
- **Only masked or safe native shots.** A web shot is masked (`redacted: true`). A native shot has no mask, so it goes only when it is an emulator shot in demo mode cropped to the app (`source: device` with `emulator: true`, NFX-91; a real phone shows a real account, so its shots are refused) or a preview rendered from code (`source: preview`). Any other unmasked shot is refused.
- **Only media.** `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` and `.mp4`, with a shot name of lowercase letters, digits and hyphens.
- **No metadata.** PNG, JPEG, WebP and GIF keep only the parts an image needs (an allow-list): pixels, palette, transparency, color profile, and for GIF the frame timing and loop count. Text, EXIF, XMP, comments, vendor data and bytes after the end marker are dropped. MP4 user data and metadata boxes become `free` boxes of the same size, so the video still plays.
- **No traces of the machine.** A file whose name holds my user name, home folder or host name, or whose stripped bytes hold one of those of 5 or more characters, a `/Users/` or `/home/` path, a UUID or an emulator serial, is refused. Short names are not looked for in the bytes, since 3 letters turn up by chance in a few MB of compressed data.

Emulator shots are safe to publish because NFX-91 takes them in demo mode and crops them to the app window.

## Exit codes and the token

| Exit | Meaning |
|---|---|
| 0 | Every file uploaded; the table is on stdout |
| 1 | Invalid input or a refused file; nothing uploaded |
| 3 | `gh` is missing, or its token cannot write releases. A classic token needs the `repo` scope (`gh auth refresh -s repo`); a fine-grained token needs Contents: read and write |
| 4 | An upload failed partway; the table lists only the files that uploaded. `--clobber` deletes the old file first, so a failed re-run can leave an older PR's link broken until the next run |

My Mac's `gh` token has `repo`, which is enough. I have not checked the token on another machine.
