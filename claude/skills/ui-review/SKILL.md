---
name: ui-review
description: Review and optimize UI/UX of the current project (screens, components, layout, theming, accessibility, responsiveness, UI performance). Use for any request to review, critique, polish, or optimize UI/UX or visual design. Detects the platform and runs the matching design skills.
argument-hint: "[scope: screen, folder, or leave empty for recent changes]"
---

Review UI/UX for: $ARGUMENTS (default: UI files changed on this branch, else the main screens).

You orchestrate from the main thread. Do not change models or effort levels; if a different one seems needed, ask the user first.

## 1. Detect the platform
Check the project root and report the result in one line:
- `pubspec.yaml` -> Flutter
- `build.gradle(.kts)` / `settings.gradle(.kts)` with Compose (`androidx.compose`, `composeApp`) -> Android / Compose (or KMP Compose)
- `*.xcodeproj`, `Package.swift` -> iOS / SwiftUI
- `package.json` with react/next/vue/svelte or `index.html` -> Web
If several match, review each platform separately. If none match, say so and ask.

## 2. Inventory (cheap)
Use the `Explore` subagent to list UI entry points in scope: screens, shared components, theme/tokens, navigation, string resources. Keep only paths and one-line notes.

## 3. Run the skills (via the Skill tool, one at a time)
Skip any skill that is not listed as available and record it under "Skipped" in the report. Never substitute from memory.

Always:
- `ui-ux-pro-max`: guidelines, color, typography, interaction, accessibility, for the detected stack
- `design:accessibility-review`: contrast, touch targets, screen reader, keyboard
- `design:design-critique`: hierarchy, consistency, usability
- `design:ux-copy`: only if labels, errors, empty states, or CTAs are in scope

By platform:
- **Android / Compose:** `material-3:material-3`, `adaptive` (window sizes, tablets, foldables), `edge-to-edge` (insets, system bars), `jetpack-compose-performance` (recomposition, jank). Add `styles` only if hard-coded values or a custom design system show up, `navigation-3` only if navigation is in scope, `testing-setup` only if asked for UI or screenshot tests.
- **Flutter:** `material-3:material-3` (covers Flutter) plus the `ui-ux-pro-max` Flutter stack. There is no Flutter performance skill, so check by hand: missing `const`, oversized `build` methods, `ListView.builder` for long lists, image caching, rebuild scope.
- **iOS / SwiftUI:** `ui-ux-pro-max` SwiftUI stack and Apple HIG conventions; no dedicated skill, say so.
- **Web:** `anthropic-skills:frontend-design` when building or reshaping UI, plus the "Always" set.

## 4. Verify visually when feasible
If the app can run cheaply, capture screenshots of the screens in scope: iOS simulator tool for iOS, the built-in browser for web, `android` CLI for Android emulators. Say plainly when this was skipped and why. Do not claim visual checks you did not do.

## 5. Report, then fix
Print findings as `severity | file:line (or screen) | issue | fix | source skill`, ordered high to low.
- For a review-only request ("review", "critique"), stop after the report.
- For "optimize", "polish", or "fix", apply the high and med fixes in the main thread, keep the scope to the findings, re-read each file before editing, then build and run existing tests. Show the result.
- Never commit, push, or stash.

## 6. Fable
For architecture-level UI problems (navigation structure, design-system overhaul) or deep performance work (memory, battery), suggest the `architect` agent in one line and wait for approval. Never run it unprompted.

## Final report
Platform, skills run, skills skipped (and why), findings fixed, findings left open, build/test status.
