#!/usr/bin/env python
"""Smoke test the Lovense Hush through Intiface Central.

Usage: uv run python scripts/test_hush.py [--url ws://127.0.0.1:12345] [--match Hush]

Buzzes SOS in morse, then the count groups 2-6-3 ("knight to f3"), then each
protocol signal. Requires Intiface Central running with the device paired.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from buttplug import ButtplugClient, DeviceOutputCommand, OutputType

from src.lib import patterns


async def play(device, steps):
    level = None
    try:
        for lv, ms in steps:
            if lv != level:
                await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, float(lv)))
                level = lv
            await asyncio.sleep(ms / 1000)
    finally:
        await device.stop()


async def find_device(client, match):
    def pick():
        for d in client.devices.values():
            if match.lower() in d.name.lower() and d.has_output(OutputType.VIBRATE):
                return d
        return None

    device = pick()
    if device is None:
        print(f"scanning for a device matching {match!r} ...")
        await client.start_scanning()
        for _ in range(30):
            await asyncio.sleep(1)
            if pick():
                break
        await client.stop_scanning()
        device = pick()
    return device


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:12345")
    parser.add_argument("--match", default="Hush")
    parser.add_argument("--intensity", type=float, default=0.7)
    args = parser.parse_args()

    client = ButtplugClient("chess-playing-test")
    await client.connect(args.url)
    print(f"connected to server: {client.server_name}")

    device = await find_device(client, args.match)
    if device is None:
        print(f"no device matching {args.match!r} found; is it paired in Intiface?")
        await client.disconnect()
        return 1
    print(f"using device: {device.name}")

    timing = patterns.Timing(intensity=args.intensity)

    print("morse SOS ...")
    await play(device, patterns.from_text("SOS", timing))
    await asyncio.sleep(1.5)

    print("count groups 2-6-3 (knight to f3) ...")
    await play(device, patterns.count_groups([2, 6, 3], timing))
    await asyncio.sleep(1.5)

    for name in ("ready", "attention", "ack", "error", "ambiguity", "check"):
        print(f"signal: {name}")
        await play(device, patterns.signal(name, timing))
        await asyncio.sleep(1.5)

    await client.disconnect()
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
