# Stillside — Mobile / PWA Audit (`apps/web`)

Audit date: 2026-07-15. Scope: mobile responsiveness, touch UX, PWA setup, forms (BYOK/vault), streaming, secondary screens.

## Summary

PWA foundation is solid: `viewport-fit:cover`, no `user-scalable=no`, standalone manifest with a maskable icon, service worker network-first for navigations, `dvh` in the app shell. But five **systemic** gaps hit the whole app:

1. **`env(safe-area-inset-*)` is used nowhere** — bottom rail, chat composer, toasts, login controls collide with the home indicator / notch.
2. **Every text input is 15px (journal 14px)** → iOS Safari zooms the page on focus. One CSS rule fixes ~5 screens.
3. **Tap targets are universally <44px** — `.btn` ~34px, `.btn-sm` ~27px, `.icon-mini` 34×34, `.seg button` ~29px, `.cbx-opt` ~32px, `.mini` ~22px, chips ~24px.
4. **Hover-only actions are invisible on touch** — message action rows (Speak / Retry / Save) are gated on `:hover` with no `@media (hover:none)` fallback.
5. **Icons are SVG-only** — iOS ignores SVG for `apple-touch-icon`, so the home-screen icon is blank; no 192/512 PNG raster fallback for Android installability.

**Count: P0 — 12, P1 — ~18, P2 — ~25.**

## Top-5 to fix first

1. `globals.css:1112` — `font-size: 16px` on `.input, textarea, select` (kills iOS-zoom across onboarding, chat, journal, memory, login — one rule, ~5 P1).
2. Safe-area insets on `.rail`, `.composer`, `.toaster`, `.login-controls`, `.chat-head` — rail/composer/toaster currently sit under system gestures (3 P0).
3. `OnboardingScreen.tsx:416-424` — hidden API-key field is controlled by the masked string → typing char-by-char destroys the key. Switch to `value={keyVal}` + `type="password"` (P0, data-loss).
4. `RoutingScreen.tsx:87-137` — 7-column table with no scroll wrapper → page-level horizontal scroll on ≤414px (P0).
5. `globals.css:5568` toaster — `bottom:20px` overlaps the bottom rail and composer on every screen (P0).

---

## P0 — breaks mobile use

| # | File:line | Problem | Fix |
|---|---|---|---|
| 1 | `globals.css:1112` | `.input, textarea, select { font-size:15px }` → iOS zoom on focus (chat, onboarding, journal 14px, memory, login) | `font-size: 16px` globally or in `@media (max-width:760px)` |
| 2 | `OnboardingScreen.tsx:416-424` | `value={revealed ? keyVal : maskKey(keyVal)}` — hidden field holds masked string; char-by-char typing destroys the key | `value={keyVal}` + `type="password"`, mask in a separate read-only element |
| 3 | `globals.css:3853-3908` | Mobile bottom rail 62px, no `env(safe-area-inset-bottom)` — icons under home indicator | `height: calc(62px + env(safe-area-inset-bottom)); padding-bottom: env(safe-area-inset-bottom)` |
| 4 | `globals.css:2190` (composer) | `padding:14px 18px`, no safe-area — send/mic under home indicator | `padding-bottom: calc(14px + env(safe-area-inset-bottom))` |
| 5 | `globals.css:5568` (toaster) | `bottom:20px` overlaps rail/composer, no safe-area | `bottom: calc(72px + env(safe-area-inset-bottom))`; lift further on chat; or `top` on mobile |
| 6 | `globals.css:1937` | Markdown tables have no overflow wrapper → wide GFM table expands bubble, `.stream` scrolls horizontally | `.msg.them .body table { display:block; overflow-x:auto; max-width:100% }` (or wrapper in `Markdown.tsx`) |
| 7 | no `img` rule | LLM `![]()` renders raw `<img>` w/o `max-width:100%` → horizontal scroll | `.msg.them .body img { max-width:100%; height:auto }` + `loading="lazy"` in `Markdown.tsx` |
| 8 | `globals.css:1996-2016` | `.msg-them-actions`/`.msg-me-actions` `opacity:0` only on `:hover` — Speak/Retry/Save invisible on touch | `@media (hover:none){ opacity:1 }` + `:focus-within{ opacity:1 }` |
| 9 | `RoutingScreen.tsx:87-137` | 7-col table, no scroll wrapper / card collapse → page horizontal scroll on ≤414px | Wrap in `.rt-scroll { overflow-x:auto }` + `min-width:560px`; ≤640px card collapse with `data-label` |
| 10 | `MemoryScreen.tsx:266-322` + `globals.css:1567` | family `.key-row` (label+select+seg) no `flex-wrap` → ~460px in a 378px card | `@media (max-width:620px){ .key-row{flex-wrap:wrap} .key-row .seg{width:100%} }` |
| 11 | `layout.tsx:18` | `apple` icon is SVG — iOS ignores → blank home-screen icon | PNG 180×180: `apple: [{ url:'/icons/apple-touch-icon-180.png', sizes:'180x180' }]` (needs asset) |
| 12 | `globals.css:4955` | `.jwrite { min-height:100vh }` (not `dvh`) — bottom controls hidden under iOS URL bar | `min-height: 100dvh` |

