# LBCA Monitoring System — Web app

React 18 + Vite, with `react-router-dom`, `recharts` for the charts and
`lucide-react` for icons. No UI framework — the design system is plain CSS
custom properties, which keeps the bundle small and the styling easy to change.

## Run it

```bash
npm install
npm run dev        # http://localhost:5173
```

The backend must be running (see `../backend/README.md`). If it is somewhere
other than `http://localhost:8000`, set `VITE_API_URL` in `.env`.

```bash
npm run build      # production build into dist/
npm run preview    # serve that build locally
npm run lint
```

## Layout

```
src/
├── api/
│   ├── client.js        fetch wrapper: tokens, silent refresh, error shaping
│   └── endpoints.js     every API call, named after what it does
├── config/
│   └── images.js        all photographs + the school's name — edit here
├── context/
│   ├── AuthContext.jsx  who is signed in; survives a page reload
│   ├── ThemeContext.jsx light/dark, remembered per browser
│   └── ToastContext.jsx notifications
├── components/
│   ├── common/          buttons, badges, modal, table shell, avatars…
│   ├── charts/          Recharts wrappers that follow the design tokens
│   └── layout/          AppShell, Sidebar, Header
├── pages/
│   ├── auth/            Login, Register, VerifyOtp, Forgot, Reset
│   └── app/             Dashboard, Students, StudentProfile, Pace,
│                        EarlyWarning, Teachers, Approvals,
│                        ClassManagement, Schedule, Account
├── styles/
│   ├── tokens.css       colours, type, spacing, shadows — light and dark
│   ├── base.css         reset + shared primitives
│   ├── auth.css         the signed-out screens
│   ├── layout.css       sidebar, header, content area
│   └── pages.css        screen-specific styling
└── App.jsx              routing and the role guards
```

## Changing the pictures

Everything is in `src/config/images.js`. To use your own photo, drop it into
`public/images/` and point the entry at `/images/your-file.jpg`.

Each photograph is loaded through an `Image()` first and only applied once it
has loaded, so a missing or slow file leaves the gradient behind it rather than
a broken frame. The sign-in screen even has a second URL it falls back to.

## Changing the look

`src/styles/tokens.css` is the single source of truth. The brand ramp
(`--brand-50` … `--brand-950`) drives buttons, links, the sidebar and the
charts; the four status ramps (`--ok-*`, `--warn-*`, `--risk-*`, `--crit-*`)
drive every risk badge and progress bar.

Dark mode redefines the same names under `:root[data-theme="dark"]`, so a new
colour only has to be written once per theme. The charts read their colours
from these variables at render time, which is why they recolour when the theme
switches.

## How auth works here

`api/client.js` keeps the access and refresh tokens in `localStorage` and
attaches the access token to every request. On a 401 it refreshes once,
transparently, and replays the request; if the refresh fails it clears the
session and fires a `lbca:session-expired` event that `AuthContext` listens for.
Parallel 401s share a single refresh call rather than stampeding.

A random device id is generated per browser and sent with every sign-in — that
is what lets the server remember a device and skip the 2FA challenge for
90 days.
