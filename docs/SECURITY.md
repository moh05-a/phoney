# Security

Last updated 2026-11-16. Reflects what was built, including what it does not protect against.

## 1. What is protected

The agent can launch programs, run approved commands and (if enabled) shut down the PC, and it reveals what is running on the machine. The goal: only a phone that the owner paired, while sitting at the PC, can do or see any of that.

## 2. Threat model

| Threat | Covered? |
|---|---|
| Another device on the same Wi-Fi trying the API | Yes: everything needs a per-device token obtained with a one-time code shown only on the PC |
| Guessing pairing codes or tokens | Yes: rate limit, lockout, 256-bit tokens |
| A stolen token being used to run arbitrary commands | Yes: a token can only trigger actions already on the PC's allowlist |
| Malicious input to the API | Yes: strict validation, no shell, size limits |
| A web page in the PC's browser attacking the local admin page | Yes: Host and Origin checks on the local listener |
| Accidental exposure beyond the home network | Yes: non-private source addresses are refused; no port forwarding or UPnP |
| **Someone on the same network intercepting traffic** | **No. See section 4.** |
| Malware already running as the owner on the PC, or physical access to the unlocked PC | Out of scope: it can already do everything the agent can |

## 3. Controls

**Pairing.** Started only from the PC (the pairing page is on the loopback listener). Codes are 8 characters from an unambiguous 31-letter alphabet, single-use, valid 10 minutes. `POST /pair` allows 5 attempts per minute per address, and 10 consecutive failures lock that address out for 5 minutes.

**Tokens.** 32 random bytes from `secrets`, prefixed `pbx_`. The agent stores only the SHA-256 hash and compares in constant time. One token per device; revoking a device (from the PC's status page, or "Unpair" on the phone) closes its live socket immediately.

**Actions.** The phone names an action ID and nothing else. Programs, arguments, paths and URLs live in `actions.toml` on the PC and are never accepted from or sent to the network. Commands run through `subprocess` with an argument list, never a shell, with a timeout. URLs must be `http` or `https`. Unknown keys in `actions.toml` are an error, not ignored.

**Parameters.** Only built-in actions accept a value from the phone, and only as declared: `volume.set` takes one whole number from 0 to 100. Wrong names, extra names, wrong types and out-of-range values are refused. Actions in `actions.toml` cannot take parameters at all.

**Answering Claude Code permission prompts.** With `claude_permissions = true` and a panel that has "Answer on this phone" switched on, a paired device can allow or deny what Claude Code is asking to do. This is a real grant of authority: an allowed Bash command runs on the PC. Limits: only a prompt Claude Code itself raised can be answered, once, by a paired device; Claude Code still applies its own deny rules after an allow; every answer is in the audit log; and with the plain-HTTP limit in section 4, someone who stole the device token on your network could answer prompts too. Turn it off with `claude_permissions = false`, or per phone in the AI tab.

**Confirmation.** `run_command` actions and power actions are dangerous by default. A dangerous action runs only with a server-issued token that is valid 30 seconds, single-use, and bound to the device and the action. The check is in the agent; calling the API directly cannot skip it. Sleep, restart and shut down do not exist at all unless `allow_power_actions = true`.

**Rate limits** (in memory, per device): 300 requests per minute, 60 actions per minute, 3 confirmed dangerous actions per minute.

**Network.** The LAN listener refuses any client whose address is not loopback, RFC 1918, link-local or unique-local. This runs before routing, so it also covers the panel files and WebSocket upgrades. The local listener binds `127.0.0.1` and additionally rejects foreign `Host` and `Origin` headers, which stops DNS-rebinding and cross-site requests from a web page.

**Input.** Every body is a Pydantic model that forbids unknown fields and bounds every string and number. Bodies over 64 KB and WebSocket messages over 16 KB are refused.

**Panel.** Agent data is only ever inserted as text nodes, never as HTML. A Content-Security-Policy restricts scripts to the agent's own origin. No third-party code is loaded.

**Audit.** The `audit_log` table and `logs\agent.log` record pairing attempts, connections and disconnections with source address, every action run with device and result, revocations and rejected sources. Tokens, codes and hook payloads are never logged. The status page shows the latest 25 entries.

**Privacy.** No telemetry and no outbound connections. A permission prompt shows the tool name and one line of its input (the command or file path) on the phone while it is pending; it is held in memory and never written to disk. GitHub data is read through your own `gh` login and is not stored. The Claude usage forwarder sends only the two rate-limit windows; the agent keeps percent used and reset time. AI hook events are reduced to tool, project folder name, state and time; prompt text and transcripts are dropped on arrival (asserted by a test). Process lists, git and port monitoring can each be turned off.

**Secrets.** None in the repository. Config, database and logs live in `%APPDATA%\Phoney\`, and `.gitignore` excludes them.

## 4. Known limit: traffic is not encrypted by the agent

The panel and API use plain HTTP and WS on the local network.

Why: the client is a web page. Safari refuses WebSocket connections to a server with an untrusted certificate, and a Home Screen web app has no way to click through a certificate warning. HTTPS would therefore require generating a certificate authority and installing and trusting it by hand in iOS Settings. That procedure could not be tested without the phone, and shipping an untested security procedure seemed worse than stating the limit.

What this means: your Wi-Fi's own encryption (WPA2/WPA3) still protects the traffic from outsiders. A device that is already **on** your network and actively intercepts traffic (for example by ARP spoofing) could read the device token and then use it. It could not exceed the allowlist, and dangerous actions would still need the confirmation round-trip, which such an attacker could also perform.

What to do about it:

- Use it on a home network you trust; do not run the agent on public or shared Wi-Fi. Windows should have the network marked **Private**, and the firewall rule should apply to Private only.
- Keep `allow_power_actions = false` and avoid powerful `run_command` entries if other people's devices share the network.
- Revoke and re-pair if you suspect anything; a revoked token is useless.

Adding HTTPS with a locally trusted certificate is the first item under open work in ROADMAP.md.

## 5. Other residual risks

| Risk | Handling |
|---|---|
| The token sits in the browser's storage on the phone, not in the Keychain | Anyone with the unlocked phone has the panel anyway. Revoke from the PC if the phone is lost. |
| The PC's IP address changes | The Home Screen app stops loading. Give the PC a DHCP reservation in the router, or re-add the panel at the new address and pair again. |
| Other programs on the PC can post fake hook events | They only change what the AI dashboard displays; they cannot trigger actions. |
| A dangerous command is added to `actions.toml` with `danger = false` | The owner's choice; the default for `run_command` is dangerous. |
| Dependency vulnerabilities | Small dependency set; run `pip-audit` when updating. |

## 6. Security tests

`tests/test_security.py` and `tests/test_actions.py` cover: every protected endpoint rejects missing and wrong tokens; tokens are stored only as hashes; pairing codes are single-use and expire; rate limit and lockout; unknown fields and oversized bodies are rejected; public source addresses are refused for HTTP, static files and WebSocket; the LAN app has no local routes; unpairing kills the token and the socket; unknown action IDs run nothing; dangerous actions need a valid, matching, unused confirmation; buttons cannot reference actions outside the allowlist; the action list never contains command lines; the local listener refuses foreign Host and Origin.

## 7. Reporting a vulnerability

Please do not open a public issue for a security problem. Use **Report a vulnerability** on the repository's **Security** tab on GitHub, which reaches the maintainer privately. Include what an attacker needs (for example "on the same Wi-Fi, without a paired device"), the steps, and the version from the agent's status page. Expect an answer within two weeks; this is a hobby project with one maintainer.

Supported versions: only the latest release.
