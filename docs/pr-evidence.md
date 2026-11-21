# PR evidence

`gh` cannot attach images to a PR body, so an agent working alone had no way to show screenshots. `scripts/pr_evidence.py` fixes that: it uploads the shots to a prerelease per month and prints the markdown table I paste into the PR body.

```bash
python3 -B scripts/pr_evidence.py publish NFX-87 .via/runs/20261118T185619Z --project .
```

- The store is one prerelease per repo per month, tag `evidence-YYYY-MM` (UTC), made on the first upload of the month. Two sessions that start the same month at once both succeed: the one that loses the race to create it just uploads.
- Each file lands as `<KEY>-<run>-<shot>.<ext>`, for example `NFX-87-20261118t185619z-home-light-phone.png`. The ticket key and the run folder's name keep parallel sessions and a "before" and an "after" run apart. Publishing the same Via run again replaces its files, so there are no duplicates. A plain folder (private repos only, below) has a name that repeats across PRs (`shots/NFX-87`), so each publish adds a UTC stamp to it and never replaces an earlier PR's files.
- The links look like `https://github.com/<owner>/<repo>/releases/download/evidence-YYYY-MM/<file>`. Images are embedded (`![...](...)`); videos stay plain links. They keep working after the branch is deleted, which is why I use releases and not a screenshots branch.
- In a private repo the links work only for people who can see the repo. I checked this on Animus on 2026-11-20 (NFX-236): an image renders inline for a signed-in viewer. An mp4 link downloads the file (GitHub serves release assets as attachments), so there is no inline player.

## Why monthly releases (NFX-236)

