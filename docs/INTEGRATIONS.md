# AI tool integrations

The AI dashboard shows which coding sessions are running and tells you when one finishes or needs your input. It works through the **hook** systems that Claude Code and Codex document officially. The agent never reads transcripts, never talks to any vendor API, and never controls the tools.

## How it works

```
Claude Code / Codex ──hook event (JSON)──► http://127.0.0.1:7421/local/hooks/<tool> ──► agent ──WebSocket──► phone
```

1. The coding tool fires a hook at certain moments (session start, prompt submitted, turn finished, permission needed, session end).
2. The hook sends the event to the agent's local listener, which is reachable only from the PC itself.
3. The agent keeps: tool, project folder name, session state, timestamps. Everything else in the event (prompt text, last message, transcript path, tool inputs) is discarded on arrival.
4. The phone gets the session list and a short event; the panel shows a toast and plays a chime for "finished" and "needs input".

Sessions are kept in memory only. Restarting the agent clears the list until the next event arrives.

| Hook event | Session state on the phone | Alert |
|---|---|---|
| `SessionStart` | Ready | |
| `UserPromptSubmit` | Working | |
| `Stop` | Ready | "finished" |
| `Notification` (permission or idle prompt), `PermissionRequest` | Needs you | "needs your input" |
| `SessionEnd` | removed | |

The endpoint always answers `200 {}`, so it can never block, slow down, or steer the coding tool.

## Claude Code

Reference: [Claude Code hooks](https://code.claude.com/docs/en/hooks). Claude Code supports hooks of type `http`, which post the event JSON to a URL.

```powershell
cd desktop\agent
python -m app hooks show claude-code       # prints exactly what would be added, changes nothing
python -m app hooks install claude-code    # shows the change, asks, then writes
python -m app hooks uninstall claude-code
```

(Use the environment's Python: `%USERPROFILE%\.venvs\phoney\Scripts\python.exe`.)

`install` merges entries like this into `~/.claude/settings.json` for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop` and `Notification`:

```json
{ "hooks": { "Stop": [ { "hooks": [ { "type": "http", "url": "http://127.0.0.1:7421/local/hooks/claude-code", "timeout": 5 } ] } ] } }
```

Your existing hooks are left untouched, the previous file is saved as `settings.json.phoney-backup`, and `uninstall` removes only Phoney's entries. If the agent is not running, Claude Code treats the failed post as a non-blocking error and carries on.

## Claude Code usage (5-hour and weekly limits)

Reference: [Claude Code status line](https://code.claude.com/docs/en/statusline). Claude Code passes session data, including `rate_limits`, to a status-line command. That is the only documented place these numbers appear, and only for subscription plans, after the first reply in a session.

```powershell
python -m app hooks show claude-usage       # prints the setting, changes nothing
python -m app hooks install claude-usage    # shows the change, asks, then writes
python -m app hooks uninstall claude-usage
```

`install` sets `statusLine` in `~/.claude/settings.json` to run `app/integrations/statusline_forward.py`. That script posts the two rate-limit windows to the agent and prints a short status line (`Model | ctx 12% | 5h 37%`), so your terminal gains a status line too. The agent keeps only percent used and reset time per window.

If you already have a status line configured, `install` refuses to replace it and prints the forwarder command so you can call it from your own script.

The panel shows this in the **Claude usage** widget (Add widget in edit mode).

## Answering Claude Code permission prompts from the phone

Reference: the `PermissionRequest` event in [Claude Code hooks](https://code.claude.com/docs/en/hooks). The hook runs when Claude Code is about to ask "allow this?". A hook may answer `allow` or `deny`; if it returns nothing, Claude Code shows its normal prompt.

```powershell
python -m app hooks show claude-permissions
python -m app hooks install claude-permissions
python -m app hooks uninstall claude-permissions
```

How a prompt travels:

1. Claude Code posts the request to `http://127.0.0.1:7421/local/hooks/claude-code/permission`.
2. If no panel is listening (none open, or "Terminal only" chosen in the AI tab), the agent answers immediately with no decision and the terminal asks as usual. No delay.
3. Otherwise a card appears on the phone, over whatever screen is showing, with the tool name and the command or file path, and a countdown.
4. **Allow** or **Deny** is returned to Claude Code. If nobody answers within `permission_wait_s` (30 by default), the agent returns no decision and the terminal asks.

What to expect: while a panel is listening, the terminal prompt appears only after the phone answers or the wait runs out. Lower `permission_wait_s` in `config.toml` if that feels slow.

Verified: the whole path with a simulated hook call and a real browser panel (card shown, Allow tapped, the documented JSON returned). Not yet seen with a real Claude Code prompt.

## GitHub

Uses the official GitHub CLI. Install it (`winget install GitHub.cli`) and run `gh auth login` once; the agent then runs `gh api` every two minutes while a panel is showing GitHub data. It reads your open pull requests with check and review state, pull requests waiting for your review, recently pushed repositories and your recent activity. It never writes to GitHub and never sees your token.

If `gh` is missing or logged out, the GitHub widget says so and everything else works.

## Codex

Reference: [Codex hooks](https://learn.chatgpt.com/docs/hooks). Codex hooks run a command and pass the event JSON on standard input, so the installed command is a tiny forwarder (`app/integrations/hook_forward.py`) that posts the event to the agent and exits successfully no matter what.

```powershell
python -m app hooks install codex
```

This writes `~/.codex/hooks.json` with entries for `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop` and `PermissionRequest`. Codex only runs hooks you have reviewed: start Codex and run `/hooks` once to trust them.

## What was and was not verified

- Verified by automated tests: event intake, state transitions, that no prompt or transcript content is stored, delivery to a subscribed phone, and that install followed by uninstall restores the settings file exactly.
- **Verified with real Claude Code (2026-11-16):** session hooks installed on this PC and events from a live session arrived (prompt, working, finished).
- **Not yet verified:** Codex hooks with the real tool, and the usage numbers with a live terminal session (the status line does not run inside the VS Code extension). The entry format follows the official documentation; the first real run is the confirmation. If Codex rejects the file, `python -m app hooks uninstall codex` restores it.

## Not included

- **Codex quota.** No official local interface was found.
- **GitHub Copilot.** No documented local interface for session state.

## Turning an integration off

Set `claude_code = false` or `codex = false` under `[integrations]` in `config.toml`. The endpoint then ignores that tool's events, and the rest of the agent is unaffected.
