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

Do not "fix" the flaws. Tests rely on them.
