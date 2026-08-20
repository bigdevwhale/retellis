# DESIGN.md — Retellis · AI companion PWA

> Brand contract for an open-source AI companion (psychotherapist / friend / coach) delivered as a SPA PWA with BYOK swappable LLM keys. Bound to the **Stripe** design system, tuned warmer on companion/chat surfaces and kept precise on config/routing surfaces.

---

## 1. Visual Theme & Atmosphere

A calm, premium, trustworthy surface — the emotional register of a quiet, well-lit room you'd willingly talk in. Stripe's "technical yet luxurious" foundation (white canvas, deep-navy headings, signature purple accent, blue-tinted multi-layer shadows, sohne-var at weight 300) is the base. For companion surfaces we warm the neutrals slightly (sand-tinted whites, softer contrast on body copy) and slow the motion, so the product never reads as clinical, transactional, or "cold fintech." For BYOK / routing / budget surfaces we keep Stripe's engineered precision intact — dense tabular numerals, hairline borders, monospace for keys and model IDs.

**North stars:**
- *Calm, not cold.* Warm-tinted neutrals on chat surfaces; reserved purple as the single emotional accent.
- *Confidence through restraint.* Weight-300 display type, generous whitespace, no shouting. The companion is present, not performing.
- *Engineered where it matters.* Routing, fallback chains, budgets, API keys use Stripe's tabular/mono precision — this audience (BYOK) reads that as competence and trust.
- *Dark mode first.* Companions are used at night. Dark surfaces use Stripe's `#0d253d` / `#1c1e54` family, never pure black.

## 2. Color Palette & Roles

### Primary
- **Companion Purple** `#533afd` — primary brand, CTA, active states, focus ring. Single emotional accent; used sparingly so it stays meaningful.
- **Purple Hover** `#4434d4` — pressed/hover on primary.
- **Deep Navy** `#061b31` — headings (light mode). Not black — warm, premium.
- **Pure White** `#ffffff` — page bg, card surfaces (light mode).

### Warm companionship neutrals (chat surfaces)
- **Sand White** `#fbfaf7` — chat canvas (light). Slight warm tint vs pure white to reduce "clinical" read.
- **Warm Ink** `#1a2233` — chat body copy (light). Softer than pure navy for long reading.
- **Companion Bubble (them)** `#f1eef9` — inbound message surface, purple-tinted.
- **User Bubble (me)** `#533afd` w/ white text — outbound, the one place purple fills a surface.

### Engineered neutrals (config / routing / budget surfaces)
- **Border Default** `#e5edf5` — cards, dividers, hairlines.
- **Border Purple** `#b9b9f9` — active/selected state borders.
- **Label** `#273951` — form labels, secondary headings.
- **Body** `#64748d` — descriptions, captions.
- **Mono Surface** `#0d253d` — key/ID chips background (dark), `#e8eef6` text.

### Status
- **Success** `#15be53` (bg `rgba(21,190,83,0.16)`, border `rgba(21,190,83,0.4)`, text `#108c3d`) — provider connected, key valid, fallback healthy.
- **Warning** `#9b6829` (lemon) — budget threshold approaching, rate-limit near.
- **Danger** `#ea2261` (ruby) — key invalid, provider down, budget exceeded.

### Dark mode tokens
- **BG** `#0d253d` · **Surface** `#11304e` · **Heading** `#eef3fb` · **Body** `#9fb0c8` · **Border** `#1f3a5a` · **Purple** stays `#533afd` · **Companion Bubble** `#16304f` · **User Bubble** `#533afd`.

### Shadows (signature, blue-tinted, multi-layer)
- **Card / elevated** `0 30px 45px -30px rgba(50,50,93,0.25), 0 18px 36px -18px rgba(0,0,0,0.1)`
- **Ambient** `0 15px 35px 0 rgba(23,23,23,0.08)`
- **Soft** `0 1px 2px 0 rgba(23,23,23,0.06)` — chat bubbles, chips
- Dark mode shadows use `rgba(0,0,0,0.4)` + `rgba(50,50,93,0.35)`.

## 3. Typography

