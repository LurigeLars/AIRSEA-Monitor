"""14-day shadow-only acquisition. Never produces trade signals or notifications.

Secrets are supplied by a parent PowerShell process through a transient environment variable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sqlite3
import ssl
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from opensky_oauth import OpenSkyOAuth

from pilot_core import (
    AIS_BOX, FLIGHT_CENTER, FLIGHT_RADIUS_NM, OPENSKY_BOUNDS,
    adsb_count, inc, iso_now, log_error, open_db, opensky_count,
    process_ais, prune_temp, record_flight_count, started_at, utcnow,
)

try:
    from websockets.asyncio.client import connect
except ImportError:
    print("Dependency missing: websockets. Run via uv --with websockets.", file=sys.stderr)
    raise SystemExit(2)

AIS_URL = "wss://stream.aisstream.io/v0/stream"
ADSB_URL = f"https://api.adsb.lol/v2/lat/{FLIGHT_CENTER[0]}/lon/{FLIGHT_CENTER[1]}/dist/{FLIGHT_RADIUS_NM}"
OS_URL = "https://opensky-network.org/api/states/all?" + urlencode({
    "lamin": OPENSKY_BOUNDS[0], "lomin": OPENSKY_BOUNDS[1],
    "lamax": OPENSKY_BOUNDS[2], "lomax": OPENSKY_BOUNDS[3],
})
USER_AGENT = "market-observation-shadow-pilot/0.1 (personal, non-production; 48 requests/day max ADSB)"


def fetch_json(url: str, *, bearer_token: str | None = None) -> dict:
    # Fixed allowlisted endpoints only; never construct a URL with a secret.
    if url not in {ADSB_URL, OS_URL}:
        raise ValueError("Non-allowlisted provider URL")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if bearer_token is not None:
        if url != OS_URL:
            raise ValueError("OAuth token only permitted for OpenSky")
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = Request(url, headers=headers)
    with urlopen(req, timeout=15, context=ssl.create_default_context()) as resp:
        if resp.status != 200:
            raise RuntimeError("HTTP error")
        raw = resp.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Oversize API response")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("Unexpected JSON structure")
        return obj


async def ais_stream(conn: sqlite3.Connection, salt: bytes, stop: asyncio.Event):
    token = os.environ.get("AISSTREAM_API_KEY", "")
    if len(token) < 20:
        raise RuntimeError("AISStream key absent/invalid; use run.ps1; do not paste keys into CLI")
    delay = 5
    while not stop.is_set():
        opened = None
        message_count = 0
        try:
            async with connect(AIS_URL, open_timeout=15, compression="deflate", max_size=2_000_000, ping_interval=30, ping_timeout=30) as ws:
                subscription = {
                    "APIKey": token,
                    "BoundingBoxes": AIS_BOX,
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData", "StandardClassBPositionReport", "ExtendedClassBPositionReport", "StaticDataReport"],
                }
                await ws.send(json.dumps(subscription, separators=(",", ":")))
                # Fail closed until provider has acknowledged our subscription.
                confirmation = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if confirmation.get("MessageType") != "SubscriptionConfirmation":
                    raise RuntimeError("AIS subscription was not confirmed")
                opened = time.monotonic()
                accrued_until = opened
                print(f"{iso_now()} AIS subscription confirmed (aggregates only)", flush=True)
                delay = 5
                while not stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        # Periodic connected-time accrual even when upstream has no messages.
                        now_mono = time.monotonic()
                        inc(conn, "ais_connected_seconds", max(0, now_mono-accrued_until))
                        accrued_until = now_mono
                        conn.commit()
                        continue
                    now_mono = time.monotonic()
                    if now_mono - accrued_until >= 60:
                        inc(conn, "ais_connected_seconds", max(0, now_mono-accrued_until))
                        accrued_until = now_mono
                        conn.commit()
                    payload = json.loads(msg)
                    if not isinstance(payload, dict):
                        continue
                    if process_ais(conn, payload, salt):
                        message_count += 1
                    if message_count % 500 == 0 and message_count:
                        conn.commit()
                        prune_temp(conn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(conn, "ais", exc)
            print(f"{iso_now()} AIS reconnect required: {type(exc).__name__}", flush=True)
        finally:
            if opened is not None:
                elapsed = max(0, time.monotonic() - accrued_until)
                inc(conn, "ais_connected_seconds", elapsed)
                conn.commit()
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except asyncio.TimeoutError:
            delay = min(delay * 2, 300)


async def flight_poll(conn: sqlite3.Connection, stop: asyncio.Event, provider: str, interval: int, auth: OpenSkyOAuth | None = None):
    url = ADSB_URL if provider == "adsb" else OS_URL
    # Initial stagger reduces simultaneous wakeups.
    if provider == "opensky":
        try:
            await asyncio.wait_for(stop.wait(), timeout=90)
            return
        except asyncio.TimeoutError:
            pass
    while not stop.is_set():
        began = time.monotonic()
        try:
            if provider == "opensky" and auth is not None:
                payload = await asyncio.to_thread(auth.fetch_states, fetch_json, url)
            else:
                payload = await asyncio.to_thread(fetch_json, url)
            count = adsb_count(payload) if provider == "adsb" else opensky_count(payload)
            record_flight_count(conn, provider, count)
            print(f"{iso_now()} {provider} snapshot ok (count withheld from console)", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(conn, provider, exc)
            print(f"{iso_now()} {provider} unavailable: {type(exc).__name__}", flush=True)
        conn.commit()
        remaining = max(1, interval - (time.monotonic() - began))
        try:
            await asyncio.wait_for(stop.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            continue


async def run(db: Path, days: int):
    if not (1 <= days <= 14):
        raise ValueError("pilot duration must be 1..14 days")
    salt_path = db.parent / "salt.bin"
    if not salt_path.exists():
        # Salt is local and excludes raw MMSIs from persisted observations.
        salt_path.write_bytes(os.urandom(32))
    salt = salt_path.read_bytes()
    if len(salt) != 32:
        raise ValueError("invalid local salt")
    conn = open_db(db)
    deadline = started_at(conn) + timedelta(days=days)
    if utcnow() >= deadline:
        print("Pilot already completed. No collector started.")
        conn.close()
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: cancellation will be handled by task termination.
    print(f"{iso_now()} Shadow pilot started; no alerts/orders/Trade Spine writes. End {deadline.isoformat()}", flush=True)
    opensky_auth = OpenSkyOAuth.from_environment()
    print(f"{iso_now()} OpenSky mode: {'authenticated' if opensky_auth else 'anonymous'}; no credentials logged", flush=True)
    workers = [
        asyncio.create_task(ais_stream(conn, salt, stop), name="AISStream"),
        asyncio.create_task(flight_poll(conn, stop, "adsb", interval=1800), name="ADSB-lol"),
        asyncio.create_task(flight_poll(conn, stop, "opensky", interval=1800 if opensky_auth else 86400, auth=opensky_auth), name="OpenSky"),
    ]
    try:
        remaining = max(0, (deadline - utcnow()).total_seconds())
        try:
            await asyncio.wait_for(stop.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            pass
    finally:
        stop.set()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        conn.commit()
        conn.close()
    print(f"{iso_now()} Shadow pilot stopped", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    if not os.getenv("AISSTREAM_API_KEY"):
        raise SystemExit("Missing AISSTREAM_API_KEY; start via run.ps1")
    asyncio.run(run(args.db, args.days))
