# Communication Protocol

Protocol version **1.0**. Last updated 2026-11-16. This is the contract between a client (the phone panel) and the agent.

## 1. Conventions

- LAN base URL: `http://<pc-address>:7420/api/v1`. WebSocket: `ws://<pc-address>:7420/api/v1/ws`. The panel itself is served from `/`.
- Local base URL (PC only): `http://127.0.0.1:7421`.
- JSON, UTF-8, `snake_case`. Timestamps are ISO 8601 UTC (`2026-11-16T09:30:00.123Z`). Sizes are bytes, rates are bytes per second, percentages are 0–100.
- A value the machine cannot provide is `null`. Clients treat every metric field as nullable.
- Request bodies are limited to 64 KB, WebSocket messages to 16 KB. Unknown fields in a request body are rejected.

### Versioning

- The major version is in the URL (`/api/v1`). A breaking change creates `/api/v2`.
- Within a major version changes are additive. Clients ignore unknown fields, message types and topics.
- The agent reports `protocol_version` in `GET /health` and in the WebSocket `hello`. A client may send its own version in the `auth` message; a different major version closes the socket with code 4426.

### Authentication

`Authorization: Bearer <device_token>` on every API request except `GET /health` and `POST /pair`. The WebSocket authenticates with its first message (browsers cannot set headers on a WebSocket).

### Errors

```json
{ "error": { "code": "confirmation_required", "message": "Confirm: Shut down?", "details": { "confirm_token": "…", "expires_in_s": 30 } } }
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `invalid_request` | Validation failed; `details.problems` lists fields |
| 401 | `unauthorized` | Missing, unknown or revoked token |
| 403 | `forbidden` | Client is not on a private network |
| 404 | `not_found` | |
| 409 | `confirmation_required` | Dangerous action; repeat with `details.confirm_token` |
| 410 | `pairing_code_expired` | Code wrong, used or expired |
| 413 | `invalid_request` | Body too large |
| 422 | `action_failed` | The OS refused; `details.reason` (for example `program_not_found`, `timeout`) |
| 429 | `rate_limited` | `Retry-After` header set |
| 501 | `unsupported` | Integration turned off on this PC |

## 2. Pairing and device

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | `{ status, name, agent_version, protocol_version, panel_build }` |
| `POST /pair` | none | Exchange a pairing code for a device token |
| `GET /agent` | token | Agent info, capabilities, and the calling device |
| `DELETE /devices/me` | token | Unpair the calling device; its socket closes with 4401 |

The pairing page on the PC shows a QR code containing `http://<pc-address>:7420/#pair=<CODE>` and the same code as text (`XXXX-XXXX`). Codes are single-use and expire after 10 minutes.

```json
POST /pair  { "pairing_code": "K7M2-Q9XD", "device_name": "iPhone", "device_model": "iPhone" }
201         { "device_id": "d_1a2b…", "token": "pbx_…", "agent": { "name": "Desk PC", "agent_version": "0.1.0", "protocol_version": "1.0" } }
```

The token is returned once. Dashes, spaces and letter case in the code are ignored.

```json
GET /agent
{ "name": "Desk PC", "agent_version": "0.1.0", "protocol_version": "1.0",
  "capabilities": { "topics": ["system.cpu", "system.memory", "…"],
                    "unsupported_topics": { "system.gpu": "no_nvidia_gpu" },
                    "features": ["actions", "deck", "productivity", "git", "ports", "claude_code", "codex"] },
  "device": { "id": "d_1a2b…", "name": "iPhone" } }
```

## 3. System

| Method and path | Purpose |
|---|---|
| `GET /system/snapshot` | One sample of every supported `system.*` topic, keyed by topic name |
| `GET /system/processes` | Same payload as the `system.processes` topic |

## 4. Actions and control deck

| Method and path | Purpose |
|---|---|
| `GET /actions` | The allowlist: `{ actions: [{ id, name, kind, danger, params }] }`. Never command lines or paths. |
| `POST /actions/{id}/run` | Run an action. Body optional: `{ "confirm_token": "…", "params": { … } }` |
| `GET /deck` | `{ groups: [{ id, name, order, buttons: [...] }] }` |
| `POST /deck/groups`, `PATCH`/`DELETE /deck/groups/{id}` | `{ name }`; patch also takes `order`. Deleting a group deletes its buttons. |
| `POST /deck/buttons`, `PATCH`/`DELETE /deck/buttons/{id}` | `{ group_id, label, icon, color, action_id }`; patch also takes `order` |

Action kinds: `media`, `volume`, `lock`, `power` (built in) and `launch_app`, `run_command`, `open_path`, `open_url` (from `actions.toml`).

Parameters: only built-in actions declare any, as `params: [{ name, min, max }]`. Today that is `volume.set` with `level` 0–100 (present when the PC can set an absolute volume). The request must send exactly the declared names with whole numbers in range; anything else is refused with 400. Actions from `actions.toml` can never take parameters.

