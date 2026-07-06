"""Viam generic service running the haptic chess session.

Depends on an input sensor (kgoal-boost or fake-kgoal) and an output buzzer
(hush-buzzer or fake-buzzer). Adapts them to the InputSource/OutputSink
protocols of lib/game.py: input polls the sensor's get_events do_command
(sequence numbers make polling lossless), output calls the buzzer's blocking
do_commands. Debug do_commands can inject synthetic squeezes so the whole
protocol is testable with zero hardware.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
from collections import deque
from typing import ClassVar, Mapping, Optional, Sequence, Tuple

import chess
from typing_extensions import Self
from viam.components.generic import Generic as GenericComponent
from viam.components.sensor import Sensor
from viam.logging import getLogger
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.easy_resource import EasyResource
from viam.resource.types import Model
from viam.services.generic import Generic as GenericService
from viam.utils import ValueTypes, struct_to_dict

from ..lib.game import ChessCoach, CoachConfig, StockfishEngine

LOGGER = getLogger(__name__)

class ViamInput:
    """InputSource backed by the squeeze sensor's do_command interface."""

    def __init__(self, sensor: Sensor, poll_s: float = 0.1):
        self.sensor = sensor
        self.poll_s = poll_s
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._since_seq = 0
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._poll())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _poll(self) -> None:
        # initialize cursor past any stale events
        try:
            result = await self.sensor.do_command({"command": "get_events", "since_seq": 0})
            self._since_seq = int(result.get("last_seq", 0))
        except Exception:
            LOGGER.debug("initial get_events failed", exc_info=True)
        while True:
            await asyncio.sleep(self.poll_s)
            try:
                result = await self.sensor.do_command({"command": "get_events", "since_seq": self._since_seq})
            except Exception:
                LOGGER.warning("get_events failed", exc_info=True)
                continue
            for event in result.get("events", []):
                self._since_seq = max(self._since_seq, int(event["seq"]))
                self.queue.put_nowait(str(event["kind"]))

    def inject(self, event: str) -> None:
        self.queue.put_nowait(event)

    async def next_event(self, timeout: float | None) -> str | None:
        try:
            return await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def capture(self, seconds: float) -> dict:
        result = await self.sensor.do_command({"command": "capture", "seconds": seconds})
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        return dict(result)

    async def set_calibration(self, baseline: float, peak: float) -> None:
        await self.sensor.do_command({"command": "set_calibration", "baseline": baseline, "peak": peak})

    def clear(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()


class ViamOutput:
    """OutputSink backed by the buzzer's blocking do_commands."""

    def __init__(self, buzzer: GenericComponent):
        self.buzzer = buzzer

    async def play_signal(self, name: str) -> None:
        await self.buzzer.do_command({"command": "signal", "name": name, "wait": True})

    async def play_groups(self, counts: list[int]) -> None:
        await self.buzzer.do_command({"command": "groups", "counts": counts, "wait": True})

    async def stop(self) -> None:
        await self.buzzer.do_command({"command": "stop"})


class ChessCoachService(GenericService, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:chess-coach"

    def __init__(self, name: str):
        super().__init__(name)
        self.sensor: Optional[Sensor] = None
        self.buzzer: Optional[GenericComponent] = None
        self.stockfish_path = ""
        self.input_poll_s = 0.1
        self.practice_restart_delay_s = 2.0
        self.engine_skill = 5
        self.engine_time_s = 1.0
        self.auto_start = True
        self.practice_mode = False
        self.stats = {"games": 0, "practice_fails": 0}
        self.coach_cfg = CoachConfig()
        self.coach: Optional[ChessCoach] = None
        self.input: Optional[ViamInput] = None
        self.engine: Optional[StockfishEngine] = None
        self._session_task: Optional[asyncio.Task] = None

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        self = super().new(config, dependencies)
        self.reconfigure(config, dependencies)
        return self

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        attrs = struct_to_dict(config.attributes)
        for key in ("input_sensor", "output_buzzer"):
            if not attrs.get(key):
                raise ValueError(f"chess-coach requires attribute {key!r}")
        return [str(attrs["input_sensor"]), str(attrs["output_buzzer"])], []

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        attrs = struct_to_dict(config.attributes)
        sensor_dep = dependencies[Sensor.get_resource_name(str(attrs["input_sensor"]))]
        buzzer_dep = dependencies[GenericComponent.get_resource_name(str(attrs["output_buzzer"]))]
        assert isinstance(sensor_dep, Sensor)
        assert isinstance(buzzer_dep, GenericComponent)
        self.sensor = sensor_dep
        self.buzzer = buzzer_dep

        default_stockfish = shutil.which("stockfish") or "/opt/homebrew/bin/stockfish"
        self.stockfish_path = str(attrs.get("stockfish_path", default_stockfish))
        self.input_poll_s = float(attrs.get("input_poll_ms", 100)) / 1000
        self.practice_restart_delay_s = float(attrs.get("practice_restart_delay_s", 2.0))
        self.engine_skill = int(attrs.get("engine_skill", 5))
        self.engine_time_s = float(attrs.get("engine_time_s", 1.0))
        self.auto_start = bool(attrs.get("auto_start", True))
        self.practice_mode = bool(attrs.get("practice_mode", False))
        self.coach_cfg = CoachConfig(
            group_gap_s=float(attrs.get("group_gap_ms", 1500)) / 1000,
            message_timeout_s=float(attrs.get("message_timeout_s", 45)),
            confirm_timeout_s=float(attrs.get("confirm_timeout_s", 30)),
            capture_seconds=float(attrs.get("capture_seconds", 3.0)),
            min_calibration_span=float(attrs.get("min_calibration_span", 40)),
            oracle_guesses=int(attrs.get("oracle_guesses", 5)),
            skip_calibration=bool(attrs.get("skip_calibration", False)),
        )

        self._stop_session()
        if self.auto_start:
            self._start_session()

    # ---- session lifecycle ----

    def _stop_session(self) -> None:
        if self._session_task is not None and not self._session_task.done():
            self._session_task.cancel()
        if self.input is not None:
            self.input.stop()
        self._session_task = None

    def _start_session(self, force_calibration: bool = False) -> None:
        self._stop_session()
        self._session_task = asyncio.create_task(self._run_session(force_calibration))

    async def _skip_calibration(self, first_game: bool, force: bool) -> bool:
        if force and first_game:
            return False
        if not first_game or self.coach_cfg.skip_calibration:
            return True
        # skip automatically when the sensor still holds a calibration
        try:
            assert self.sensor is not None
            readings = await self.sensor.get_readings()
            return bool(readings.get("calibrated"))
        except Exception:
            return False

    async def _run_session(self, force_calibration: bool = False) -> None:
        assert self.sensor is not None and self.buzzer is not None
        engine = None
        self.input = ViamInput(self.sensor, self.input_poll_s)
        self.input.start()
        last_color = None
        first_game = True
        session_log: deque = deque(maxlen=300)
        try:
            engine = await StockfishEngine.create(self.stockfish_path, self.engine_skill, self.engine_time_s)
            self.engine = engine
            while True:
                cfg = dataclasses.replace(
                    self.coach_cfg,
                    practice=self.practice_mode,
                    initial_color=last_color,
                    skip_calibration=await self._skip_calibration(first_game, force_calibration),
                )
                self.coach = ChessCoach(self.input, ViamOutput(self.buzzer), engine, cfg, log=session_log)
                LOGGER.info("chess session starting (practice=%s)", self.practice_mode)
                result = await self.coach.run_session()
                LOGGER.info("chess session finished: %s (%s)", self.coach.board.result(), result)
                self.stats["games"] += 1
                if result == "practice_fail":
                    self.stats["practice_fails"] += 1
                if not self.practice_mode:
                    break
                # practice: fresh game, same color, no re-calibration
                last_color = self.coach.user_color
                first_game = False
                self.input.clear()
                await asyncio.sleep(self.practice_restart_delay_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("chess session crashed")
        finally:
            self.input.stop()
            if engine is not None:
                await engine.close()
                self.engine = None

    # ---- Viam API ----

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        cmd = command.get("command")

        if cmd == "state":
            running = self._session_task is not None and not self._session_task.done()
            snapshot = self.coach.snapshot() if self.coach else {}
            return {
                "session_running": running,
                "practice_mode": self.practice_mode,
                "stats": dict(self.stats),
                "engine_ok": self.engine is not None,
                "stockfish_path": self.stockfish_path,
                "stockfish_found": bool(self.stockfish_path) and os.path.isfile(self.stockfish_path),
                **snapshot,
            }

        if cmd == "set_practice":
            self.practice_mode = bool(command.get("on", True))
            self._start_session()
            return {"ok": True, "practice_mode": self.practice_mode}

        if cmd in ("reset", "start"):
            self._start_session()
            return {"ok": True}

        if cmd == "recalibrate":
            self._start_session(force_calibration=True)
            return {"ok": True}

        if cmd == "set_board":
            if self.coach is None:
                return {"error": "no active session"}
            self.coach.board = chess.Board(str(command["fen"]))
            return {"ok": True, "fen": self.coach.board.fen()}

        if cmd == "simulate_squeeze":
            if self.input is None:
                return {"error": "no active session"}
            self.input.inject(str(command.get("kind", "short")))
            return {"ok": True}

        if cmd == "simulate_groups":
            if self.input is None:
                return {"error": "no active session"}
            for n in command["counts"]:
                for _ in range(int(n)):
                    self.input.inject("short")
                self.input.inject("group_break")
            return {"ok": True}

        if cmd == "input_move":
            if self.input is None:
                return {"error": "no active session"}
            self.input.inject(f"move:{command['uci']}")
            return {"ok": True}

        if cmd == "correct_user_move":
            if self.coach is None or not self.coach.board.move_stack:
                return {"error": "no move to correct"}
            move = chess.Move.from_uci(str(command["uci"]))
            board = self.coach.board
            popped = board.pop()
            if move not in board.legal_moves:
                board.push(popped)
                return {"error": f"{move.uci()} is not legal in place of {popped.uci()}"}
            board.push(move)
            return {"ok": True, "fen": board.fen()}

        return {"error": f"unknown command {cmd!r}"}

    async def close(self):
        self._stop_session()
        if self.engine is not None:
            await self.engine.close()
        await super().close()
