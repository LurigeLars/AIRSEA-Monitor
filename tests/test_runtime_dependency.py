"""Offline dependency-manifest and loopback WebSocket regression checks."""
from __future__ import annotations

import asyncio
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "App" / "requirements.txt"
RUNNER = ROOT / "App" / "run.ps1"


class RuntimeManifestTests(unittest.TestCase):
    def test_manifest_is_exactly_pinned_and_consumed_by_runner(self):
        requirements = [
            line.strip()
            for line in MANIFEST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(len(requirements), 1)
        self.assertRegex(requirements[0], r"^websockets==[0-9]+(?:\.[0-9]+)+$")
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("Join-Path $PSScriptRoot 'requirements.txt'", source)
        self.assertIn("--with-requirements $requirementsPath", source)
        self.assertNotIn("--with websockets python", source)

    def test_websocket_client_echo_on_loopback(self):
        try:
            from websockets.asyncio.client import connect
            from websockets.asyncio.server import serve
        except ImportError:
            self.skipTest("websockets not installed; dedicated uv CI lane covers this")

        async def echo(socket):
            await socket.send(await socket.recv())

        async def exercise():
            async with serve(echo, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                async with connect(
                    f"ws://127.0.0.1:{port}",
                    open_timeout=15,
                    compression="deflate",
                    max_size=2_000_000,
                    ping_interval=30,
                    ping_timeout=30,
                ) as socket:
                    await socket.send("airsea-runtime-smoke")
                    self.assertEqual(await socket.recv(), "airsea-runtime-smoke")
        asyncio.run(exercise())


class CollectorLifecycleTests(unittest.TestCase):
    def test_opensky_stagger_does_not_stop_the_pilot(self):
        """Regression: the 90 s OpenSky stagger once set the shared stop event."""
        try:
            import websockets  # noqa: F401  collector exits without it
        except ImportError:
            self.skipTest("websockets not installed; dedicated uv CI lane covers this")
        import sqlite3
        import sys
        from unittest import mock

        sys.path.insert(0, str(ROOT / "App"))
        import collector

        real_wait_for = asyncio.wait_for

        async def short_wait_for(awaitable, timeout=None):
            return await real_wait_for(awaitable, timeout=0.01 if timeout and timeout >= 60 else timeout)

        async def exercise():
            stop = asyncio.Event()
            with mock.patch.object(collector.asyncio, "wait_for", short_wait_for), \
                    mock.patch.object(collector, "fetch_json", lambda url, **kw: {"states": []}), \
                    mock.patch.object(collector, "record_flight_count"), \
                    mock.patch.object(collector, "log_error"):
                task = asyncio.create_task(
                    collector.flight_poll(sqlite3.connect(":memory:"), stop, "opensky", interval=1800)
                )
                await asyncio.sleep(0.2)
                stopped = stop.is_set()
                stop.set()
                await task
            return stopped

        self.assertFalse(asyncio.run(exercise()), "OpenSky stagger must not set the shared stop event")


if __name__ == "__main__":
    unittest.main()
