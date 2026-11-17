# Phoney

Turn an old iPhone into a desk panel for your Windows PC: live system stats, a music player, your GitHub pull requests, Claude Code sessions, and a deck of buttons that control the PC. It works over your home Wi-Fi with no cloud service, no account and no App Store.

> **Status: early preview (0.2).** Tested on one Windows 11 PC and in a phone-sized browser. It has not yet been tried on many real phones. Bug reports are very welcome.

- **Desktop agent** (`desktop/agent`, Python + FastAPI) runs on the PC: live metrics, an allowlist of actions, AI-tool events, tasks and notes.
- **Phone panel** (`desktop/agent/app/web`) is a web app the agent serves. Open it in Safari, add it to the Home Screen, and it runs full screen like an app. No Mac, no Xcode, no weekly re-signing.

## What it does

| Screen | What you get |
|---|---|
| Panel | A desk-panel look that fills a landscape phone: big clock, a music player with cover art and progress (Spotify or anything else Windows shows in its media controls), Claude usage rings. Pages of widgets: CPU, memory, network, disk activity, storage, battery, GPU (NVIDIA), top processes, clock, AI sessions, Claude usage, git projects, tasks, GitHub, now playing, volume slider, media keys. Add, remove, reorder, resize, rename, per-widget settings, multiple pages. |
| Deck | Touch buttons in groups: media and volume keys, lock, launch apps, open folders and URLs, run approved commands, sleep / restart / shut down with confirmation. |
| Dev | Your GitHub pull requests with check and review state, review requests and recent activity. Git branch and status of your local projects, recent commits, listening dev-server ports, running dev tools. |
| AI | Claude Code and Codex sessions, with an alert when one finishes or needs your input. Claude plan usage. Allow or deny Claude Code permission prompts from the phone. |
| Focus | Tasks with daily priorities, notes, pomodoro, stopwatch, countdown. |

## What you need

- A Windows 11 PC. Windows 10 should work but has not been tested.
- [Python 3.13](https://www.python.org/downloads/): `winget install Python.Python.3.13`
- An iPhone on the same Wi-Fi network. The panel is a web page, so other phones and tablets with a modern browser should work too, but only the iPhone is targeted.
- Optional:
  - The [GitHub CLI](https://cli.github.com/), signed in with `gh auth login`, for the GitHub widgets.
  - [Claude Code](https://code.claude.com/) or Codex for the AI screen.
  - An NVIDIA graphics card for the GPU widget.

## Quick start

On the PC, in PowerShell:

```powershell
git clone https://github.com/moh05-a/phoney.git
cd phoney
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1     # once: creates the environment and installs
powershell -ExecutionPolicy Bypass -File scripts\start.ps1     # starts the agent
```

(No Git? Download the ZIP from the green **Code** button on GitHub and unzip it.)

Then:

1. Open **http://127.0.0.1:7421** on the PC and choose **Pair a phone**.
2. Scan the QR code with the iPhone Camera and open the link in Safari.
3. Tap **Share → Add to Home Screen**, then open **Phoney** from the Home Screen.
4. Type the pairing code shown on the PC.

If the phone cannot load the page:
- Allow Python through Windows Firewall for **Private** networks.
- Make sure your Wi-Fi is marked as a Private network in Windows.

To keep the phone's screen on, set **Settings → Display & Brightness → Auto-Lock → Never** on the iPhone.

The agent shows an icon in the system tray (near the clock): right-click it to open the status page, pair a phone, or quit.

To start the agent automatically, without a window, when you sign in: `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1`

## Security in one paragraph

Use Phoney only on a network you trust, such as your home Wi-Fi.
- Only paired phones can read anything or press anything.
- The phone can only trigger actions listed in a file on the PC.
- Sleep, restart and shut down are off until you turn them on.
- The agent refuses connections from outside your local network.

The connection between phone and PC is plain HTTP, not encrypted, so someone already on your network could watch it. Never forward its port to the internet. Details and known limits are in [docs/SECURITY.md](docs/SECURITY.md).

## Making it yours

Everything is in `%APPDATA%\Phoney\` (created on first start). Restart the agent after editing.

- `actions.toml`: the apps, folders, URLs and commands the phone is allowed to trigger. The phone can arrange buttons but can never add a command; only this file can.
- `config.toml`: PC name, ports, your git projects, which integrations are on, and `allow_power_actions` for sleep / restart / shut down.

Claude Code (sessions, plan usage, permission prompts) and Codex are connected with one command each; see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Documents

| Document | Contents |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Scope, limitations, decisions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, modules, technical decisions |
| [docs/API.md](docs/API.md) | REST and WebSocket protocol, version 1.0 |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, controls, known limits, how to report a vulnerability |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | How the Claude Code, GitHub and Codex integrations work |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Setup, tests, how to add a widget or an action kind |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What is done, what was verified, what is open |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to report bugs and send changes |

## Credits

Inspired by the ideas in [PhoneBox](https://github.com/ersinkoc/phonebox), an Android panel by ersinkoc. Phoney is a separate project written from scratch for the iPhone and uses none of its code.

## License

[MIT](LICENSE)