Run results: `{ "status": "started" }`, or for `run_command` `{ "status": "completed", "exit_code": 0, "output": "last 600 characters" }`.

Dangerous actions: the first call returns `409 confirmation_required` with a token valid for 30 seconds, bound to the device and the action, usable once. The client asks the user, then repeats the call with the token.

A button: `{ id, group_id, label, icon, color, order, action_id, available, danger }`. `available` is false when its action is no longer on the allowlist. `action_id` must be on the allowlist or the request is rejected with 400.

## 5. Layout

`GET /layout` → `{ "layout": <document or null> }`. `PUT /layout` stores a document and returns it. One layout is shared by all paired devices.

```json
{ "schema": 1, "pages": [ { "id": "system", "name": "System", "widgets": [
    { "id": "w1", "type": "cpu", "name": "CPU", "enabled": true,
      "position": { "x": 0, "y": 0 }, "size": { "w": 2, "h": 2 }, "config": {} } ] } ] }
```

Limits: 1–12 pages, up to 40 widgets per page, `x` 0–3, `w` 1–4, `h` 1–6. The agent validates shape and limits but not widget types, so a newer client can store types an older one does not know.

## 6. Developer, AI, productivity

| Method and path | Purpose |
|---|---|
| `GET /dev/projects` | Same payload as topic `dev.projects` |
| `GET /dev/projects/{name}/git` | Status plus `commits: [{ hash, subject, author, when }]` (last 10) |
| `GET /dev/ports` | Same payload as topic `dev.ports` |
| `GET /ai/sessions` | Same payload as topic `ai.sessions` |
| `GET /ai/usage` | Same payload as topic `ai.usage` |
| `GET /ai/permissions` | Same payload as topic `ai.permissions` |
| `POST /ai/permissions/{id}` | `{ "decision": "allow" | "deny" }`. Answers a pending prompt once; 404 if it was already answered or expired |
| `GET /github` | Same payload as topic `github.overview` |
| `GET /media/art` | The current track's cover image (PNG or JPEG); 404 if there is none |
| `GET /ai/events?since=<id>` | `{ events: [...] }` newer than `since`; the agent keeps the last 200 |
| `GET`/`POST /tasks`, `PATCH`/`DELETE /tasks/{id}` | Task: `{ id, title, done, priority, created_at, done_at }` |
| `GET`/`POST /notes`, `PATCH`/`DELETE /notes/{id}` | Note: `{ id, text, updated_at }` |

## 7. WebSocket

Connect to `/api/v1/ws` and send the auth message within 5 seconds.

Client → agent:

| `type` | Fields | Meaning |
|---|---|---|
| `auth` | `token`, `protocol_version` (optional) | Must be the first message |
| `subscribe` | `id`, `topics: ["system.cpu", …]` | Entries may also be objects `{ "topic": "…" }` |
| `unsubscribe` | `id`, `topics` | |
| `ping` | | Agent answers `pong` |

Agent → client:

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `name`, `agent_version`, `protocol_version`, `capabilities`, `server_time` | Sent after successful auth |
| `ack` | `id`, `result` | For subscribe: `{ subscribed: [{ topic, interval_ms }], rejected: [{ topic, code }] }` |
| `data` | `topic`, `ts`, `payload` | A sample, or the current state of a state topic |
| `data` with `history: true` | `topic`, `payload: [ … ]` | Up to 60 earlier samples, sent once on subscribe |
| `event` | `topic`, `ts`, `payload` | A one-off occurrence |
| `pong`, `error` | `error` has `code`, `message`, and `id` when it answers a request | |

```json
→ { "type": "auth", "token": "pbx_…", "protocol_version": "1.0" }
← { "type": "hello", "name": "Desk PC", "protocol_version": "1.0", "capabilities": { … }, "server_time": "…" }
→ { "type": "subscribe", "id": "1", "topics": ["system.cpu", "system.gpu"] }
← { "type": "ack", "id": "1", "result": { "subscribed": [{ "topic": "system.cpu", "interval_ms": 1000 }],
                                           "rejected":   [{ "topic": "system.gpu", "code": "unsupported" }] } }
← { "type": "data", "topic": "system.cpu", "ts": "…", "payload": { "percent": 12.5, "per_core": [10.1, 14.9] } }
```

Rejection codes: `unsupported` (known topic, not available on this PC), `unknown_topic`.

### Liveness and close codes

The client sends `ping` every 15 seconds. The agent closes a socket that has been silent for 45 seconds. Subscriptions do not survive a reconnect.

| Code | Meaning | Client behavior |
|---|---|---|
| 1000, 1001, abnormal | Agent stopped or network dropped | Reconnect with backoff |
| 4401 | Token missing, wrong or revoked; or no auth message in time | Forget the token, show pairing |
| 4403 | Client is not on a private network | |
| 4408 | Idle timeout | Reconnect immediately |
| 4409 | The same device connected again elsewhere | Do not auto-reconnect |
| 4426 | Protocol major version not supported | Ask the user to reload |

