# J.A.R.V.I.S. Frontend Standards (BEM + SCSS)

These are the binding rules the Overnight Autopilot must follow when generating HTML/SCSS
from a Figma design. The RAG cortex embeds this file so the code generator can retrieve the
relevant rules before writing a single line.

## 1. Naming — Strict BEM
- **Block:** standalone component. `.card`, `.nav`, `.hero`.
- **Element:** a part of a block, double underscore. `.card__title`, `.nav__item`.
- **Modifier:** a variant, double dash. `.card--featured`, `.btn--primary`.
- Never nest more than one element deep in the class name (`.card__body__text` is forbidden — use `.card__text`).
- Class names are lowercase kebab-case. No camelCase, no IDs for styling.

## 2. SCSS Structure
- One block per file: `_card.scss`, `_nav.scss`. Partials start with `_`.
- Use SCSS nesting to mirror BEM, with `&`:
  ```scss
  .card {
    &__title { }
    &__body { }
    &--featured { }
  }
  ```
- Maximum nesting depth: 3 levels. Deeper nesting is a smell.
- Declarations order: layout (display/position) → box model (margin/padding/size) → typography → visual (color/background/border) → motion (transition).

## 3. Design Tokens (variables)
- All colors, spacing, radii, and font sizes MUST be SCSS variables — never hard-coded hex or px in component rules.
- Spacing scale is an 8px baseline grid: `$space-1: 4px; $space-2: 8px; $space-3: 16px; $space-4: 24px; $space-5: 32px; $space-6: 48px;`.
- Map Figma `itemSpacing` / padding to the nearest token on the 8px scale.
- Colors: `$color-bg`, `$color-surface`, `$color-text`, `$color-accent`. Derive from Figma SOLID fills.

## 4. Typography
- Use a type scale, not arbitrary sizes: `$fs-xs: 12px; $fs-sm: 14px; $fs-base: 16px; $fs-lg: 20px; $fs-xl: 28px;`.
- Map Figma `fontSize` to the nearest scale step. Preserve `fontFamily`, `fontWeight`, and `lineHeight`.
- `line-height` is unitless where possible (e.g. `1.5`).

## 5. Layout
- Translate Figma auto-layout (`layoutMode: HORIZONTAL/VERTICAL`) to Flexbox:
  - `HORIZONTAL` → `display: flex; flex-direction: row;`
  - `VERTICAL` → `display: flex; flex-direction: column;`
  - `itemSpacing` → `gap`.
  - padding values → `padding` using spacing tokens.
- Use CSS Grid only for true 2D layouts.
- Mobile-first: base styles target small screens; layer breakpoints with `min-width` media queries.

## 6. Responsiveness & Accessibility
- Use relative units (`rem`, `%`, `clamp()`) for fluid sizing; avoid fixed px widths on containers.
- All images: `max-width: 100%`. Wide content scrolls inside its own `overflow-x: auto` container.
- Maintain WCAG AA contrast. Every interactive element needs a visible `:focus-visible` state.
- Semantic HTML: `<header> <nav> <main> <section> <article> <footer> <button>` — never a `<div>` where a semantic tag fits.

## 7. Output Contract
- Emit two files: `index.html` and `styles.scss` (plus partials if the design is large).
- HTML references classes only (no inline styles, no `<style>` blocks).
- Every tag is balanced; every SCSS block has matching braces. The validator rejects unbalanced output.
