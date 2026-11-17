# Phoney: Product Requirements

Last updated 2026-11-16. Reflects what was built.

## 1. What it is

A two-part personal tool that turns an old iPhone into an always-on control panel for one Windows PC:

- **Desktop agent** (Python, on the PC): samples system metrics, executes allowlisted actions, receives events from AI coding tools, stores configuration, tasks and notes.
- **Phone panel** (a web app served by the agent): dashboards of widgets, a control deck, developer and AI views, productivity tools. It is opened in Safari and added to the Home Screen.

There is no cloud service, account, or subscription. The phone talks directly to the agent over the local network.

## 2. The decision that shaped it: a web panel instead of a native app

The original brief asked for a native Swift app. The owner has no Mac, and a native iOS app can only be compiled, signed and installed with Xcode on macOS. The owner asked for whatever works without one.

| | Native Swift app | Web panel (chosen) |
|---|---|---|
| Needs a Mac | Yes | No |
| Re-signing with a free Apple ID | Every 7 days | Never |
| Can be built and tested on this Windows PC | No | Yes |
| Updates | Rebuild and reinstall | Restart the agent, reload the page |
| Look and feel | Fully native | Full screen from the Home Screen; close to native |
| Credential storage | Keychain | Browser storage (weaker; see SECURITY.md) |
| Runs in the background | No (iOS suspends it) | No (same) |

The protocol between phone and agent is unchanged by this choice, so a native app can be added later without touching the agent.

## 3. Inspiration: what was taken from PhoneBox

[PhoneBox](https://github.com/ersinkoc/phonebox) (MIT) is an Android panel with a .NET agent. Adopted ideas: the phone requests actions **by ID** while commands stay on the PC; pairing by QR code; settings and pairing reachable only from the PC; monitoring Claude Code and Codex sessions; pages of widgets on a grid. Answering Claude Code permission prompts from the phone was added later with its own design (see SECURITY.md). Not adopted: USB transport, the 100+ widget catalog, the visual editor, Home Assistant / OBS / Discord integrations. No PhoneBox code is used, and this project is published under its own name, Phoney, so it is not mistaken for the original.

## 4. Goals, in priority order

1. Reliability: the panel reconnects by itself and never shows stale data as if it were live.
2. Security: nobody else on the network can read metrics or trigger actions.
3. Low resource use on the PC.
4. Fast communication: 1-second metrics, instant button presses.
5. A polished, dense, dark-first phone interface.
6. Extensibility: new widget types and integrations without rewrites.
7. Easy install and maintenance.

## 5. Non-goals

Android or iPad layouts, cloud relay, accounts, public internet exposure, App Store distribution, and an unrestricted remote terminal in any form.

## 6. Features

| Area | Delivered |
|---|---|
| Monitoring | CPU (total, per core, frequency), memory and swap, network up/down, disk read/write, storage per volume, battery, top processes, NVIDIA GPU (optional package). Unsupported metrics are reported as such and their widgets say so. |
| Media | A player card with cover art, track progress, transport keys and volume (optional `audio` extra). Works with whatever Windows shows in its media flyout, including Spotify. |
| GitHub | Open pull requests with check and review state, review requests, recent activity and recently pushed repositories, through the `gh` tool. |
| Control deck | Buttons in groups, created and edited on the phone: label, icon, color, group, order. Built-in media keys, volume keys and lock. Owner-defined app launches, folders, URLs and commands. Sleep, restart and shut down when enabled. Dangerous actions need confirmation. |
| Developer | Git branch, changed and untracked counts, ahead/behind, last 10 commits for registered projects; listening ports and running instances of known dev tools. |
| AI coding | Claude Code and Codex sessions with state, recent activity, alerts with sound when a session finishes or needs input. Claude plan usage (5-hour and weekly) for subscribers. Claude Code permission prompts can be allowed or denied from the phone. |
| Productivity | Tasks with a priority flag, notes, pomodoro, stopwatch, countdown. |
| Widget system | Each widget has id, type, name, config, position, size, enabled. Multiple pages. Add, remove, reorder, resize, rename, hide, and per-widget settings through an edit mode. Layout stored on the agent. |
| Design | Desk-panel styling with a desk mode that fills the screen; dark and light themes, portrait and landscape layouts, connection indicator, loading / empty / error / stale states. |
| Security | One-time pairing codes, per-device random tokens, allowlist, confirmation, rate limits, audit log, PC-only admin page. |

## 7. Limitations

| Limitation | Consequence |
|---|---|
| iOS suspends web pages and apps that are not in the foreground | Live data and alerts only while the panel is on screen. It is designed to sit open on a stand. |
| The panel is served over plain HTTP on the local network | Traffic is not encrypted beyond your Wi-Fi's own encryption. See SECURITY.md for why and what limits the risk. |
| Without HTTPS, Safari does not offer the screen wake-lock API | Set Auto-Lock to Never on the phone. |
| The Home Screen app and Safari keep separate storage on iOS | Pair from the Home Screen app, not from the Safari tab. |
| psutil exposes no temperature sensors on Windows | No CPU temperature. |
| GPU metrics are NVIDIA-only | This PC has no `nvidia-smi`, so the GPU widget reports "Not available". |
| Calendar | Not implemented. A web page cannot read the iPhone calendar. |
| Copilot, Codex quota | No documented local interface; not implemented (see INTEGRATIONS.md). |

## 8. Owner decisions on record (2026-11-16)

Recent iPhone; no Mac available; free Apple ID; "do whatever works" → web panel.
