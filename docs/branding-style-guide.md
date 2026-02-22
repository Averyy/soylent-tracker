# Soylent Stock Tracker - Branding & Style Guide

## Concept

A cheerful stock tracker for Soylent fans. Think: the friendly neighborhood ice cream shop menu board, but for meal replacement bottles. Bright flavor colors, a repeating bottle pattern wallpaper, and a rainbow color bar give the whole thing a warm, inviting personality -- while still being clean enough to read at a glance.

Soylent's brand DNA (clean, modern, green) is the foundation, but we layer on playfulness through the multi-flavor color palette, animated reveals, and a background that feels like wrapping paper at a Soylent-themed birthday party.

## Visual Reference (soylent.com / soylent.ca)

Soylent's brand uses:
- **Rounded geometric logo** in a thin square border
- **All-lowercase typography** (casual, modern)
- **Black & white primary** with bright green (#7AC143) as the accent
- **5 flavor colors** in a signature color bar: strawberry, banana, chocolate, mint, blue
- **Sans-serif font** (Apercu -- a geometric/grotesque hybrid)
- **Flat design** -- no gradients, no shadows, minimal borders

## Our Adaptation

We borrow Soylent's flavor palette and bottle silhouette, then turn it into something that feels like a candy store dashboard:

### Color Palette

#### UI Colors

| Role | CSS Variable | Color | Notes |
|------|-------------|-------|-------|
| Background | `--bg` | `#F7F7F6` | Warm off-white, lets the bottle pattern peek through |
| Surface/Cards | `--surface` | `#FFFFFF` | Crisp white cards floating on the patterned bg |
| Surface active | `--surface-active` | `#ECECEC` | Pressed/active state for surface elements |
| Text primary | `--text` | `#1A1A1A` | Near-black, easy on the eyes |
| Text secondary | `--text-secondary` | `#6B7280` | Gray-500 for metadata and timestamps |
| Text tertiary | `--text-tertiary` | `#B0B5BC` | Lightest text for timestamps, tertiary info |
| Placeholder | `--placeholder` | `#C4C8CD` | Input placeholder text and disabled indicators |
| Accent | `--accent` | `#5e93db` | Soylent blue -- the primary interactive color |
| Accent light | `--accent-light` | `#EDF3FB` | Light blue for badges and highlights |
| Accent text | `--accent-text` | `#2D5F9E` | Dark blue for text on accent backgrounds |
| Green (in-stock) | `--green` | `#22C55E` | Green for pulsing in-stock status dots |
| Danger / Out of Stock | `--red` | `#EF4444` | Red -- the "oh no" color |
| Danger light | `--red-bg` | `#FEF2F2` | Light red background |
| iMessage green | `--imessage-green` | `#34C759` | Apple iMessage brand green for SMS mentions |
| Border | `--border` | `#E5E7EB` | Subtle gray borders on cards |
| Interactive | `--interactive` | `#5e93db` | Blue buttons and links |
| Interactive hover | `--interactive-hover` | `#4A82D0` | Darker blue on hover |
| Interactive active | `--interactive-active` | `#3D73B8` | Pressed state for buttons |

#### Soylent Flavor Colors (from soylent.com)

The heart of the fun -- each flavor gets its own color, used throughout:

| Flavor | Full Color | Light Shade (12% blend) | CSS Variable |
|--------|-----------|------------------------|--------------|
| Strawberry | `#f8485e` | `#F7E2E4` | `--soylent-strawberry` |
| Banana | `#eed484` | `#F6F3E8` | `--soylent-banana` |
| Chocolate | `#623b2a` | `#E5E0DE` | `--soylent-chocolate` |
| Mint | `#ade8bf` | `#EEF5EF` | `--soylent-mint` |
| Blue | `#5e93db` | `#E5EBF3` | `--soylent-blue` |

**Full colors** appear in: auth card rainbow bar, future flavor-specific UI elements.
**Light shades** appear in: background bottle pattern (label fill on each bottle silhouette). Computed as 12% blend of the full color against `#F7F7F6`.

### Background: Animated Bottle Wallpaper

Full-viewport animated wallpaper of Soylent bottle silhouettes. Bottles are arranged in a jittered hex grid, randomly appearing and disappearing with spring-bounce animations. Renders on every page behind all content.

#### Visual Style

- Bottles use the **flavor light shade colors** as label fills (see palette above), with subtle gray outlines (`#DCDEE0` light / `#333340` dark) and gray caps (`#D0D1D2` light / `#303038` dark)
- Animation feel: bottles **shrink to nothing** (fast, 600ms) and **spring back** with a bouncy overshoot (organic, playful)
- Bottles randomly cycle — a few disappear each tick, hidden ones pop back with 60% chance
- Dark mode: bottles instantly recolor to dark palette (muted, darker fills)

#### Wallpaper Controls Panel

Floating glassmorphic panel in the top-right corner, toggled from a "Wallpaper" button in the footer. Contains sliders for density, size variation, rotation, randomness, cycle speed, and batch size. No dividers between controls.

**Glassmorphism constraint**: The bottle wallpaper uses very faint pastel colors, so standard glass effects don't work:
- **What works**: low opacity bg (`~0.35` white / `~0.4` dark) + minimal blur (`2px`)
- **What doesn't work**: high opacity (0.5+) or high blur (8px+) — panel looks solid, bottles invisible
- Rule of thumb: `backdrop-filter` glass needs high-contrast content behind it. Faint backgrounds require very low blur and low opacity.

### Typography

- **Font**: `Inter` -- clean geometric sans-serif, friendly and readable
- **Headings**: Normal casing, 600 weight
- **Body**: 400 weight, 15px base size
- **Monospace accents**: `IBM Plex Mono` for counts, timestamps, technical data
- **Letter-spacing**: Slightly loose on headings (0.01-0.02em)
- **Casing**: Standard sentence/title case throughout -- no forced lowercase

### Layout Principles

- **Max-width**: 960px outer container, 720px content width on desktop (>960px viewport)
- **Generous spacing**: 24-32px between sections, 16px within cards
- **Cards**: Product rows (`.card-list`) have 1px border, 12px border-radius, minimal shadow (`0 1px 3px`). Rainbow cards (auth/header) have stronger shadows (`0 0 24px`)
- **Status indicators**: Colored dots (blue/red) next to product names, not colored backgrounds
- **Card-based product list**: Each product is a card row, no tables
- **No middot separators**: Never use `·` (middle dot / &middot;) as a text separator anywhere in the UI. Use colons, commas, or spatial separation instead.

### CSS Architecture Rules

These rules prevent duplication and keep the stylesheet maintainable:

- **Use shared base classes** for repeated visual patterns. When 2+ components share the same layout/styling (background, border-radius, box-shadow, etc.), extract a shared class (e.g. `.rainbow-card`, `.card-list`, `.card-row`) and compose with component-specific classes.
- **Every repeated color must be a CSS variable.** If a hex value appears 2+ times, extract it to `:root` (and `html.dark` if applicable). Key variables: `--surface-hover`, `--border-strong`, `--green-text`, `--red-text`, `--ease-out`.
- **Never duplicate `::before`/`::after` pseudo-elements.** If multiple selectors need the same pseudo-element, use a shared base class.
- **Dark mode overrides only for values that change.** If the base rule uses `var(--border)` and dark mode redefines `--border`, don't add a `html.dark` override that sets `border-color: var(--border)` -- it's already handled. Only add dark overrides for values that genuinely differ (e.g. hardcoded colors, shadows with different opacity).
- **No `!important`** except in `@media (prefers-reduced-motion)`. Fix specificity issues with more specific selectors instead.
- **No inline `style=""` attributes in templates.** Use utility classes (`.spacer-md`, `.inline`, `.fw-500`, `.fs-xs`, `.text-primary`, etc.) or component modifiers (`.auth-card--compact`, `.error-msg--bottom`). The only exception is one-off layout overrides that truly don't warrant a class.
- **One selector per component.** Don't split the same class across multiple rule blocks -- merge them.
- **Extract repeated easing values** into `--ease-out` (or similar variables) rather than repeating `cubic-bezier(...)` values.

### Template DRY Rules

- **Extract shared markup into partials.** If 2+ templates contain near-identical blocks (forms, SVG icons, product loops), extract to `templates/partials/`.
- **Deduplicate inline SVGs.** Any SVG icon or graphic used in 2+ places must be a partial (`partials/flag_ca.html`, `partials/icon_external.html`, etc.).
- **Use `{% include %}` with `{% with %}` for parameterized partials** (e.g. `{% with action_url="/verify" %}{% include "partials/otp_form.html" %}{% endwith %}`).

### Components

#### Auth Card (Login/Verify)
- Centered card, max 400px
- Rainbow top border (5 flavor color stripes, 6px tall)
- "soylent tracker" logo with bottle icon in Soylent blue
- Simple phone input + submit button
- Minimal -- just the essentials

### Animation

Animations should feel **subtle and classy** -- never flashy or distracting. The goal is polish, not spectacle.

- **Library**: Use [anime.js v4](https://animejs.com/documentation) for JS animations (self-hosted at `static/js/vendor/anime-4.3.6.umd.min.js`). Prefer anime.js springs for organic, bouncy motion. CSS `@keyframes` / `transition` are fine for simple state changes (hovers, fades).
- **Easing**: `var(--ease-out)` / `cubic-bezier(0.22, 1, 0.36, 1)` for natural deceleration. anime.js `spring()` for playful bounce. Always use the CSS variable, never the raw value.
- **Duration**: 150-450ms for UI transitions. Never longer than 450ms.
- **Page loads**: Staggered fadeUp reveals (translateY 18px, opacity 0→1), 0.1-0.2s stagger between sections.
- **Hover pattern**: No `transform: scale` on hover. Hover brightens/darkens bg, active presses inward via `inset box-shadow`.
- **Loading states**: Dim to 50% opacity, `pointer-events: none` during requests.
- **Reduced motion**: All animations respect `prefers-reduced-motion: reduce`.

### Accessibility

- All interactive elements need `aria-label` attributes
- Respect `prefers-reduced-motion` for all animations
- Mobile keyboards: use `inputmode="numeric"` on phone/OTP fields

### Tone

- **Cheerful**: The rainbow bar and bottle wallpaper say "this is fun" before you read a word
- **Friendly**: Like your favorite ice cream shop that happens to track nutrition bottles
- **Data-first**: Stock status is still the hero -- the fun just makes checking it a treat
- **Approachable**: Nothing intimidating, nothing corporate -- just a fan-made tool with personality