## P1 — noticeably bad

**Safe-area / keyboard**
- `globals.css:226` `.login-controls top:20px` collides with notch → `top: calc(20px + env(safe-area-inset-top))`.
- No `scroll-margin-top`/`scroll-padding-top` — lower onboarding fields (base URL, custom model) covered by keyboard.

**BYOK / Vault forms (onboarding + family)**
- All key/passphrase fields lack `autoCapitalize="off" autoCorrect="off" spellCheck={false}` → mobile keyboard autocorrects `sk-`, capitalizes.
- Passphrase fields lack show/hide toggle (`OnboardingScreen:403,504`, `FamilySettingsTabs:858,892`, `FamilySettingsScreen:534`).
- No `enterKeyHint` (`go`/`done`/`next`) on any field.
- Family fields lack `autoComplete` (`current-password`/`new-password`/`off`).
- Base-URL inputs lack `inputMode="url"`; key/model lack `inputMode="text"`.
- Validation errors render **below** the field → covered by keyboard; `.err` 13px on `--warn` (pink) weak contrast. Show above submit button, ≥14px, `--danger` color.

**Tap targets (<44px) — systemic**
- `.btn` ~34px, `.btn-sm` ~27px, `.seg button` ~29px, `.icon-mini` 34×34, `.cbx-opt` ~32px, `.mini` ~22px, `.pc-act` ~28px, `.jactions button` ~24px, `.toast-close` 22×22, `.chip` ~24px.
- Wrap fix: `min-height:44px` (and `min-width:44px` for icon-only) on `.btn/.btn-sm/.seg button/.icon-mini`; `.cbx-opt{padding:12px}`; `.mini{padding:8px 12px;min-height:36px}`; `.toast-close{width:36px;height:36px}`.

**Chat / streaming**
- `ChatScreen.tsx:151-155` + `globals.css:1804` — `scroll-behavior:smooth` + `scrollTo` per token → janky scroll. `behavior: streaming!==null ? 'auto' : 'smooth'`.
- `ChatScreen.tsx:151-155` — auto-scroll yanks to bottom even when user reads history. Add near-bottom check (`scrollHeight - scrollTop - clientHeight < 80`).
- `ChatScreen.tsx:594/1241` — re-render Markdown per token → CLS from partial fence/heading. Debounce parse ~30-50ms or plain-text bubble during stream.
- No Copy button for assistant reply (only Speak) → add `.mini` Copy with toast, visible on touch via P0 #8.
- Inline unlock input in lockout banner inherits 15px (same iOS-zoom) — fixed by P0 #1.

**Secondary screens**
- `SettingsScreen.tsx:618` — Remove provider fires immediately, no confirm → two-step inline confirm (mirror Memory wipe).
- `SettingsScreen:441` Revoke/Remove `.btn-sm` ~28px (destructive).
- `MemoryScreen.tsx:462` — empty state has no CTA (just `.help` line) → add "Start chat" button to seed memories.
- `RoutingScreen.tsx` — no skeleton/loading (`state===null` → blank screen).

