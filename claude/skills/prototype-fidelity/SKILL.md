---
name: prototype-fidelity
description: Work based on an approved prototype or design (artifact, Figma, mockup) where the prototype is the spec. Use when creating or refining tickets from it, implementing its UI, reviewing that UI, or writing the PR.
---

The prototype is the spec. Apply the step that matches where you are.

1. **Tickets:** every ticket built from the prototype links it AND names the artboard or screen. It carries the acceptance criterion "matches the prototype: colors, sizes, radii, spacing, icons, states (open/pressed/selected/empty/dark)", with the concrete values that matter written in the ticket, not only a link. When creating or refining such a ticket, add this if missing.
2. **Implementing:** open and read the prototype source (not only the ticket text) before writing UI code, and list its concrete values (tokens, colors per element, dimensions, radii) in the plan. If the app lacks a token the prototype uses, say so and name the ticket that adds it.
3. **Review:** `review-loop` hands the reviewer the prototype and asks it to list visual deviations as findings. Before opening the PR, compare the emulator screenshots against the prototype yourself.
4. **PR:** the description states what was checked against the prototype and what deliberately differs (and which ticket covers it).