**Primary:** `sohne-var` (fallback `SF Pro Display`, then system). **Mono:** `SourceCodePro` (fallback `SFMono-Regular`). **Serif:** `"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif"` — the `--serif` token, reserved for the Journal body type only (a calm, read-first diary surface). OpenType `"ss01"` on all sohne text; `"tnum"` for tabular numbers (budgets, token counts, latency, cost).

| Role | Size | Weight | LH | Tracking | Notes |
|---|---|---|---|---|---|
| Display Hero | 48–56px | 300 | 1.03 | -1.4px | Onboarding headlines only |
| Section / Screen title | 28–32px | 300 | 1.1 | -0.64px | Screen headers |
| Card title | 22px | 300 | 1.1 | -0.22px | Memory entries, settings groups |
| Body | 16px | 300–400 | 1.45 | normal | Chat messages, descriptions |
| Body Large | 18px | 300 | 1.4 | normal | Companion voice (long-form) |
| Label | 14px | 400 | 1.0 | normal | Form labels, nav |
| Caption | 12–13px | 400 | 1.4 | normal | Timestamps, metadata |
| Mono Body | 13px | 500 | 1.6 | normal | API keys (masked), model IDs |
| Mono Label | 11px | 500 | 1.0 | 0.06em uppercase | Technical tags (`OPENAI`, `ANTHROPIC`) |

**Principles:** weight 300 as signature; no 700 in sohne (mono uses 500/700 for code). Two OpenType modes — `ss01` for prose, `tnum` for numbers. Progressive tracking tightens with size.

## 4. Components

### Buttons
- **Primary**: bg `#533afd`, text `#fff`, radius 6px, pad `9px 16px`, 16px/400, hover `#4434d4`, shadow soft.
- **Ghost**: transparent, text `#533afd`, border `1px solid #b9b9f9`, radius 6px, hover bg `rgba(83,58,253,0.06)`.
- **Danger**: ghost with ruby text `#ea2261` + border `rgba(234,34,97,0.3)`.
- **Icon-only**: 32px square, radius 6px, hover bg `rgba(83,58,253,0.06)`.

### Cards / Surfaces
- bg `#fff` (light) / `#11304e` (dark), border `1px solid #e5edf5` / `#1f3a5a`, radius 8px (featured) / 6px (standard) / 4px (tight chips). Shadow per §2.

### Chat bubbles
- **Companion**: bg `#f1eef9` (light) / `#16304f` (dark), text Warm Ink / `#eef3fb`, radius `14px 14px 14px 4px` (tail bottom-left), max 72ch, pad `12px 16px`.
- **User**: bg `#533afd`, text `#fff`, radius `14px 14px 4px 14px` (tail bottom-right), align right.

### Inputs
- border `1px solid #e5edf5`, radius 6px, pad `10px 12px`, focus ring `0 0 0 3px rgba(83,58,253,0.22)` + border `#533afd`. Mono input for keys: `SourceCodePro`, letter-spacing `0.04em`, masked as `sk-••••••••••••••••3a2f`.

### Badges / Pills
- **Provider pill**: mono label 11px uppercase, bg `#0d253d`/`#11304e`, text `#e8eef6`, radius 4px, pad `2px 8px`. Status dot precedes label (success/warning/danger).
- **Salience chip** (memory): bg `rgba(83,58,253,0.08)`, text `#4434d4`, radius 999px, pad `2px 10px`, 12px/400.

### Navigation
- Left vertical rail (desktop) / bottom tab bar (mobile PWA), 5 destinations: Chat · Memory · Routing · Persona · Settings. Active item: purple text + `rgba(83,58,253,0.08)` pill bg. Sticky, blur backdrop `blur(12px)`.

### Memory event-chain card
- Vertical timeline; each event = timestamp (tnum, caption), salience chip, one-line summary, optional emotion tags. Connected by 1px `#e5edf5` spine. Selected event: border-purple + ambient shadow.

