"""Command-line stand-in for the phone: pairs, connects to the live socket and prints what arrives.

Run from desktop/agent with the agent already running:

    python tools/ws_client.py                      # asks for a pairing code, then streams CPU and memory
    python tools/ws_client.py --topics system.network system.disk
    python tools/ws_client.py --seconds 600 --quiet   # soak test: reports message gaps and agent resource use

The token is kept in the data directory (ws_client_token.txt) so pairing is needed only once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil
import websockets

from app.core.config import default_data_dir, load_config


def pair(base: str, token_file: Path) -> str:
    if token_file.exists():
        return token_file.read_text().strip()
    code = input("Pairing code (open the agent's local page and choose 'Pair a phone'): ")
    body = json.dumps({"pairing_code": code, "device_name": "ws_client"}).encode()
    request = urllib.request.Request(
        f"{base}/api/v1/pair", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            token = json.load(response)["token"]
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Pairing failed: {exc.read().decode()}") from exc
    token_file.write_text(token)
    return token


def agent_process(port: int) -> psutil.Process | None:
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port and conn.pid:
            return psutil.Process(conn.pid)
    return None


async def stream(args: argparse.Namespace) -> None:
    data_dir = default_data_dir()
    port = load_config(data_dir).server.lan_port
    token = pair(f"http://127.0.0.1:{port}", data_dir / "ws_client_token.txt")
    proc = agent_process(port)
    if proc:
        proc.cpu_percent(None)

    counts: dict[str, int] = {}
    last_seen: dict[str, float] = {}
    worst_gap = 0.0
    started = time.monotonic()
    async with websockets.connect(f"ws://127.0.0.1:{port}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token, "protocol_version": "1.0"}))
        hello = json.loads(await ws.recv())
        print(f"connected to {hello['name']} (protocol {hello['protocol_version']})")
        await ws.send(json.dumps({"type": "subscribe", "id": "1", "topics": args.topics}))
        next_ping = started + 15
        while (now := time.monotonic()) - started < args.seconds:
            if now >= next_ping:
                await ws.send(json.dumps({"type": "ping"}))
                next_ping = now + 15
            try:
                message = json.loads(await asyncio.wait_for(ws.recv(), 1))
            except TimeoutError:
                continue
            if message["type"] == "ack":
                print("ack:", json.dumps(message["result"]))
            if message["type"] != "data" or message.get("history"):
                continue
            topic = message["topic"]
            now = time.monotonic()
            if topic in last_seen:
                worst_gap = max(worst_gap, now - last_seen[topic])
            last_seen[topic] = now
            counts[topic] = counts.get(topic, 0) + 1
            if not args.quiet:
                print(f"{message['ts']}  {topic:16} {json.dumps(message['payload'])[:110]}")

    elapsed = time.monotonic() - started
    print(f"\n{elapsed:.0f}s  messages: {counts}  longest gap between samples of one topic: {worst_gap:.2f}s")
    if proc:
        cpu = proc.cpu_percent(None) / (psutil.cpu_count() or 1)
        print(f"agent process: {cpu:.2f}% of total CPU on average, {proc.memory_info().rss / 1e6:.0f} MB RAM")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--topics", nargs="+", default=["system.cpu", "system.memory"])
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--quiet", action="store_true")
    asyncio.run(stream(parser.parse_args()))