Until NFX-236 a UI PR pushed its shots to a `<prefix>/<KEY>-shots` branch and linked them by branch name. `after-merge` deleted that branch, and every image in the merged PR turned into a 404 (Animus #177 to #184). I rejected a tag per PR (tags pile up) and one orphan `pr-shots` branch: a force-push does not free space on GitHub without a Support request, and any rewrite changes the SHAs that recent PRs link to. A release can be deleted whole, so old months go away cleanly, and a month holds up to 1000 files.

Each month's first upload creates a prerelease and a tag. If the repo has a workflow on `release` or `push: tags`, make it skip them, or it runs once a month for nothing: `tags-ignore: ['evidence-*']` under `push`, and `if: ${{ !startsWith(github.ref_name, 'evidence-') }}` on a release job (`ref_name` is the tag in both events).

## Retention: `sweep`

```bash
python3 -B scripts/pr_evidence.py sweep            # from the repo; --repo OWNER/NAME to name another
```

- It deletes each `evidence-YYYY-MM` release, with its tag, whose month ended more than 90 days ago (`gh release delete <tag> --cleanup-tag --yes`). A merged PR's shots therefore last 3 to 4 months.
- It keeps an old month while an open PR, or one merged in the last 90 days, links to it (a file or the release page), and says which PR. `after-merge` sweeps right after its own merge, so a PR that stayed open long past its evidence month keeps its images for 90 more days.
- It warns when a release passes 800 files. GitHub stops at 1000; the next step would be a release per half month.
- If it cannot list the releases, the open PRs or the recently merged ones, it deletes nothing (exit 3). Other releases (`evidence`, `v1.0`) are never touched.
- `after-merge` runs it after the branch cleanup. There is no scheduler and no GitHub Action (Actions minutes ran out on Animus).

## Private repos

A private repo's evidence is seen only by people who can see the repo, so `publish` there needs no Via run record and no `public: true`:

- A Via run folder still sends only its kept shots (a dropped shot stays home).
- Any other folder inside the project sends every media file under it, named by its path (`after/home-dark.png` becomes shot `after-home-dark`); other files are skipped and listed.
- Metadata stripping and the machine-trace check below still apply.

A public or internal repo keeps every rule below. The visibility comes from `gh repo view --json visibility`.

A folder under `.via/`, or one holding a Via record (`findings.json`, `spec.json`), always goes through the kept-shots review, even before its record is written.

The check runs only at upload time. Before making a private repo public, delete its `evidence-*` releases (`gh release delete <tag> --cleanup-tag --yes`): otherwise every shot uploaded without review becomes public with it.

## Moving old PRs: `migrate`

```bash
python3 -B scripts/pr_evidence.py migrate 183 --sha 483deb666abc1ac181b61e068d83e754e4138a44           # the plan
python3 -B scripts/pr_evidence.py migrate 183 --sha 483deb666abc1ac181b61e068d83e754e4138a44 --write   # do it
```

- It finds the links in a PR body that point into `shots/<KEY>/` of this repo (`blob`, `raw`, `tree` and `raw.githubusercontent.com`), fetches each file at the commit, strips it, uploads it to this month's release as `<KEY>-<sha7>-<shot>` and rewrites the link. A folder link (`tree/...`) uploads every media file in the folder and points at the release page.
- A link that names a commit uses that commit; a link that names a branch uses `--sha`. A deleted branch's last commit is in `gh api "repos/<owner>/<repo>/activity?activity_type=branch_deletion"` (field `before`).
- A file it cannot rebuild (missing at that commit, or refused by the rules below) keeps its old link and is listed on stderr, and the run exits 5. Keep that PR's screenshots branch until each listed link is fixed by hand. A `.mov` or `.webm` recording is always refused (old screenshots branches allowed them): convert it to mp4 and publish it, or leave its link.
- Private repos only: a public repo publishes from a Via run.
- Migrated shots go to the current month, so the sweep deletes them 3 to 4 months later like any other.

## What it refuses

On a public repo anyone can see the files, so the script is strict. Any refusal stops the whole run before the first upload. In a private repo only the last three rules apply (media only, no metadata, no machine traces), plus a Via run folder's kept review.

- **Only kept shots.** It asks `via_run.py outbound-shots` for the shots the run record marks as kept. A folder that is not a Via run folder, or a shot that is dropped or not reviewed yet, never leaves the machine.
- **Only shots approved for the public.** Each shot needs `public: true` in the run record, set after looking at it again for a public audience. The web mask blurs form fields, email and phone links and tagged elements, not page text, so a masked shot can still show a name or account data.
- **Only masked or safe native shots.** A web shot is masked (`redacted: true`). A native shot has no mask, so it goes only when it is an emulator shot in demo mode cropped to the app (`source: device` with `emulator: true`, NFX-91; a real phone shows a real account, so its shots are refused) or a preview rendered from code (`source: preview`). Any other unmasked shot is refused.
- **Only media.** `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` and `.mp4`, with a shot name of lowercase letters, digits and hyphens.
- **No metadata.** PNG, JPEG, WebP and GIF keep only the parts an image needs (an allow-list): pixels, palette, transparency, basic color info, and for GIF the frame timing and loop count. Text, EXIF, XMP, comments, ICC profiles (they can name the display), vendor data and bytes after the end marker are dropped. MP4 keeps only the boxes a video needs (an allow-list); every other box, old free space included, becomes a zero-filled `free` box of the same size, so the video still plays. Recording times and track names are zeroed, and a video with a track that is not video or sound is refused (iPhone and QuickTime recordings carry one, so they are refused). The codec description (`stsd`) is kept as it is, so the encoder's name stays. Dropping the ICC profile means a Display P3 shot shows slightly duller colors.
- **No traces of the machine.** A file whose name holds my user name, home folder or host name, or whose stripped bytes hold one of those of 5 or more characters, a `/Users/` or `/home/` path, a UUID or an emulator serial, is refused. Short names are not looked for in the bytes, since 3 letters turn up by chance in a few MB of compressed data.

Emulator shots are safe to publish because NFX-91 takes them in demo mode and crops them to the app window.

## Exit codes and the token

| Exit | Meaning |
|---|---|
| 0 | Every file uploaded; the table is on stdout |
| 1 | Invalid input or a refused file; nothing uploaded |
| 3 | `gh` is missing, or its token cannot write releases. A classic token needs the `repo` scope (`gh auth refresh -s repo`); a fine-grained token needs Contents: read and write |
| 4 | An upload, a sweep delete or a migrate body edit failed partway; publish's table lists only the files that uploaded. `--clobber` deletes the old file first, so a failed re-run can leave an older PR's link broken until the next run |
| 5 | `migrate` repointed some links but left others, listed on stderr |

My Mac's `gh` token has `repo`, which is enough. I have not checked the token on another machine.
