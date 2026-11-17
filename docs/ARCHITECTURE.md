# Architecture

Last updated 2026-11-16. Reflects what was built.

## 1. Overview

```
 iPhone (Safari / Home Screen)                 Windows PC
┌───────────────────────────┐  HTTP + WS    ┌──────────────────────────────────────┐
│ Phone panel (web app)     │  port 7420    │ Desktop agent (one Python process)   │
│  views · widget registry  │◄─────────────►│  LAN listener   0.0.0.0:7420         │
│  live.js (WebSocket)      │  bearer token │   serves the panel + API + WebSocket │
│  api.js  (REST)           │               │  Local listener 127.0.0.1:7421       │◄── browser on the PC
└───────────────────────────┘               │   pairing page, devices, hook intake │◄── Claude Code / Codex hooks
                                            │  hub · actions · integrations · DB   │
                                            └────────────────┬─────────────────────┘
                                                             ▼
                                              psutil · Windows APIs · git · NVML
```

One protocol connects the two sides (API.md). The agent works with no phone connected, and the panel is just a client of that protocol: a native app could replace it without agent changes.

## 2. Desktop agent (`desktop/agent/app`)

### 2.1 Two listeners in one process

| Listener | Bind | Serves | Auth |
|---|---|---|---|
| LAN | `0.0.0.0:7420` | Panel files, REST API, WebSocket | Device token (except health, pair, and the static panel files) |
| Local | `127.0.0.1:7421` | Status page, pairing page with QR, device revocation, hook intake | Reachable only from the PC; Host and Origin headers checked |

Both are Uvicorn servers in the same asyncio loop, each with its own FastAPI app (`main.py`). Administrative routes do not exist on the network-facing app at all, so no routing mistake can expose them. A test asserts this.

### 2.2 Modules

```
app/
├── __main__.py        CLI: run the agent, manage AI-tool hooks
├── main.py            builds both apps, logging, runs both servers
├── tray.py            optional system-tray icon (status page, pair, data folder, quit)
├── core/              config (TOML → Pydantic), context (shared state), errors (protocol envelope)
├── api/               lan.py (pair, agent, system, actions, deck, layout), extras.py (dev, AI, tasks, notes),
│                      local.py (PC-only pages and hook intake), deps.py (auth, guard middleware)
├── networking/ws.py   the live WebSocket
├── monitoring/        collectors.py (one class per topic), hub.py (subscriptions and fan-out)
├── commands/          registry.py (allowlist, executors, confirmation tokens)
├── integrations/      dev.py (git, ports), ai.py (session store), github.py (overview through the `gh` tool), permissions.py (prompt broker), hooks.py + hook_forward.py + statusline_forward.py (installer, forwarders)
├── platform/          the only OS-specific code: ops.py (keys, lock, power, launching), winproc.py (fast process list), winmedia.py (volume level, now playing)
├── security/          auth.py (pairing, tokens, address checks), ratelimit.py
├── database/models.py SQLite schema
├── defaults/          config.toml and actions.toml copied to the data folder on first run
└── web/               the phone panel
```

### 2.3 Monitoring: sample only what someone is watching

- A collector is a small class with a `topic`, an interval, `unsupported_reason()` and a blocking `sample()`.
- The hub starts a collector's loop when the first client subscribes to its topic and cancels it when the last one leaves. With no phone connected the agent samples nothing (asserted by a test, visible on the status page).
- `sample()` runs in a worker thread, so slow calls never stall the event loop.
- The last 60 samples per topic are kept in memory and replayed to a new subscriber so charts fill immediately. Nothing is written to disk.
- A collector that cannot run (no battery, no NVIDIA GPU) is listed under `unsupported_topics` with a reason; the rest is unaffected.
- The process list comes from a single `NtQuerySystemInformation` call on Windows (`platform/winproc.py`). psutil reads processes one at a time and, for each one the user may not open, rescans the whole system, which cost about 1.5 s of CPU per sample here.
- CPU percentages are computed from the collector's own baseline of `cpu_times`, because `psutil.cpu_percent` keeps a separate baseline per calling thread and returns zeros from a thread pool.

