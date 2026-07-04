"""Viam generic component driving a Lovense Hush 2 through buttplug.io.

Connects to Intiface Central's websocket with the official `buttplug` client
and plays morse-like buzz patterns (see lib/patterns.py). Pattern playback is
a single serialized task: a new pattern cancels the current one. do_command
calls block until playback finishes by default, so a caller sequencing
signal/groups exchanges gets correct timing for free.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import ClassVar, Mapping, Optional, Sequence, Tuple

from buttplug import ButtplugClient, ButtplugDevice, DeviceOutputCommand, OutputType
from typing_extensions import Self
from viam.components.generic import Generic
from viam.logging import getLogger
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model
from viam.utils import ValueTypes, struct_to_dict

from ..lib import patterns
from ..lib.patterns import Step, Timing

LOGGER = getLogger(__name__)


class HushBuzzer(Generic, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:hush-buzzer"

    def __init__(self, name: str):
        super().__init__(name)
        self.ws_url = "ws://127.0.0.1:12345"
        self.device_match = "Hush"
        self.timing = Timing()
        self.client: Optional[ButtplugClient] = None
        self.device: Optional[ButtplugDevice] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._play_task: Optional[asyncio.Task] = None

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        self = super().new(config, dependencies)
        self.reconfigure(config, dependencies)
        return self

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        attrs = struct_to_dict(config.attributes)
        for key in ("intensity", "error_intensity"):
            if key in attrs and not 0 < float(attrs[key]) <= 1:
                raise ValueError(f"{key} must be in (0, 1]")
        return [], []

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        attrs = struct_to_dict(config.attributes)
        self.ws_url = str(attrs.get("ws_url", "ws://127.0.0.1:12345"))
        self.device_match = str(attrs.get("device_match", "Hush"))
        self.timing = Timing(
            dot_ms=int(attrs.get("dot_ms", 200)),
            dash_ms=int(attrs.get("dash_ms", 600)),
            gap_ms=int(attrs.get("gap_ms", 250)),
            group_gap_ms=int(attrs.get("group_gap_ms", 900)),
            intensity=float(attrs.get("intensity", 0.7)),
            error_intensity=float(attrs.get("error_intensity", 0.4)),
        )
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = asyncio.create_task(self._connect_loop())

    # ---- buttplug connection ----

    def _pick_device(self) -> Optional[ButtplugDevice]:
        if self.client is None:
            return None
        for device in self.client.devices.values():
            if self.device_match.lower() in device.name.lower() and device.has_output(OutputType.VIBRATE):
                return device
        return None

    async def _connect_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                if self.client is None or not self.client.connected:
                    self.device = None
                    client = ButtplugClient("chess-playing")
                    client.on_device_removed = self._on_device_removed
                    client.on_server_disconnect = self._on_server_disconnect
                    await client.connect(self.ws_url)
                    self.client = client
                    LOGGER.info("connected to Intiface at %s", self.ws_url)
                    backoff = 1.0

                if self.device is None:
                    self.device = self._pick_device()
                    if self.device is None:
                        await self.client.start_scanning()
                        await asyncio.sleep(5)
                        try:
                            await self.client.stop_scanning()
                        except Exception:
                            pass
                        self.device = self._pick_device()
                    if self.device is not None:
                        LOGGER.info("using device: %s", self.device.name)

                await asyncio.sleep(2 if self.device is None else 10)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                LOGGER.warning("Intiface connection failed (%s); retrying in %.0fs", err, backoff)
                self.client = None
                self.device = None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _on_device_removed(self, device: ButtplugDevice) -> None:
        if self.device is not None and device.index == self.device.index:
            LOGGER.warning("device %s removed", device.name)
            self.device = None

    def _on_server_disconnect(self) -> None:
        LOGGER.warning("Intiface server disconnected")
        self.device = None

    # ---- playback ----

    async def _play_steps(self, steps: list[Step]) -> None:
        device = self.device
        if device is None:
            raise RuntimeError("no buzzer device connected")
        level = None
        try:
            for lv, ms in steps:
                if lv != level:
                    await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, float(lv)))
                    level = lv
                await asyncio.sleep(ms / 1000)
        finally:
            try:
                await device.stop()
            except Exception:
                LOGGER.debug("device stop failed", exc_info=True)

    async def _play(self, steps: list[Step], wait: bool) -> Mapping[str, ValueTypes]:
        if self._play_task is not None and not self._play_task.done():
            self._play_task.cancel()
            try:
                await self._play_task
            except (asyncio.CancelledError, Exception):
                pass
        self._play_task = asyncio.create_task(self._play_steps(steps))
        if wait:
            await self._play_task
        return {"duration_ms": patterns.duration_ms(steps)}

    def _timing(self, command: Mapping[str, ValueTypes]) -> Timing:
        overrides = {
            f.name: type(getattr(self.timing, f.name))(command[f.name])
            for f in dataclasses.fields(Timing)
            if f.name in command
        }
        return dataclasses.replace(self.timing, **overrides)

    # ---- Viam API ----

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        cmd = command.get("command")
        wait = bool(command.get("wait", True))
        timing = self._timing(command)
        level = float(command["intensity"]) if "intensity" in command else None

        if cmd == "pattern":
            steps = patterns.from_elements([str(e) for e in command["pattern"]], timing, level)
        elif cmd == "morse":
            steps = patterns.from_morse(str(command["text"]), timing, level)
        elif cmd == "morse_letter":
            steps = patterns.from_text(str(command["letter"]), timing, level)
        elif cmd == "count":
            steps = patterns.count(int(command["n"]), timing, level)
        elif cmd == "groups":
            steps = patterns.count_groups([int(n) for n in command["counts"]], timing, level)
        elif cmd == "signal":
            steps = patterns.signal(str(command["name"]), timing)
        elif cmd == "buzz":
            steps = [(level if level is not None else timing.intensity, int(command.get("ms", 300)))]
        elif cmd == "stop":
            if self._play_task is not None:
                self._play_task.cancel()
            if self.device is not None:
                await self.device.stop()
            return {"ok": True}
        elif cmd == "status":
            return {
                "connected": self.client is not None and self.client.connected,
                "device": self.device.name if self.device else "",
                "playing": self._play_task is not None and not self._play_task.done(),
            }
        else:
            return {"error": f"unknown command {cmd!r}"}

        return dict(await self._play(steps, wait))

    async def close(self):
        for task in (self._play_task, self._connect_task):
            if task is not None:
                task.cancel()
        if self.device is not None:
            try:
                await self.device.stop()
            except Exception:
                pass
        if self.client is not None and self.client.connected:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        await super().close()
