# Via sample apps

Three tiny apps that later Via tickets use to prove their acceptance criteria: `web`, `compose` and `flutter`.
Each has one screen with two deliberate design flaws, so an audit has something to find:

- the subtitle has weak contrast (`#BBBBBB` on white);
- the button is smaller than the touch target (96x28).

| App | Build or render |
|---|---|
| `web` | Open `web/index.html` in a browser. |
| `compose` | `cd compose && ./gradlew assembleDebug` (needs the Android SDK and JDK 17+). |
| `flutter` | `cd flutter && flutter test` |

`compose` and `flutter` also have previews (`Previews.kt`, `previews.dart`) for the default, empty and long states. `/via audit` shoots them with `plugins/via/scripts/via_capture_native.py`. Compose needs Gradle, the Android SDK and, on the first run, a few minutes of downloads; Flutter needs `flutter` on PATH. Set `VIA_GRADLEW`, `VIA_FLUTTER`, `VIA_ROBORAZZI` or `VIA_ROBOLECTRIC` to change a tool or version. Gradle leaves its `build/` folder behind.

`web/tickets/SAMPLE-1.md` is a markdown ticket for `/via spec` without Jira. Run spec on a copy of `web` (for example in a temp folder with `git init`), because `via_run.py init` adds `.via/` to the project's `.gitignore`.

Do not "fix" the flaws. Tests rely on them.
