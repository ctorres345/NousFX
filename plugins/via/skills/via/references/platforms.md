# Platforms

Only **web** is built. Compose and Flutter come with NFX-10, SwiftUI with NFX-11.

## Web

### Detect
A project is web when it has an `index.html`, or a `package.json` that depends on react, next, vue, svelte or astro. If it matches several platforms, review each one on its own. If it matches none, say so and ask.

### Screens
- Static pages: every `.html` file outside `node_modules`, build output (`dist`, `build`, `.next`) and hidden folders.
- A framework app: ask the user for the dev-server URL (for example `http://localhost:5173/`) and the routes to cover. Do not start servers yourself.

### Skills
Run each one that is in your skill list. Skip, with a reason, any that is not.

| Skill | Run when |
|---|---|
| `design-taste-frontend` | The screen is a public page: landing, marketing, portfolio, product page. |
| `ai-design-check` | The screen is an app screen: dashboard, form, settings, tool. |
| `ui-ux-pro-max` | Always. |
| `design:accessibility-review` | Always. |
| `design:design-critique` | Always. |

When a page is both, run both and say so. Pinned third-party skills (Impeccable) arrive in a later release. Vercel's web-design-guidelines skill is not pinned, because it fetches its rules from a branch at runtime (see `docs/sync.md`).

### Capture
`scripts/via_capture.py` takes four shots per screen with headless Chrome or Chromium: light and dark, at phone (390x844) and desktop (1280x800). It needs no install. Set `VIA_CHROME` to a browser path if it is not found.

The browser never loads the page directly. A local mask server serves it and blurs inputs, textareas, email and phone links and anything marked `data-via-redact`, so every shot comes back `redacted: true`. That flag means the mask was applied, not that the page is clean: an input styled as plain text can still show data. The mask also reaches fields inside shadow roots (a script copies the style into each one), and absolute dev-server URLs in a page, script or JSON file are rewritten to the mask server, so frames and script navigation stay behind it. A page may frame and submit forms only to the mask server, so a third-party embed shows blocked. Limits that remain: a closed shadow root made by markup (`shadowrootmode="closed"`), SVG or XHTML pages (style but no script), other XML pages (policy only), and a frame made from `srcdoc`, `about:blank` or a `blob:` URL; check those shots by eye. A page that redirects off the dev server, redirects by meta refresh, or arrives compressed fails the capture (exit 4) instead of being shot unmasked. Add `--redact-selector "<css>"` (repeatable) for a page's own account name, avatar or token fields. A dev-server page's own Content-Security-Policy header is replaced on the way through by one that only limits framing and form targets, so the mask can apply. The mask is a first layer, not the guarantee: see `second-opinion.md`, "Which shots".

Pages that need a login or real data: do not capture real accounts. Say what was skipped and why.
