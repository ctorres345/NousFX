# Prototype page

For each visual finding worth showing, Via builds one page: the real "before" screenshot beside 2 or 3 "after" variants, so the user picks a fix by looking. One page per run holds all of them. `scripts/via_prototype.py` renders it; you write only the variants.

## Which findings
- Visual findings only: layout, color, type size or weight, radius, spacing.
- Highest severity first, at most 5 per run.
- Each needs a screenshot of its screen with `review: kept`. A dropped or unreviewed shot never goes in the page.

## `<run_dir>/prototype.json`
```json
{"version": 1, "findings": [
  {"id": "VIA-home-1a2b3c4d",
   "variants": [
     {"label": "Taller button",
      "rationale": "48 pt tall, meets the touch target.",
      "html": "<div style=\"padding: var(--space-6)\"><button style=\"background: var(--primary); color: var(--on-primary); border-radius: var(--radius-pill); font-size: var(--font-size-body); padding: var(--space-2) var(--space-6)\">Start</button></div>",
      "states": {"default": "Primary fill", "pressed": "Primary at 88% opacity", "disabled": "Outline, 38% opacity", "empty": "Not applicable"}}
   ]}
]}
```
- `id` is the finding's ID from the merged record. Run `merge` first.
- 2 or 3 variants per finding. `label` up to 80 characters, `rationale` up to 400.
- `states` is optional. Keys: `default`, `pressed`, `disabled`, `empty`. A state you leave out shows as "not specified" in the spec panel.
- Draw the variant as it would look inside the app's 390x844 phone frame. It is a mockup, not a render.
- Redraw the whole screen, not only the changed element. The before shows the full screen; an after with one button in an empty frame cannot be compared with it. Copy the surrounding layout from the before screenshot and change only what the finding fixes.

## Writing the variant HTML
- Use `var(--token)` for every color, radius, spacing and type value. The spec panel lists the tokens a variant uses with their light and dark values, read from the project's CSS. A name the project does not define shows "no token". Literal values do not appear in the panel, and `build` warns about them.
- To see which tokens exist: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/via_tokens.py" read --project .`
- The page is built from an allow-list, so a variant that steps outside it is refused with the reason:
  - Tags: layout and text (`div`, `span`, `p`, `h3`-`h6` (the page owns `h1` and `h2`), `button`, `ul`, `ol`, `li`, `label`, `section`, `header`, `footer`, `nav`, `article`, tables, `strong`, `em`, `small`, `b`, `i`, `br`, `hr`). No scripts, links, images, forms, inputs, `svg`, `style`.
  - Attributes: `class`, `style`, `role`, `title`, `aria-*` (label, hidden, disabled, pressed, selected, current) and `disabled`. No `id`, no `data-*`, no event handlers. Class names starting with `vp-` are reserved.
  - Styles: no `url()`, `image-set`, `@rules`, comments or backslashes; no `position: fixed` or `sticky`; no custom properties of your own.
- Draw icons and images with text or shapes made from `div`s and colors. Nothing loads from the network.

## What the page does
- The mockups are not interactive (`inert`): their buttons are not tab stops and are not read out. The rationale and the spec panel carry the same information as text.
- Phone frame 390x844 with a 24 px radius. Before on the left, the chosen variant next to it, the spec panel beside that. Up to 1100 px wide they stack.
- At 700 px and narrower each finding gets a Before/After switch (After is selected) that shows one column at a time, so you compare without scrolling past two 844 px frames. Wider than that the switch is hidden and both columns show.
- The variant switcher is a row of boxed tab links. Each variant has an anchor (`#f1-v2`), and so does each finding (`#f1`). The anchors go into the record and the ledger. The "Jump to finding" list at the top is plain underlined links, so it does not look like the tabs.
- The "Mockup theme" Light/Dark toggle switches the token values (and the highlighted Light or Dark column in each spec panel) and, when both exist, the before screenshot. It changes only the mockups and the before shot, not the page. If only one theme was captured, the page says so.
- The before image's alt text names the screen, the theme and the finding's issue (trimmed to 200 characters).
- A page can have one `:target` at a time, so choosing a variant on one finding sends the other findings back to their first variant. Pick finding by finding.
- No script runs. The switcher and toggle are CSS (`:has()` and `:target`), so they need a current browser.

## Publishing
- `build` asks the allow-list (`via_outbound.py`, destination `artifacts`). `publish_allowed: true` means this project may publish the page as a private Artifact; `false` means the local `prototype.html` in the run folder is the result.
- After publishing, `via_prototype.py link <run_dir> --url <url>` records the link in `findings.json` (`prototype.url`, `published: true`). It refuses when the project is not allowed.
- The Artifact link stays in the untracked run record. The tracked ledger only gets the anchors.
- A rebuild replaces the page and the links; publish again and run `link` again.
