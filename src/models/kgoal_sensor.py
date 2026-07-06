"""Viam sensor model for the Minna kGoal Boost.

Reads the pressure stream directly over BLE (bleak). The buttplug protocol
implementation (buttplugio/buttplug kgoal_boost.rs) documents the device:
advertises as "Boost"; pressure notifications on characteristic
10c2be2d-d2d5-b7a8-5f42-e2468c9ebbf5 as 7-byte packets with bytes 3-4 =
big-endian u16 normalized pressure (0-2000) and bytes 5-6 = raw. Battery is
standard GATT. Squeeze detection runs on the notification stream and emits
sequence-numbered events fetched via do_command("get_events").
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, ClassVar, Mapping, Optional, Sequence, Tuple

from bleak import BleakClient, BleakScanner
from typing_extensions import Self
from viam.components.sensor import Sensor
from viam.logging import getLogger
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model
from viam.utils import SensorReading, ValueTypes, struct_to_dict

from ..lib.squeeze_detect import SqueezeDetector

LOGGER = getLogger(__name__)

PRESSURE_CHAR = "10c2be2d-d2d5-b7a8-5f42-e2468c9ebbf5"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"


def _percentile(values: list[float], frac: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(frac * len(s)))]


class KgoalBoost(Sensor, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:kgoal-boost"

    def __init__(self, name: str):
        super().__init__(name)
        self.detector = SqueezeDetector()
        self.events: deque = deque(maxlen=256)
        self.samples: deque = deque(maxlen=4096)  # (t, pressure) for capture windows
        self.pressure = 0
        self.raw = 0
        self.battery: Optional[int] = None
        self.connected = False
        self.device_name = "Boost"
        self.device_address = ""
        self.scan_timeout_s = 15.0
        self._task: Optional[asyncio.Task] = None
        self._disconnected: Optional[asyncio.Event] = None

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        self = super().new(config, dependencies)
        self.reconfigure(config, dependencies)
        return self

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        attrs = struct_to_dict(config.attributes)
        for frac in ("on_fraction", "off_fraction", "ema_alpha"):
            if frac in attrs and not 0 < float(attrs[frac]) < 1:
                raise ValueError(f"{frac} must be between 0 and 1")
        return [], []

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        attrs = struct_to_dict(config.attributes)
        self.device_name = str(attrs.get("device_name", "Boost"))
        self.device_address = str(attrs.get("device_address", ""))
        self.scan_timeout_s = float(attrs.get("scan_timeout_s", 15.0))

        old = self.detector
        self.detector = SqueezeDetector(
            long_press_ms=int(attrs.get("long_press_ms", 1000)),
            min_press_ms=int(attrs.get("min_press_ms", 80)),
            on_fraction=float(attrs.get("on_fraction", 0.35)),
            off_fraction=float(attrs.get("off_fraction", 0.20)),
            ema_alpha=float(attrs.get("ema_alpha", 0.02)),
        )
        if old.calibrated:
            self.detector.set_calibration(old.baseline, old.baseline + old.span)
        self.detector.seq = old.seq

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    # ---- BLE ----

    def _on_disconnect(self, _client: BleakClient) -> None:
        if self._disconnected is not None:
            self._disconnected.set()

    def _on_notify(self, _char, data: bytearray) -> None:
        if len(data) < 7:
            return
        t = time.monotonic()
        self.pressure = (data[3] << 8) | data[4]
        self.raw = (data[5] << 8) | data[6]
        self.samples.append((t, self.pressure))
        event = self.detector.process(self.pressure, t)
        if event is not None:
            LOGGER.debug("squeeze: %s", event)
            self.events.append(event)

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            try:
                if self.device_address:
                    device: Any = self.device_address
                else:
                    device = await BleakScanner.find_device_by_name(self.device_name, timeout=self.scan_timeout_s)
                    if device is None:
                        raise RuntimeError(f"no BLE device named {self.device_name!r} found")

                self._disconnected = asyncio.Event()
                async with BleakClient(device, disconnected_callback=self._on_disconnect) as client:
                    LOGGER.info("connected to kGoal %s", client.address)
                    self.connected = True
                    backoff = 1.0
                    try:
                        self.battery = int((await client.read_gatt_char(BATTERY_CHAR))[0])
                    except Exception:
                        LOGGER.debug("battery read failed", exc_info=True)
                    await client.start_notify(PRESSURE_CHAR, self._on_notify)
                    await self._disconnected.wait()
                LOGGER.warning("kGoal disconnected")
            except asyncio.CancelledError:
                raise
            except Exception as err:
                LOGGER.warning("kGoal connection failed (%s); retrying in %.0fs", err, backoff)
            finally:
                self.connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    # ---- Viam API ----

    async def get_readings(self, *, extra=None, timeout=None, **kwargs) -> Mapping[str, SensorReading]:
        return {
            "pressure": self.pressure,
            "raw": self.raw,
            "baseline": self.detector.baseline or 0.0,
            "on_threshold": self.detector.on_threshold,
            "off_threshold": self.detector.off_threshold,
            "calibrated": self.detector.calibrated,
            "squeezing": self.detector.pressed,
            "connected": self.connected,
            "battery": -1 if self.battery is None else self.battery,
            "last_event_seq": self.detector.seq,
        }

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        cmd = command.get("command")

        if cmd == "get_events":
            since = int(command.get("since_seq", 0))
            events = [e.to_dict() for e in self.events if e.seq > since]
            return {"events": events, "last_seq": self.detector.seq}

        if cmd == "capture":
            seconds = float(command.get("seconds", 3.0))
            if not self.connected:
                return {"error": "kGoal not connected"}
            start = time.monotonic()
            await asyncio.sleep(seconds)
            window = [p for (t, p) in self.samples if t >= start]
            if not window:
                return {"error": "no pressure samples received during capture"}
            return {
                "median": _percentile(window, 0.5),
                "p95": _percentile(window, 0.95),
                "max": max(window),
                "n": len(window),
            }

        if cmd == "set_calibration":
            self.detector.set_calibration(float(command["baseline"]), float(command["peak"]))
            return {"ok": True, "on_threshold": self.detector.on_threshold, "off_threshold": self.detector.off_threshold}

        if cmd == "clear_events":
            self.events.clear()
            return {"ok": True}

        if cmd == "status":
            readings = await self.get_readings()
            return dict(readings)

        return {"error": f"unknown command {cmd!r}"}

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await super().close()
