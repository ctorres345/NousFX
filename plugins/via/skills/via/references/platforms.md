# Platforms

**Web**, **Compose** and **Flutter** are built. SwiftUI comes with NFX-11.

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

The browser never loads the page directly. A local mask server serves it and blurs inputs, textareas, email and phone links and anything marked `data-via-redact`, so every shot comes back `redacted: true`. That flag means the mask was applied, not that the page is clean: an input styled as plain text can still show data. The mask also reaches fields inside shadow roots (a script copies the style into each one), and absolute dev-server URLs in a page, script or JSON file are rewritten to the mask server, so frames and script navigation stay behind it. A page may frame and submit forms only to the mask server, so a third-party embed shows blocked. Limits that remain: a closed shadow root made by markup (`shadowrootmode="closed"`), SVG or XHTML pages (style but no script), other XML pages (policy only), a script that empties a root's `adoptedStyleSheets` in place, and a frame made from `srcdoc`, `about:blank` or a `blob:` URL; check those shots by eye. A page that redirects off the dev server, redirects by meta refresh, or arrives compressed fails the capture (exit 4) instead of being shot unmasked. Add `--redact-selector "<css>"` (repeatable) for a page's own account name, avatar or token fields. A dev-server page's own Content-Security-Policy header is replaced on the way through by one that only limits framing and form targets, so the mask can apply. The mask is a first layer, not the guarantee: see `second-opinion.md`, "Which shots".

Pages that need a login or real data: do not capture real accounts. Say what was skipped and why.

## Compose and Flutter (native capture)

`scripts/via_capture_native.py` shoots the app's own previews. Via never invents a screen or a state.

### Detect
`via_capture_native.py detect --project <dir>` prints the platforms and their folders.
- Flutter: a `pubspec.yaml` with a top-level `flutter:` key.
- Compose: a Gradle build file that mentions `androidx.compose` or `composeApp`, in the project folder, in a module, or in a folder directly under the project.
- A project with both is reviewed per platform. Compose Multiplatform with no Android module is reported as unsupported in `coverage`; say so.

### Screens and states
- A screen is a public `@Preview` function with no parameters (Compose: public or internal, at the top of its file). Flutter uses the `@Preview` annotation from `widget_previews`.
- The preview's name gives the state. `HomeEmptyPreview` is the `empty` state of `home`. The states are `default`, `empty`, `loading`, `error`, `offline` and `long`. A name with no state word is `default`.
- Private previews, previews with parameters and nested ones are listed in `coverage` as not captured, with the reason.

### The matrix
| Shot | Taken for |
|---|---|
| phone, light and dark | every state |
| large screen, light and dark | the default state |
| largest font (2.0) | the default state |
| right to left | the default state, only when the app ships a right-to-left translation (`ar`, `he`, `fa` or `ur` in `.arb` files or `values-*` folders) |

A state with no preview is a gap in `coverage`, not a made-up shot. A gap in the matrix goes in `coverage` too.

### Skills
Run each one that is in your skill list. Skip, with a reason, any that is not.

| Skill | Run when |
|---|---|
| `material-3:material-3` | Always (it covers Compose and Flutter). |
| `adaptive` | Compose, when a large-screen shot exists. |
| `edge-to-edge` | Compose, always. |
| `jetpack-compose-performance` | Compose, when you are reading composable code for a finding. |
| `ui-ux-pro-max` | Always. |
| `design:accessibility-review` | Always. |
| `design:design-critique` | Always. |

### Capture
The route adds one temporary test file to the project, runs it, and puts every file back, also after a failure.
- Flutter runs `flutter test` ($`VIA_FLUTTER`, else `flutter` on PATH).
- Compose runs Gradle with Roborazzi and Robolectric. They come in through an init script in the run folder, so no build file is edited. The first run downloads the libraries and can take minutes. Gradle's own `build/` and `.gradle/` folders stay, as after any build.
- Right to left on Flutter is forced on Via's own wrapper. An app whose preview is a full `MaterialApp` follows its own locale list, so check that shot by eye.
- Variables: `VIA_GRADLEW` (else the project's `gradlew`), `VIA_ROBORAZZI` and `VIA_ROBOLECTRIC` (an app on much newer Compose than Kotlin 2.1 and BOM 2024.10 may need newer ones). The Android SDK comes from `ANDROID_HOME`, `local.properties` or `~/Library/Android/sdk`, `~/Android/Sdk`.

Every native shot name starts with the platform (`compose-home-light-phone.png`), so a project with both platforms never collides. Native capture has no mask, so every shot comes back `redacted: false`. Look at each shot, then set its `review`. `review: kept` is the only gate before a shot may leave the run folder.

### Emulator fallback
When the route fails (a tool is missing, or the build fails) and exactly one emulator is running, the script shoots the screen on it with `adb`: default state, phone light and dark, and at the largest font. It reads the night mode and font scale first and puts them back after, also after a failure or when the run is stopped with SIGTERM.
- `source` is `device`, the output has a `warnings` list, and `coverage` lists large, right to left and the other states as not captured. Say so in the report.
- It shoots only when the app on screen is the project: the foreground package must match an `applicationId` or `namespace` in the project's build files (a `.debug` suffix is fine). Anything else, or no id found, is no fallback. The check is there because nothing is masked.
- A physical phone is not used by default, because its screen may show personal data. `$ANDROID_SERIAL` (an explicit pick) or `$VIA_ADB_ALLOW_DEVICE=1` allows it. It never starts an emulator.
- `$VIA_ADB` sets the adb path (else `adb` on PATH, else the SDK's `platform-tools`).
- With no `adb`, no usable device, several devices and no `ANDROID_SERIAL`, a different app on screen, or a Flutter project with no `android/` folder, there is no fallback. The route's error and exit code come back with the reason added. A fallback that fails after it touched the device exits 4.
- Do not run two captures against one device at once: they would change the same settings.

Exit codes: 0 done, 1 invalid input (nothing written), 3 a tool was not found, 4 the capture failed (no shots kept).
