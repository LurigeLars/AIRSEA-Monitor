"""Offline dependency-manifest and loopback WebSocket regression checks."""
from __future__ import annotations

import asyncio
import re
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


if __name__ == "__main__":
    unittest.main()
