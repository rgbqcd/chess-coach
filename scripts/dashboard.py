#!/usr/bin/env python
"""Live debugging dashboard for the haptic chess robot.

Bridges the viam machine to a local web page: an asyncio task polls the
chess-coach state, the squeeze sensor readings, and the buzzer status, and a
tiny stdlib HTTP server serves web/index.html plus the merged snapshot at
/state.json. No dependencies beyond what the module already uses.

Usage: uv run python scripts/dashboard.py [--robot localhost:8080] [--port 8765]
       [--coach coach] [--sensor kgoal] [--buzzer hush]
"""

import argparse
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import chess
from viam.components.sensor import Sensor
from viam.robot.client import RobotClient
from viam.rpc.dial import DialOptions
from viam.components.generic import Generic as GenericComponent
from viam.services.generic import Generic as GenericService

INDEX_HTML = ROOT / "web" / "index.html"
POLL_S = 0.35

# shared between the poller (asyncio) and the HTTP server (threads);
# always replaced atomically, never mutated in place
latest: dict = {"connected": False, "error": "connecting..."}
# set by the poller so HTTP threads can forward commands to the coach
runtime: dict = {"loop": None, "coach": None}


def san_history(ucis: list) -> list:
    """Replay UCI moves from the start position to get SAN; fall back to UCI."""
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


async def poll(args) -> None:
    global latest
    robot = None
    while True:
        try:
            if robot is None:
                opts = RobotClient.Options(dial_options=DialOptions(insecure=True, disable_webrtc=True))
                robot = await RobotClient.at_address(args.robot, opts)
                coach = GenericService.from_robot(robot, args.coach)
                sensor = Sensor.from_robot(robot, args.sensor)
                buzzer = GenericComponent.from_robot(robot, args.buzzer)
                runtime["loop"] = asyncio.get_running_loop()
                runtime["coach"] = coach
                print(f"connected to {args.robot}")

            state, readings, buzz = {}, {}, {}
            try:
                state = dict(await coach.do_command({"command": "state"}))
            except Exception as err:
                state = {"error": f"coach: {err}"}
            try:
                readings = dict(await sensor.get_readings())
            except Exception as err:
                readings = {"error": f"sensor: {err}"}
            try:
                buzz = dict(await buzzer.do_command({"command": "status"}))
            except Exception as err:
                buzz = {"error": f"buzzer: {err}"}

            state["move_history_san"] = san_history(state.get("move_history", []))
            latest = {
                "connected": True,
                "t": time.time(),
                "coach": state,
                "sensor": readings,
                "buzzer": buzz,
            }
        except Exception as err:
            latest = {"connected": False, "error": str(err), "t": time.time()}
            if robot is not None:
                try:
                    await robot.close()
                except Exception:
                    pass
                robot = None
            await asyncio.sleep(2)
        await asyncio.sleep(POLL_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/state.json":
            body = json.dumps(latest).encode()
            ctype = "application/json"
        elif path in ("/", "/index.html"):
            body = INDEX_HTML.read_bytes()
            ctype = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/command":
            self.send_response(404)
            self.end_headers()
            return
        loop, coach = runtime["loop"], runtime["coach"]
        try:
            length = int(self.headers.get("Content-Length", 0))
            cmd = json.loads(self.rfile.read(length) or b"{}")
            if loop is None or coach is None:
                raise RuntimeError("robot not connected")
            future = asyncio.run_coroutine_threadsafe(coach.do_command(cmd), loop)
            body = json.dumps(dict(future.result(timeout=10))).encode()
            status = 200
        except Exception as err:
            body = json.dumps({"error": str(err)}).encode()
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass  # keep the console quiet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default="localhost:8080")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--coach", default="coach")
    parser.add_argument("--sensor", default="kgoal")
    parser.add_argument("--buzzer", default="hush")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"dashboard: http://localhost:{args.port}")

    try:
        asyncio.run(poll(args))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
