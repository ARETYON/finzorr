# design-system

This directory is the design-token / utility-class module boundary.

`tokens.css` holds:

- The CSS-variable-backed semantic color tokens (`:root` for the light
  theme, `[data-theme='ops']` for the cinematic "Ops" skin) that
  `tailwind.config.js`'s `theme.colors` extension is built on top of.
- The small family of hand-rolled utility classes that implement the Ops
  skin on top of those variables: `.fui-only`, `.clip-panel`/`.clip-plate`/
  `.clip-btn`, `.fui-brackets`, `.fui-hatch`, `.fui-glow`, `.fui-label`,
  `.app-title`/`.font-display`, `.route-badge`/`.fui-mono`,
  `.msg-assistant`/`.msg-user`, `.status-dot`, `.stream-cursor`.

Component code should reach for semantic Tailwind classes (`bg-surface`,
`text-ink`, `bg-route-memory`, ...) or the utility classes above — never
raw hex/rgb values or Tailwind's default color palette (`bg-violet-*` etc).
If a new semantic color/spacing need shows up, add a token here and wire it
into `tailwind.config.js` rather than hardcoding a value in a component.

This is the boundary Master Plan 2's Figma-driven redesign will design
against later: the token values and class definitions here are the
contract; everything downstream (components, pages) consumes them by name.

`src/index.css` stays reserved for genuinely global/reset-level styles
(element defaults, the Ops page background texture, markdown prose colors,
scrollbar styling) and imports this file.
