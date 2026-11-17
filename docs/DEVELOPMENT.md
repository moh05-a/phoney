# Development Guide

Last updated 2026-11-16.

## 1. Layout

```
phoney/
├── desktop/agent/
│   ├── app/            the agent; app/web/ is the phone panel
│   ├── tests/          pytest suite
│   ├── tools/          ws_client.py (command-line phone), make_icon.py
│   └── pyproject.toml
├── scripts/            setup.ps1, start.ps1, autostart.ps1
├── docs/
└── README.md
```

## 2. Setup

Requirements: Windows, Python 3.13, Git.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This creates the virtual environment in `%USERPROFILE%\.venvs\phoney` (outside the project, so it still works if the project folder is synced by OneDrive or similar) and installs the agent in editable mode with the dev tools and the optional `audio` (volume level, now playing) and `tray` (tray icon) extras.

In VS Code, choose that interpreter (`Python: Select Interpreter` → `%USERPROFILE%\.venvs\phoney\Scripts\python.exe`), otherwise the editor reports the packages as missing.

## 3. Everyday commands

Run from `desktop\agent`, with `$py = "$env:USERPROFILE\.venvs\phoney\Scripts\python.exe"`:

```powershell
& $py -m app                          # run the agent (Ctrl+C stops it)
& $py -m pytest -q                    # tests (about 25 seconds)
& $py -m ruff check . ; & $py -m ruff format .
& $py tools\ws_client.py              # stream CPU and memory in the terminal, like a phone would
& $py tools\ws_client.py --seconds 600 --quiet --topics system.cpu system.memory system.network system.disk_io   # soak test
& $py -m app hooks show claude-code   # see INTEGRATIONS.md
```

To experiment without touching your real data, point the agent at another folder first: `$env:PHONEY_DATA_DIR = "C:\temp\pbx-test"`.

Checking by hand:

```powershell
curl.exe http://127.0.0.1:7420/api/v1/health
curl.exe -H "Authorization: Bearer <token>" http://127.0.0.1:7420/api/v1/system/snapshot
```

The panel can be developed in a desktop browser: open `http://127.0.0.1:7420/`, pair, and use the browser's phone-size preview. Reload after editing files in `app/web`; there is no build step.

## 4. How to extend

**A new widget type.** In `app/web/js/widgets.js` add one `define('mytype', { name, icon, topics, sizes, make })`. `make(widget)` returns `{ body, aside?, on: { 'topic': (payload, history) => … }, destroy? }`. It then appears in "Add widget". No other file changes. For settings, add `options: [{ key, label, type: 'bool' | 'choice', default, choices }]`; the values arrive in `widget.config`.

**A new metric topic.** Add a collector class in `app/monitoring/collectors.py` with `topic`, `interval_s`, `unsupported_reason()` and `sample()`, and add it to `system_collectors()`. Document the payload in `docs/API.md`.

**A new action for your deck.** No code: add an `[[action]]` to `%APPDATA%\Phoney\actions.toml` and restart the agent.

**A new action kind.** Add it to `Kind` and `execute()` in `app/commands/registry.py`, put the OS call in `app/platform/ops.py`, extend `FakePlatform` in `tests/conftest.py`, and add a test.

**A new integration.** Give it a flag in `IntegrationsConfig`, create its collectors or event topic in `build_context` only when the flag is on, and answer 501 from its endpoints when off.

## 5. Conventions

- Python: type hints, Pydantic at every boundary, no bare `except`, errors raised or logged and never silently dropped. `ruff` for lint and format (line length 110).
- Panel JavaScript: ES modules, no dependencies, agent data only ever becomes text nodes.
- Tests never touch the real machine: OS effects go through `PlatformOps` and are faked. Collector tests read real metrics but change nothing.
- A protocol change updates `docs/API.md` in the same commit.
- A new dependency needs a line in ARCHITECTURE.md section 5 saying why.

## 6. Git

Every push and pull request runs the tests and the linter on Windows through GitHub Actions (`.github/workflows/tests.yml`). Runtime data (`config.toml`, `actions.toml`, the database, logs) lives in `%APPDATA%\Phoney\`, never in the repository; `.gitignore` also excludes it in case it is copied in by mistake.

## 7. Building a native iOS app later

Not possible on Windows: compiling, signing and installing an iOS app needs macOS with Xcode. If a Mac becomes available, a SwiftUI client can be written against `docs/API.md` with no agent changes. It would add Keychain storage, certificate pinning and a proper wake lock; it would still not run in the background.
