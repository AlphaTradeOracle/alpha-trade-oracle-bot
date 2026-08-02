# Brand assets

Drop your logo here as **`logo.png`** (or `.svg`) — the app picks it up automatically.

- Path is configured in `src/config/branding.ts` (`logoSrc`).
- Recommended: square, at least 512×512, PNG with transparency or SVG.
- While `logo.png` is missing, `logo-fallback.svg` is rendered instead, so the
  layout never breaks.

To use a different filename or format, change `logoSrc` in `src/config/branding.ts`.