### 2.4 Actions: allowlist on the PC, buttons on the phone

- An **action** is something the agent can execute: built-ins (media keys, volume keys, lock, power) or entries in `actions.toml` (`launch_app`, `run_command`, `open_path`, `open_url`). Only that file, edited at the PC, defines them.
- A **button** is presentation: label, icon, color, group, order, and an action ID. Buttons are created and edited from the phone.
- The phone never sees command lines or paths, and no endpoint accepts one. Commands run without a shell.
- `run_command` and power actions are dangerous by default. A dangerous action needs a two-step call with a server-issued, single-use, 30-second confirmation token bound to the device and action.
- All OS effects go through `PlatformOps`, so tests use a fake and never touch the real machine.

### 2.5 Integrations

Each integration has a flag in `config.toml`. When off, its collectors are not created, its endpoints answer `501 unsupported`, and its feature name is absent from the capability list, so the panel hides or explains it. AI events are display-only and can never trigger an action. Mechanisms are documented in INTEGRATIONS.md.

### 2.6 Persistence

SQLite via SQLAlchemy 2, synchronous sessions (writes are tiny). Tables: `devices`, `pairing_codes`, `audit_log`, `button_groups`, `buttons`, `layouts`, `tasks`, `notes`. AI sessions and metric history are in memory only.

Data folder `%APPDATA%\Phoney\` (override with `PHONEY_DATA_DIR`): `config.toml`, `actions.toml`, `phoney.db`, `logs\agent.log`.

The schema is created with `create_all`. There is no migration tool yet; the first schema change that must preserve data should add one.

### 2.7 Process model on Windows

A normal process in the signed-in user's session, with a tray icon when the `tray` extra is installed, optionally started at sign-in from the Startup folder (`scripts\autostart.ps1`, using `pythonw` so there is no window). Not a Windows service: services run in an isolated session and cannot launch visible apps, send media keys, or lock the workstation.

## 3. Phone panel (`desktop/agent/app/web`)

Plain HTML, CSS and JavaScript modules. No framework, no build step, no third-party code.

```
web/
├── index.html, styles.css, manifest.webmanifest, icon-180.png
└── js/
    ├── main.js      shell: pairing screen, tabs, connection chip, settings, global AI alerts
    ├── api.js       REST client, token storage, confirm-and-retry for dangerous actions
    ├── live.js      WebSocket state machine and topic subscriptions
    ├── ui.js        element builder, icons, sheets, dialogs, toasts, formatters, chime
    ├── widgets.js   widget registry, sparkline drawing, grid packing
    └── views/       dashboard.js, deck.js, work.js (Dev, AI), focus.js (tasks, notes, timers)
```

### 3.1 Connection state machine (`live.js`)

```
connecting ──hello──► online ──drop──► connecting (backoff 1s, 2s, 4s … 30s, with jitter)
     ├── close 4401 ► unauthorized  (token forgotten, pairing screen shown)
     ├── close 4426 ► incompatible  (reload needed)
     └── close 4409 ► replaced      (same device connected elsewhere; tap the chip to take over)
