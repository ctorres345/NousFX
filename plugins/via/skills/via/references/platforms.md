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

The browser never loads the page directly. A local mask server serves it and blurs inputs, textareas, email and phone links and anything marked `data-via-redact`, so every shot comes back `redacted: true`. That flag means the mask was applied, not that the page is clean: a page that navigates by script to another absolute URL, or an input styled as plain text, can still show data. Fields inside a web component's shadow DOM, or inside an iframe that loads an absolute dev-server URL, are not reached by the mask at all, so check those shots by eye. A page that redirects off the dev server, redirects by meta refresh, or arrives compressed fails the capture (exit 4) instead of being shot unmasked. Add `--redact-selector "<css>"` (repeatable) for a page's own account name, avatar or token fields. A dev-server page loses its Content-Security-Policy header on the way through, so the mask can apply. The mask is a first layer, not the guarantee: see `second-opinion.md`, "Which shots".

Pages that need a login or real data: do not capture real accounts. Say what was skipped and why.
