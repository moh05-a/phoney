# Roadmap and status

Last updated 2026-11-16.

| Phase | Scope | Status |
|---|---|---|
| 0 | Research and planning | Done |
| 1 | Desktop agent | Done and tested |
| 2 | Phone client | Done as a web panel; tested in a desktop browser, **not yet on an iPhone** |
| 3 | Control deck | Done; real key presses and launches not yet exercised by hand |
| 4 | Developer and AI integrations | Done; hooks not yet run against real Claude Code or Codex |
| 5 | Productivity | Done, except calendar |
| 6 | Polish | Mostly done; see open work |

## How it was verified

| Check | Result |
|---|---|
| Automated tests (`pytest`) | 115 passing: security, actions and deck, WebSocket and hub, features and integrations |
| Lint (`ruff check`) | Clean |
| Agent started for real and exercised with `curl` | Health, pairing, snapshot, capabilities all correct |
| Panel driven in headless Microsoft Edge at phone size (390×844 and 844×390) | Pairing with a code, live widgets, both dashboard pages, edit mode, deck, confirmation dialog, button editor, Dev, AI, tasks, timers, settings, light theme, landscape. No JavaScript errors. |
| 10-minute stream of four 1-second topics | About 580 samples per topic in 600 s; longest gap between samples 1.38 s |
| Reconnection in the browser | Agent stopped: panel showed "Reconnecting" with dimmed widgets. Agent restarted: live again by itself within 14 s, no reload |
| Revoking the device from the PC | Panel returned to the pairing screen with an explanation and forgot its token |
| Resource use with a full dashboard subscribed (9 topics, 90 s) | Agent averaged 0.10% of total CPU (about 1.6% of one core) and 92 MB RAM. Idle with no phone: about 0.3% of one core. The 80 MB memory target in the plan was missed by 12 MB. |
| `scripts\setup.ps1` | Ran cleanly on this PC |

Two bugs found and fixed during this testing, each now covered by a test:

- CPU readings were zero, because `psutil.cpu_percent` keeps a separate baseline per thread. The collector now computes percentages from its own baseline.
- The top-processes sample cost about 1.5 s of CPU every 5 s, because psutil falls back to a full system scan for each process it may not open. It now uses one Windows call for all processes (`app/platform/winproc.py`) and costs about 1 ms.

## Added in the second round (2026-11-16)

| Feature | How it was verified |
|---|---|
| Widget settings (name, shown/hidden, per-type options such as network adapter, drive, 24-hour clock, core bars) | Headless browser: chose an adapter, saved, confirmed the agent stored it and the widget showed it |
| Volume slider | Headless browser moved the slider and the real PC volume followed (64 → 62 → 64), read back through Core Audio |
| Now-playing widget | Renders and reads the Windows media session; nothing was playing during the test, so a live track title is unconfirmed |
| `volume.set` parameter validation | Tests: wrong type, out of range, extra or missing names all refused |
| Tray icon | Agent started windowless with `pythonw` and logged the tray as started; Quit path tested against real servers. The menu itself was not clicked by hand. |
| Claude usage widget | The real forwarder script was run with sample status-line data; the widget showed both limits and reset times. Not yet seen with a live Claude Code session. |

## Added in the third round (2026-11-16)

| Feature | How it was verified |
|---|---|
| Desk-panel redesign, desk mode, page dots | Headless browser, portrait and landscape; the Desk page fills a landscape phone with nothing to scroll |
| Player card (cover art, progress, volume) | Against the live Spotify session on this PC: title, artist, album, position and the real cover image |
| GitHub overview | Against the live account through `gh`: activity, repositories and pull requests rendered in the widget and the Dev tab |
| Claude Code session hooks | Real: events from a live session arrived at the running agent |
| Permission prompts on the phone | Simulated hook call plus a real browser panel: card shown, Allow tapped, the documented decision JSON returned. Not yet seen with a real prompt. |
| Claude usage rings | Sample data through the real forwarder. The status line does not run in the VS Code extension, so live numbers need a terminal session. |