```

- The client pings every 15 s; 35 s of silence means the link is dead and it reconnects.
- Coming back to the foreground or back online retries immediately instead of waiting out the backoff.
- Subscriptions are re-sent after every reconnect. The last values stay on screen, dimmed, while the state is not `online`.

### 3.2 Widget system (`widgets.js`)

A widget type is one `define(type, { name, icon, topics, sizes, make })` call. `make` returns DOM nodes and a map of topic → update function. The dashboard knows nothing about individual types:

- It reads the layout document, packs enabled widgets into the grid, and mounts each through the registry.
- Mounting subscribes to the widget's topics; unmounting unsubscribes, which lets the agent stop sampling.
- A type may declare `options` (on/off or a choice). The dashboard builds a settings sheet from them and stores the values in the widget's `config`; choices can come from live data, such as the list of network adapters.
- **Desk mode** (default) hides the tab bar on the panel and shows page dots; a menu button leads to the other screens. On a landscape phone the row height is computed so the first page fills the screen exactly (8 columns by 4 rows).
- Open panels reload themselves when the agent reports a new `panel_build`, so an agent update reaches the phone without touching it.
- Unknown types render a placeholder; topics the PC cannot provide render "Not available on this PC".
- The grid is 4 columns in portrait and 8 in landscape. Stored positions are for 4 columns; other widths are re-packed at render time.

### 3.3 Where data lives

| Data | Where | Why |
|---|---|---|
| Device token, theme, sound setting, timers, cached layout | Phone (browser storage) | Works offline; timers must not depend on the network |
| Layout, buttons, groups, tasks, notes | Agent | Survives clearing the browser or re-pairing; shared by every paired device |
| Actions allowlist, config | Agent, edited only at the PC | Security |

Changes made on one device are announced on the `sync` topic so other open panels refresh.

## 4. Main technical decisions

| # | Decision | Alternative | Reason |
|---|---|---|---|
| D1 | Web panel served by the agent | Native Swift app | No Mac available; see PRD section 2 |
| D2 | Plain HTTP on the LAN | HTTPS with a self-signed certificate | Safari refuses WebSockets to untrusted certificates and Home Screen apps cannot click through warnings, so HTTPS would need a certificate profile installed by hand on the phone, which could not be tested here. Trade-offs in SECURITY.md. |
| D3 | Separate loopback listener for admin, pairing page and hooks | One listener with address checks | Routes that are not bound cannot be reached by mistake |
| D4 | Actions defined on the PC only; phone edits buttons | Phone defines commands | Otherwise a token is equivalent to remote code execution |
| D5 | Subscription-driven sampling | Fixed polling of everything | Lowest idle cost; matches the widget model |
| D6 | First-message WebSocket auth | Authorization header | Browsers cannot set headers on a WebSocket; a query string would put the token in logs |
| D7 | No framework or build step for the panel | React / Vite | Nothing to install, nothing to go stale, easy to read |
| D8 | Volume keys built in; absolute volume and now playing as an optional extra | Requiring Core Audio packages | The base install stays small and still controls volume; the `audio` extra adds the slider and track info |
| D9 | One shared layout for all devices | Per-device layouts | One panel is the use case; re-pairing keeps the layout |
| D11 | GitHub through the `gh` command-line tool | Storing a personal access token | The agent never holds a GitHub credential; `gh` keeps it in the Windows credential store |
| D12 | Permission prompts are held only while a phone is subscribed, and time out to the terminal | Always intercept | Sitting at the PC with no panel open costs nothing; the terminal always remains a way to answer |
| D13 | Media-session queries run on their own thread | Sharing the collector's thread | The session API never completes on a thread where COM was initialized for the volume interface (found in testing: the sample hung) |
| D10 | No plugin loader | `plugins/` directory | The collector and widget registries already give extensibility without loading arbitrary code |

## 5. Dependencies

| Package | Purpose |
|---|---|
| fastapi, uvicorn, websockets, pydantic | Web framework, server, WebSocket transport, validation |
| psutil | Metrics |
| sqlalchemy | Persistence |
| segno | QR code on the pairing page (pure Python) |
| pytest, httpx, ruff (dev) | Tests and lint |
| pycaw, winrt-* (optional extra `audio`) | Absolute volume through Core Audio; now playing through the Windows media session |
| pystray, pillow (optional extra `tray`) | System-tray icon |
| nvidia-ml-py (optional extra `gpu`) | NVIDIA metrics. The `pynvml` package on PyPI is deprecated in its favor. |

Media keys, lock and power use Windows APIs through `ctypes` and system executables.
