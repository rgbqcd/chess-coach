"""Viam generic service that serves the practice dashboard from inside the module.

With this in the machine config there is no separate bridge process: whenever
viam-server is up, http://localhost:<port> serves the live setup/game page,
talking to the coach, sensor, and buzzer directly as module dependencies.
(scripts/dashboard.py remains as a standalone bridge for pointing the same
page at a *remote* machine with an API key.)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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

LOGGER = getLogger(__name__)

INDEX_HTML = Path(__file__).resolve().parents[2] / "web" / "index.html"
CACHE_TTL_S = 0.25


def san_history(ucis: list) -> list:
    board = chess.Board()
    out = []
    try:
        for u in ucis:
            move = chess.Move.from_uci(str(u))
            out.append(board.san(move))
            board.push(move)
    except (ValueError, AssertionError):
        return [str(u) for u in ucis]
    return out


class DashboardService(GenericService, EasyResource):
    MODEL: ClassVar[Model] = "rgbqcd:chess-playing:dashboard"

    def __init__(self, name: str):
        super().__init__(name)
        self.coach: Optional[GenericService] = None
        self.sensor: Optional[Sensor] = None
        self.buzzer: Optional[GenericComponent] = None
        self.port = 8765
        self.bind = "127.0.0.1"
        self.read_only = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self._cache: dict = {}
        self._cache_t = 0.0
        self._cache_lock = threading.Lock()

    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        self = super().new(config, dependencies)
        self.reconfigure(config, dependencies)
        return self

    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        attrs = struct_to_dict(config.attributes)
        for key in ("coach", "input_sensor", "output_buzzer"):
            if not attrs.get(key):
                raise ValueError(f"dashboard requires attribute {key!r}")
        return [str(attrs["coach"]), str(attrs["input_sensor"]), str(attrs["output_buzzer"])], []

    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        attrs = struct_to_dict(config.attributes)
        self.coach = dependencies[GenericService.get_resource_name(str(attrs["coach"]))]  # type: ignore[assignment]
        self.sensor = dependencies[Sensor.get_resource_name(str(attrs["input_sensor"]))]  # type: ignore[assignment]
        self.buzzer = dependencies[GenericComponent.get_resource_name(str(attrs["output_buzzer"]))]  # type: ignore[assignment]
        self.read_only = bool(attrs.get("read_only", False))
        self.loop = asyncio.get_running_loop()

        port = int(attrs.get("port", 8765))
        bind = str(attrs.get("bind", "127.0.0.1"))
        if self._server is None or port != self.port or bind != self.bind:
            self._stop_server()
            self.port = port
            self.bind = bind
            self._start_server()

    # ---- HTTP ----

    def _start_server(self) -> None:
        service = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/state.json":
                    self._send(json.dumps(service.state_blocking()).encode(), "application/json")
                elif path in ("/", "/index.html"):
                    self._send(INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                if self.path.split("?", 1)[0] != "/command":
                    self.send_response(404)
                    self.end_headers()
                    return
                if service.read_only:
                    self._send(json.dumps({"error": "dashboard is read-only"}).encode(), "application/json", 403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    cmd = json.loads(self.rfile.read(length) or b"{}")
                    result = service.command_blocking(cmd)
                    self._send(json.dumps(result).encode(), "application/json")
                except Exception as err:
                    self._send(json.dumps({"error": str(err)}).encode(), "application/json", 500)

            def log_message(self, *_args):
                pass

        try:
            self._server = ThreadingHTTPServer((self.bind, self.port), Handler)
        except OSError as err:
            LOGGER.error("dashboard cannot bind %s:%s: %s", self.bind, self.port, err)
            self._server = None
            return
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        LOGGER.info("dashboard serving at http://%s:%s", self.bind, self.port)

    def _stop_server(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    # ---- state gathering (called from HTTP threads) ----

    def _run_on_loop(self, coro, timeout: float = 10.0):
        assert self.loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def state_blocking(self) -> dict:
        with self._cache_lock:
            if time.time() - self._cache_t < CACHE_TTL_S and self._cache:
                return self._cache
        try:
            state = self._run_on_loop(self._gather_state())
        except Exception as err:
            state = {"connected": False, "error": str(err), "t": time.time()}
        with self._cache_lock:
            self._cache = state
            self._cache_t = time.time()
        return state

    async def _gather_state(self) -> dict:
        assert self.coach is not None and self.sensor is not None and self.buzzer is not None
        state, readings, buzz = {}, {}, {}
        try:
            state = dict(await self.coach.do_command({"command": "state"}))
        except Exception as err:
            state = {"error": f"coach: {err}"}
        try:
            readings = dict(await self.sensor.get_readings())
        except Exception as err:
            readings = {"error": f"sensor: {err}"}
        try:
            buzz = dict(await self.buzzer.do_command({"command": "status"}))
        except Exception as err:
            buzz = {"error": f"buzzer: {err}"}
        state["move_history_san"] = san_history(state.get("move_history", []))
        return {
            "connected": True,
            "read_only": self.read_only,
            "t": time.time(),
            "coach": state,
            "sensor": readings,
            "buzzer": buzz,
        }

    def command_blocking(self, cmd: dict) -> dict:
        target = str(cmd.pop("target", "coach"))
        resource = {"coach": self.coach, "buzzer": self.buzzer, "sensor": self.sensor}.get(target)
        if resource is None:
            return {"error": f"unknown target {target!r}"}
        return dict(self._run_on_loop(resource.do_command(cmd)))

    # ---- Viam API ----

    async def do_command(self, command: Mapping[str, ValueTypes], *, timeout=None, **kwargs) -> Mapping[str, ValueTypes]:
        if command.get("command") == "status":
            return {"serving": self._server is not None, "port": self.port, "read_only": self.read_only}
        return {"error": "unknown command"}

    async def close(self):
        self._stop_server()
        await super().close()