### Topics

Sampling intervals are fixed by the agent and reported in the `ack`.

| Topic | Interval | Payload |
|---|---|---|
| `system.cpu` | 1 s | `percent`, `per_core[]`, `freq_mhz`, `logical_cores`, `physical_cores` |
| `system.memory` | 1 s | `total`, `used`, `available`, `percent`, `swap_total`, `swap_used`, `swap_percent` |
| `system.network` | 1 s | `up_bps`, `down_bps`, `total_sent`, `total_recv`, `nics: [{ name, up_bps, down_bps }]` |
| `system.disk_io` | 1 s | `read_bps`, `write_bps` |
| `system.gpu` | 2 s | `gpus: [{ index, name, util_percent, mem_used, mem_total, temp_c, power_w }]` |
| `system.processes` | 5 s | `sort`, `items: [{ pid, name, cpu_percent, mem_bytes }]` (top 15; CPU as a share of the whole machine; memory is the private working set on Windows, as in Task Manager) |
| `system.disk` | 30 s | `volumes: [{ mount, fs, total, used, percent }]` |
| `system.battery` | 30 s | `percent`, `plugged`, `secs_left` |
| `media.state` | 2 s | `volume` (0–100), `muted`, `title`, `artist`, `album`, `app`, `playing`, `position_s`, `duration_s`, `art`. Volume fields or track fields are `null` when that half is unavailable. `art` is an id that changes with the cover; fetch the image from `GET /media/art`. |
| `github.overview` | 120 s | `login`, `prs: [{ repo, number, title, draft, checks, review, updated_at }]`, `review_requests: [{ repo, number, title, author, updated_at }]`, `repos: [{ name, pushed_at, private, branch }]`, `activity: [{ repo, text, at }]`. `checks` is `passing`, `failing`, `running` or `null`; `review` is `approved`, `changes_requested`, `review_required` or `null`. |
| `dev.projects` | 10 s | `projects: [{ id, name, branch, ahead, behind, changed_files, untracked_files, dirty }]` or `{ id, name, error }` |
| `dev.ports` | 10 s | `listeners: [{ port, pid, process }]`, `apps: [{ name, count }]` |
| `ai.sessions` | on change | `sessions: [{ id, tool, project, state, started_at, last_activity_at }]`; `state` is `idle`, `working` or `needs_input`. Current state is sent on subscribe. |
| `ai.usage` | on change | `five_hour`, `seven_day`: each `{ percent, resets_at }` or `null`; `updated_at`. Exact Claude plan usage from the terminal status line. Current state is sent on subscribe. |
| `ai.local_usage` | 60 s | `five_hour`: `{ tokens, messages, started_at, resets_at, peak_tokens }` or `null` when no window is open; `seven_day`: `{ tokens, messages, peak_tokens }` (rolling 7 days); `days`: the last 7 days as `[{ date, tokens }]`, oldest first, local dates; `updated_at`. Counted from Claude Code's session files; `peak_tokens` is the busiest window in the last 35 days, for comparison. Unsupported (`claude_code_not_found`) when there is no `~/.claude/projects`. |
| `ai.permissions` | on change | `requests: [{ id, tool, project, tool_name, summary, created_at, expires_in_s }]`: Claude Code permission prompts waiting for an answer. Current state is sent on subscribe. **Subscribing is what tells the agent a phone is ready to answer**; with no subscriber, prompts go straight to the terminal. |
| `ai.events` | events | `{ id, ts, tool, session_id, project, kind, summary }`; `kind` is `started`, `prompt`, `finished`, `needs_input` or `ended` |
| `sync` | events | `{ what }` where `what` is `deck`, `layout`, `tasks` or `notes`: another client changed it, re-fetch |

## 8. Local listener (`http://127.0.0.1:7421`, PC only)

| Endpoint | Purpose |
|---|---|
| `GET /` | Status page: paired devices, what is being sampled, recent activity |
| `GET /pair` | Creates a pairing code and shows it with a QR code |
| `POST /local/devices/{id}/revoke` | Revoke a device |
| `POST /local/hooks/claude-code`, `POST /local/hooks/codex` | Hook event intake; always answers `200 {}` |
| `POST /local/hooks/claude-code/permission` | Claude Code `PermissionRequest` hook. Held open until a phone answers or `permission_wait_s` passes; answers the decision, or `{}` for "ask in the terminal" |
| `POST /local/statusline` | Claude Code rate-limit numbers from the status-line forwarder; always answers `200 {}` |

Requests whose `Host` header is not `127.0.0.1:7421` or `localhost:7421`, or whose `Origin` is foreign, are refused with 403.
