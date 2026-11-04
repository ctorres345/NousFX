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

When a page is both, run both and say so. Pinned third-party skills (Impeccable, Vercel guidelines) arrive in a later release.

### Capture
`scripts/via_capture.py` takes four shots per screen with headless Chrome or Chromium: light and dark, at phone (390x844) and desktop (1280x800). It needs no install. Set `VIA_CHROME` to a browser path if it is not found.

Pages that need a login or real data: do not capture real accounts. Say what was skipped and why.