A bug found and fixed in this round: with music playing, the now-playing query hung when it shared a thread with the volume query; it now runs on its own thread with a timeout.

## Not verified, and why

| Item | Why | How you verify it |
|---|---|---|
| The panel on a real iPhone in Safari and from the Home Screen | No device available to the build session | Follow the Quick start in README.md. Things to look at: safe-area spacing around the notch and home indicator, tap feel, the chime after the first touch. |
| Reaching the agent from another device over Wi-Fi | Tests ran on loopback to avoid opening the firewall without you | Start the agent normally; Windows will ask about the firewall the first time |
| Real media keys, volume keys, lock, app launches | Tests use a fake OS layer so they cannot disturb the PC | Press the buttons on the deck |
| Sleep, restart, shut down | Same; and they are off by default | Set `allow_power_actions = true`, restart the agent, try Sleep first |
| Claude Code and Codex hooks with the real tools | Installing them changes your personal settings files | `python -m app hooks install claude-code`, then start a session |
| NVIDIA GPU metrics | This PC has no NVIDIA tooling | `pip install nvidia-ml-py` on a machine with an NVIDIA GPU |
| Autostart script | It adds a Startup entry to your account | `scripts\autostart.ps1`, then sign out and in |
| 24-hour stability | Time | Leave it on the stand for a day and check `logs\agent.log` |

## Acceptance criteria from the plan

Phase 1 (agent):

- [x] `python -m app` starts both listeners on Python 3.13; `/health` reports protocol 1.0
- [x] Pairing from the local page yields a token; the code fails a second time and after expiry
- [x] Every endpoint except health and pair answers 401 without a valid token
- [x] Stream runs 10 minutes without gaps over 2 seconds (see table above)
- [x] With no client connected, no collector runs
- [x] Subscribing to an unsupported topic is rejected cleanly
- [x] Revoking a device closes its socket with 4401
- [x] Security tests pass
- [x] Resource use measured (see table above)
- [x] `pytest` and `ruff` green

Phase 2 (client), as adapted to the web panel:

- [x] Pairs in under a minute with the code from the PC
- [x] CPU and memory widgets update every second
- [x] Shows a reconnecting state and recovers by itself
- [x] Revocation on the PC returns the panel to the pairing screen
- [x] Dark and light themes; portrait and landscape
- [ ] All of the above confirmed on the physical iPhone
- [ ] Screen stays awake (needs Auto-Lock set to Never; no wake lock over HTTP)

Phase 3 (deck):

- [x] Default allowlist ships with media, volume, lock, File Explorer and Terminal only
- [x] Buttons can be created, renamed, re-iconed, recolored, regrouped, reordered and deleted from the phone, and are stored on the agent
- [x] A button cannot reference an action outside the allowlist
- [x] Dangerous actions cannot run without the confirmation round-trip
- [x] Every run is in the audit log
- [ ] Real launches and key presses confirmed by hand

Phase 4 (developer and AI):

- [x] Each integration can be turned off and the agent runs normally without it
- [x] Git status matches a real test repository in clean and dirty states
- [x] Hook events reach a subscribed panel; no prompt or transcript content is stored
- [x] Install shows the exact change and asks first; uninstall restores the file
- [ ] Confirmed with real Claude Code and Codex sessions
- [x] Claude Code usage display (status-line forwarder; tested with the real forwarder script and sample data)

Phase 5 (productivity):

- [x] Tasks and notes: create, edit, complete, delete; stored on the agent
- [x] Timers computed from timestamps, so they survive backgrounding
- [x] Edits while disconnected fail with a visible message (they are not queued)
- [ ] Calendar (not possible from a web page)

## Open work, most valuable first

1. **HTTPS with a locally trusted certificate**, closing the limit in SECURITY.md section 4. Needs the phone for testing the certificate install.
2. **Try it on the iPhone** and fix whatever the real device shows.
3. mDNS name for the agent, so the panel survives the PC's IP address changing.
4. A database migration tool before the first schema change.