## P2 — polish

- `manifest.webmanifest` — icons SVG-only, no 192/512 PNG + maskable PNG → add raster (Android installability).
- `manifest` — no `id` field → `"id": "/?"`.
- `layout.tsx:24` — static `themeColor` (dark) in SSR → light users get dark status bar on first paint. Use `[{media:'(prefers-color-scheme:light)',color:'#faf6f2'}, …]`.
- Breakpoints are `max-width` (desktop-first), 7 ad-hoc values → long-term refactor to `min-width` scale.
- `sw.js:62-77` — cache-first for `/manifest.webmanifest` and `/icons/*` → stale icons post-release; network-first for these paths. Precache omits `maskable.svg`.
- `sw.js:44-57` — offline navigation falls back to `/` (landing), not app shell.
- Fonts Sohne/SourceCodePro named but never loaded (no `@font-face`/`next/font`) → load via `next/font/local` with `display:swap`, or drop from stack.
- `.key-row .input` lacks `min-width:0` → long key can overflow flex row (`globals.css:1567`).
- `ModelCombobox.tsx:122-140` — filter input lacks autocapitalize-off; `.cbx-menu` desktop dropdown → bottom-sheet on ≤620px.
- `RoutingScreen:100-112` — row-as-link with no touch affordance; `title` invisible on touch.
- `.jreadfull`, practices `.pr-chip`, `.seg` → `min-height:44px`.
- `.toaster` no `max-height`/overflow → stack grows above viewport; cap at 3.
- `.mem-summary` no `overflow-wrap:anywhere`.
- `SettingsScreen:617` — empty `<span/>` leaves a gap in vault rows at 720px.
- "Honest limits" disclosures on `.help` 12px → 14px on mobile (critical security text).

---

## Quick wins (one pass, CSS-only)

```css
/* 1. Kills iOS-zoom on all fields at once (P0 #1, ~5 P1) */
.input, textarea, select { font-size: 16px; }

/* 2. Tap targets to 44px (systemic) */
.btn, .btn-sm, .seg button, .icon-mini { min-height: 44px; }
.btn-sm, .icon-mini { min-width: 44px; }
.cbx-opt { padding: 12px; min-height: 44px; }
.mini { padding: 8px 12px; min-height: 36px; }
.toast-close { width: 36px; height: 36px; }
.pc-act { padding: 10px 14px; min-height: 40px; }
.jactions button, .jconfirmdel button { padding: 8px 12px; min-height: 40px; }

/* 3. Safe-area (P0 #3-5) */
@media (max-width: 760px) {
  .rail { height: calc(62px + env(safe-area-inset-bottom)); padding-bottom: env(safe-area-inset-bottom); }
}
.composer { padding-bottom: calc(14px + env(safe-area-inset-bottom)); }
.toaster { bottom: calc(72px + env(safe-area-inset-bottom)); }
.login-controls { top: calc(20px + env(safe-area-inset-top)); right: calc(22px + env(safe-area-inset-right)); }

/* 4. Hover-only actions on touch (P0 #8) */
@media (hover: none) { .msg-them-actions, .msg-me-actions { opacity: 1; } }
.msg-them-actions:focus-within, .msg-me-actions:focus-within { opacity: 1; }

/* 5. Markdown overflow (P0 #6, #7) */
.msg.them .body table { display: block; overflow-x: auto; max-width: 100%; }
.msg.them .body img { max-width: 100%; height: auto; }

/* 6. Chat streaming scroll (P1) */
.stream { overscroll-behavior: contain; }

/* 7. jwrite dvh (P0 #12) */
.jwrite { min-height: 100dvh; }
```

Plus two non-CSS P0 fixes:
- `OnboardingScreen.tsx:416-424` — `value={keyVal}` + `type="password"` (P0 #2, data-loss).
- `RoutingScreen.tsx` — wrap `<table>` in `<div className="rt-scroll">` + `min-width:560px` (P0 #9).