### Journal surface
- A read-first diary, separate from chat. Composer at top (serif textarea "What's on your mind…", optional title, free-text mood, tag chips, a 1–3 "matters to me" segment → salience). Below it a reverse-chronological feed **grouped by day** (`Today` / `Yesterday` / absolute date as a small uppercase rule). Entry body in **serif** (`--serif`, ~17.5px / 1.7) for calm reading; optional serif title, mood chip, tag chips, 1–3 salience stars, a quiet "from chat with {name}" provenance line when seeded from a message, and hover-revealed edit/delete. Reuses `.card`, `.chip`, `.tag`, `.seg`, `.stagger` rise; no new color tokens. Empty state = one gentle sentence + the composer focused (§7). "Save to journal" hover action on the user's own chat bubbles seeds an entry and routes here.

### Routing / budget panel
- Table: provider | model | status dot | reqs (tnum) | cost (tnum, $) | latency (tnum, ms). Hairline rows `1px solid #e5edf5`. Fallback chain shown as ordered chips with connectors. Budget ring (SVG) — arc in purple, threshold ticks.

## 5. Layout

- **Base unit 8px.** Scale: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96.
- **PWA shell**: max-width 1280px content; companion chat column max 760px for readability; side panels 320px.
- **Mobile**: single column, bottom tab bar 56px, safe-area insets, chat input docked with `env(keyboard-inset)`.
- **Grid**: 12-col at ≥1024px, 4-col at ≥640px, 1-col below.
- **Density**: companion surfaces generous (24–32px gutters); config surfaces compact (12–16px).

## 6. Motion

- **Easing**: `cubic-bezier(0.22, 1, 0.36, 1)` (Stripe-style smooth-out) for entrances; `cubic-bezier(0.4, 0, 0.2, 1)` for state changes.
- **Durations**: 140ms (micro: hover, focus), 220ms (small: badge, chip), 320ms (view transitions, panels). Companion surfaces skew slower (×1.25) to feel calm.
- **Streaming**: companion token stream — fade-in 120ms per token chunk, caret blinks 1.1s. No typewriter jitter on scroll.
- **View transitions**: cross-fade 220ms + 4px lift. Panel slide: 280ms from edge.
- **Respect `prefers-reduced-motion`**: collapse to opacity-only, ≤120ms.
- **Never bounce, never parallax** on companion surfaces — calm is the brand.

## 7. Voice & Tone (copy)

- Companion voice: warm, plain, never syrupy. Short sentences. Names the emotion before offering a frame ("That sounds heavy. Want to sit with it, or map it out?").
- UI copy: precise and quiet. "Add a provider" not "Supercharge your AI!". Empty states are one gentle sentence + one action.
- Technical copy (routing, keys): exact and brief. "OpenAI · gpt-5-mini · 12 reqs · $0.041". No exclamation marks anywhere.
- Disclosure honesty (per product principle): companion never claims feelings it doesn't have; UI never claims confidentiality it can't guarantee. Key copy in the UI is neutral-true ("encrypted in transit and at rest on the server") and never implies zero-knowledge, on-device custody, or "only you can read it." The full disclosure — that the server holds the DEK and can decrypt keys at reply time, i.e. this is *not* zero-knowledge — lives in `SECURITY.md`, not on every screen.

## 8. Accessibility

- Contrast: headings ≥ 7:1, body ≥ 4.5:1 (warm ink on sand white = ~9:1). Focus visible: 3px purple ring at 0.22 alpha + 1px solid. Keyboard: full tab order, visible focus, Escape closes panels. `aria-live="polite"` on streaming companion output. Reduced motion honored. Touch targets ≥ 44px. Dark mode meets AAA on body where possible.

## 9. Anti-patterns (do not)

- No pure-black surfaces or pure-white chat canvas — warm-tinted only.
- No bold (700) in sohne for UI — weight 300/400 only.
- No pill-shaped buttons, no harsh 2px borders, no flat single-layer shadows.
- No emoji in companion copy; no exclamation marks; no "AI" buzzwords in headlines.
- No simulating affect ("I feel your pain") — disclose, don't perform.
- No raw API key shown unmasked; mono-masked always, reveal-on-hold with audit log.
- No gratuitous animation, no bounce, no parallax on companion surfaces.
- No storing/echoing keys server-side in plaintext — keys are envelope-encrypted server-side (see `SECURITY.md`). No client vault, no on-device key custody — do not reintroduce "encrypted on-device" / "zero-knowledge" claims in UI copy.