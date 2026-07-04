#!/usr/bin/env python
"""Live pressure stream + squeeze detection from the kGoal Boost over BLE.

Usage: uv run python scripts/test_kgoal.py [--name Boost | --address <mac/uuid>] [--calibrate]

Prints a live pressure bar and classified short/long squeeze events.
--calibrate runs the relax/squeeze capture first; otherwise detection uses
the default assumed span.
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bleak import BleakClient, BleakScanner

from src.lib.squeeze_detect import SqueezeDetector
from src.models.kgoal_sensor import BATTERY_CHAR, PRESSURE_CHAR


class Monitor:
    def __init__(self, detector):
        self.detector = detector
        self.samples = []  # (t, pressure)
        self.pressure = 0

    def on_notify(self, _char, data):
        if len(data) < 7:
            return
        t = time.monotonic()
        self.pressure = (data[3] << 8) | data[4]
        self.samples.append((t, self.pressure))
        event = self.detector.process(self.pressure, t)
        bar = "#" * min(60, self.pressure // 20)
        line = f"\r{self.pressure:5d} |{bar:<60}|"
        if event:
            line = f"\r{event.kind.upper():5s} squeeze  {round((event.t_end - event.t_start) * 1000):4d}ms  peak {event.peak:.0f}\n" + line
        sys.stdout.write(line)
        sys.stdout.flush()

    async def capture(self, seconds):
        start = time.monotonic()
        await asyncio.sleep(seconds)
        window = [p for (t, p) in self.samples if t >= start]
        return statistics.median(window) if window else 0


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Boost")
    parser.add_argument("--address", default="")
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()

    if args.address:
        device = args.address
    else:
        print(f"scanning for {args.name!r} ...")
        device = await BleakScanner.find_device_by_name(args.name, timeout=20)
        if device is None:
            print("device not found. Is it on? (squeeze it to wake it)")
            return 1

    monitor = Monitor(SqueezeDetector())
    async with BleakClient(device) as client:
        print(f"connected: {client.address}")
        try:
            battery = int((await client.read_gatt_char(BATTERY_CHAR))[0])
            print(f"battery: {battery}%")
        except Exception as err:
            print(f"battery read failed: {err}")

        await client.start_notify(PRESSURE_CHAR, monitor.on_notify)

        if args.calibrate:
            print("\nCALIBRATION: relax for 3 seconds ...")
            baseline = await monitor.capture(3)
            print(f"\nbaseline: {baseline:.0f}. Now SQUEEZE HARD for 3 seconds ...")
            peak = await monitor.capture(3)
            print(f"\npeak: {peak:.0f}, span: {peak - baseline:.0f}")
            monitor.detector.set_calibration(baseline, peak)

        print("\nstreaming — squeeze away, ctrl-C to quit\n")
        while True:
            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nbye")
