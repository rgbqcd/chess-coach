"""Hardware-free stand-ins for the kGoal sensor and Hush buzzer.

fake-kgoal accepts simulated squeezes via do_command and serves them through
the same get_events interface as the real sensor. fake-buzzer logs every
pattern it is asked to play and keeps a history you can fetch, so the full
chess-coach protocol can be exercised on a machine with no bluetooth at all.
"""

from __future__ import annotations

import time
from collections import deque
from typing import ClassVar, Mapping, Sequence, Tuple

from viam.components.generic import Generic
from viam.components.sensor import Sensor
from viam.logging import getLogger
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model
from viam.utils import SensorReading, ValueTypes

from ..lib.squeeze_detect import SqueezeEvent

LOGGER = getLogger(__name__)


class FakeKgoal(Sensor, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:fake-kgoal"

    def __init__(self, name: str):
        super().__init__(name)
        self.events: deque = deque(maxlen=256)
        self.seq = 0

    async def get_readings(self, *, extra=None, timeout=None, **kwargs) -> Mapping[str, SensorReading]:
        return {
            "pressure": 100,
            "raw": 100,
            "baseline": 100.0,
            "on_threshold": 310.0,
            "off_threshold": 220.0,
            "calibrated": True,
            "squeezing": False,
            "connected": True,
            "battery": 100,
            "last_event_seq": self.seq,
        }

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        cmd = command.get("command")

        if cmd == "simulate_squeeze":
            kind = str(command.get("kind", "short"))
            now = time.monotonic()
            self.seq += 1
            duration = 0.8 if kind == "long" else 0.2
            self.events.append(SqueezeEvent(self.seq, kind, now - duration, now, 700.0))
            return {"ok": True, "seq": self.seq}

        if cmd == "get_events":
            since = int(command.get("since_seq", 0))
            return {"events": [e.to_dict() for e in self.events if e.seq > since], "last_seq": self.seq}

        if cmd == "capture":
            # relaxed captures see ~100, squeezed ~700: enough span to pass calibration
            return {"median": 100.0, "p95": 700.0, "max": 750.0, "n": 60}

        if cmd == "set_calibration":
            return {"ok": True}

        if cmd == "clear_events":
            self.events.clear()
            return {"ok": True}

        return {"error": f"unknown command {cmd!r}"}


class FakeBuzzer(Generic, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:fake-buzzer"

    def __init__(self, name: str):
        super().__init__(name)
        self.history: list[dict] = []

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        cmd = command.get("command")
        if cmd == "history":
            history, self.history = self.history, []
            return {"history": history}
        if cmd == "status":
            return {"connected": True, "device": "fake", "playing": False}
        entry = {k: v for k, v in command.items()}
        self.history.append(entry)
        LOGGER.info("BUZZ %s", entry)
        return {"duration_ms": 0}
