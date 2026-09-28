---
name: accessibility
description: Inspect and repair keyboard, focus, semantics, forms, and assistive-technology behavior.
---

Choose representative user tasks and inspect rendered behavior. Check keyboard traversal, visible focus, names, headings, error association, and status announcements. Prefer native controls.

For dialogs, menus, comboboxes, or tabs, read the relevant [WAI-ARIA pattern](https://www.w3.org/WAI/ARIA/apg/). Match semantics to behavior, including focus entry, movement, escape, and return. ARIA attributes do not implement keyboard interaction.

Check zoom/reflow, contrast, motion preferences, and non-color cues. Use [WAI tutorials](https://www.w3.org/WAI/tutorials/) for form, image, and table details. Combine automated and manual checks where available. Report concrete barriers, test environments, and untested combinations; passing a scanner is not certification or complete conformance.
