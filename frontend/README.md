# finzorr.ai — frontend

React 19 + TypeScript (strict, `noUncheckedIndexedAccess`,
`exactOptionalPropertyTypes`) + Vite (rolldown) + Tailwind. Two selectable
themes: Light (default) and the sci-fi FUI "Ops" skin, both driven by CSS
custom-property tokens in `src/index.css`.

## Run

```bash
npm ci
npm run dev        # http://localhost:5173 — /api and /ws proxy to :8000
npm test           # vitest (jsdom)
npx oxlint --deny-warnings
npm run build      # tsc -b && vite build
```

Copy `.env.example` to `.env.local` to override:

- `VITE_API_BASE_URL` — backend origin (empty in dev; the Vite proxy covers it)
- `VITE_GOOGLE_CLIENT_ID` — Google Identity Services client id (empty hides
  the Google button; the dev bypass still works)

## Layout

- `src/pages/` — `Login`, `Chat` (main app), `Share` (public transcript)
- `src/components/chat/` — sidebar, message bubbles, input (mic + image
  attach), price chart, artifact panel, persona picker
- `src/hooks/useChatSocket.ts` — WS client: exponential-backoff reconnect,
  ping keepalive, offline send queue, mid-stream cancel
- `src/store/` — zustand stores (auth, chat, theme, settings)
- `src/api/` — one typed module per backend domain over a single axios
  instance (`withCredentials`)
- `src/lib/` — pure helpers (artifact block parsing)

## Conventions

- Tests live next to the code as `*.test.ts(x)`; vitest config is separate
  from `vite.config.ts` (rolldown-vite vs vitest's bundled rollup-vite types
  don't mix in one file).
- oxlint runs with warnings-as-errors in CI; keep the config in
  `.oxlintrc.json` authoritative.
- WS frames and REST shapes mirror the backend's pydantic schemas — update
  `src/types.ts` / `src/api/*` in the same PR as any backend schema change.
