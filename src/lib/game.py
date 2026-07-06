"""Chess-coach game loop.

Pure protocol/state-machine logic, written against two tiny interfaces so the
Viam service, unit tests, and the CLI harness all share the same loop:

- InputSource yields squeeze-level events: "short", "long", "group_break"
  (an explicit group boundary, used by simulated input), or "move:<uci>"
  (debug bypass). A None from next_event() means the timeout elapsed, which
  is how real pause-based group segmentation happens.
- OutputSink plays named signals and count groups on the buzzer.

See docs/PROTOCOL.md for the haptic protocol this implements.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

import chess
import chess.engine

from . import encoding

HINTS = {
    "idle": "no active session",
    "calibrate": "calibration starting",
    "calibrate_relax": "RELAX — capturing baseline (3 s)",
    "calibrate_squeeze": "SQUEEZE HARD — capturing peak (3 s)",
    "wait_color": "squeeze your color: 1 short = white, 2 shorts = black",
    "confirm_color": "confirm the color echo: 1 = yes, 2 = no",
    "wait_opponent_input": "enter the opponent's move: from-square then to-square — or 1 long squeeze = machine guesses",
    "oracle": "machine guessing: 1 = that's it, 2 = next, long = manual — answering mid-buzz cuts it short",
    "confirm_move": "confirm the move echo: 1 = yes, 2 = no",
    "promotion_query": "promotion piece: 1=Q 2=N 3=R 4=B",
    "engine_think": "engine thinking…",
    "output_move": "buzzing the recommended move…",
    "wait_ack": "play the move on the board, then squeeze 1 short (long = replay)",
    "game_over": "game over",
}


class InputSource(Protocol):
    async def next_event(self, timeout: float | None) -> str | None: ...
    async def capture(self, seconds: float) -> dict: ...
    async def set_calibration(self, baseline: float, peak: float) -> None: ...
    def clear(self) -> None: ...


class OutputSink(Protocol):
    async def play_signal(self, name: str) -> None: ...
    async def play_groups(self, counts: list[int]) -> None: ...
    async def stop(self) -> None: ...


class Engine(Protocol):
    async def best_move(self, board: chess.Board) -> chess.Move: ...
    async def ranked_moves(self, board: chess.Board, n: int) -> list[chess.Move]: ...
    async def close(self) -> None: ...


class InputCancelled(Exception):
    """User held a long squeeze: abandon the current message."""

    def __init__(self, shorts: int = 0):
        self.shorts = shorts  # shorts already squeezed in the current group


class OracleRequested(Exception):
    """Long squeeze on an empty message: the machine should guess the move."""


class MoveBypass(Exception):
    def __init__(self, uci: str):
        self.uci = uci


@dataclass
class CoachConfig:
    group_gap_s: float = 1.5  # pause that closes a count group
    message_timeout_s: float = 45.0  # max wait between groups of one message
    confirm_timeout_s: float = 30.0
    capture_seconds: float = 3.0
    min_calibration_span: float = 40.0  # pressure counts (device range 0-2000)
    skip_calibration: bool = False
    oracle_guesses: int = 5  # ranked guesses offered before falling back to manual entry
    practice: bool = False  # AI opponent: user must correctly enter its moves
    initial_color: bool | None = None  # preset user color (skips color select)


class StockfishEngine:
    def __init__(self, engine: chess.engine.Protocol, time_s: float):
        self._engine = engine
        self._time_s = time_s

    @classmethod
    async def create(cls, path: str, skill: int, time_s: float) -> "StockfishEngine":
        _, engine = await chess.engine.popen_uci(path)
        await engine.configure({"Skill Level": max(0, min(20, skill))})
        return cls(engine, time_s)

    async def best_move(self, board: chess.Board) -> chess.Move:
        result = await self._engine.play(board, chess.engine.Limit(time=self._time_s))
        if result.move is None:
            raise RuntimeError("engine returned no move")
        return result.move

    async def ranked_moves(self, board: chess.Board, n: int) -> list[chess.Move]:
        n = min(n, board.legal_moves.count())
        infos = await self._engine.analyse(board, chess.engine.Limit(time=self._time_s), multipv=n)
        return [info["pv"][0] for info in infos if info.get("pv")]

    async def close(self) -> None:
        try:
            await self._engine.quit()
        except chess.engine.EngineError:
            pass


class ChessCoach:
    """Runs one haptic chess session. Instantiate per game."""

    def __init__(
        self,
        input_source: InputSource,
        output: OutputSink,
        engine: Engine,
        cfg: CoachConfig | None = None,
        log: deque | None = None,
    ):
        self.input = input_source
        self.output = output
        self.engine = engine
        self.cfg = cfg or CoachConfig()

        self.state = "idle"
        self.board = chess.Board()
        self.user_color: bool | None = None
        self.pending_candidates: list[encoding.Candidate] = []
        self.last_message: list[int] = []
        self.expected_move: chess.Move | None = None  # practice: the move to enter
        self.expected_san: str | None = None
        # a shared log deque lets the activity feed survive practice-game restarts
        self.log: deque = log if log is not None else deque(maxlen=300)
        self._log_seq = int(self.log[-1]["seq"]) if self.log else 0

    # ---- introspection (for do_command "state") ----

    def _log(self, kind: str, detail: str) -> None:
        self._log_seq += 1
        self.log.append({"seq": self._log_seq, "t": time.time(), "kind": kind, "detail": detail})

    async def _signal(self, name: str) -> None:
        self._log("buzz_signal", name)
        await self.output.play_signal(name)

    async def _groups_out(self, counts: list[int]) -> None:
        self._log("buzz_groups", "-".join(map(str, counts)))
        await self.output.play_groups(counts)

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "hint": HINTS.get(self.state, ""),
            "fen": self.board.fen(),
            "turn": "white" if self.board.turn else "black",
            "user_color": {True: "white", False: "black", None: None}[self.user_color],
            "move_history": [m.uci() for m in self.board.move_stack],
            "pending_candidates": [c.move().uci() for c in self.pending_candidates],
            "last_message": list(self.last_message),
            "practice": self.cfg.practice,
            "expected_move": (
                {"uci": self.expected_move.uci(), "san": self.expected_san}
                if self.expected_move is not None
                else None
            ),
            "log": list(self.log)[-100:],
        }

    # ---- low-level input helpers ----

    async def _read_group(self, first_timeout: float | None) -> int:
        """Count short squeezes until a pause >= group_gap closes the group.
        Raises InputCancelled on a long squeeze, MoveBypass on a move event.
        Returns 0 if nothing arrived within first_timeout."""
        n = 0
        while True:
            ev = await self.input.next_event(self.cfg.group_gap_s if n else first_timeout)
            if ev is None:
                if n:
                    self._log("squeeze_group", str(n))
                return n
            if ev == "group_break":
                if n:
                    self._log("squeeze_group", str(n))
                    return n
                continue
            if ev == "long":
                self._log("squeeze_long", "long")
                raise InputCancelled(shorts=n)
            if ev == "short":
                n += 1
            elif ev.startswith("move:"):
                raise MoveBypass(ev[5:])

    async def _read_message(self, allow_oracle: bool = False) -> list[int]:
        """One 4-group message. First squeeze waits indefinitely. A long squeeze
        on a still-empty message raises OracleRequested (when allowed)."""
        while True:
            try:
                groups = []
                for i in range(4):
                    try:
                        n = await self._read_group(None if i == 0 else self.cfg.message_timeout_s)
                    except InputCancelled as exc:
                        if allow_oracle and i == 0 and exc.shorts == 0:
                            raise OracleRequested() from None
                        raise
                    if n == 0:  # inter-group timeout: user stalled out
                        raise InputCancelled()
                    groups.append(n)
                self.last_message = groups
                return groups
            except InputCancelled:
                await self._signal("error")
                self.input.clear()

    async def _read_choice(self, prompt_signal: str | None = None) -> int:
        """One count group as an answer (confirm=1 / reject=2 / menu pick).
        A long squeeze or timeout counts as 0 (caller decides what that means)."""
        if prompt_signal:
            await self._signal(prompt_signal)
        try:
            return await self._read_group(self.cfg.confirm_timeout_s)
        except InputCancelled:
            return 0

    async def _confirm(self) -> bool:
        """1 short = yes, 2 shorts = no; anything else re-asks (3 tries -> no)."""
        for _ in range(3):
            n = await self._read_choice()
            if n == 1:
                return True
            if n == 2:
                return False
            await self._signal("error")
        return False

    # ---- session phases ----

    async def calibrate(self) -> None:
        self.state = "calibrate"
        while True:
            self.state = "calibrate_relax"
            await self._signal("calibrate_relax")
            relaxed = await self.input.capture(self.cfg.capture_seconds)
            self.state = "calibrate_squeeze"
            await self._signal("calibrate_squeeze")
            squeezed = await self.input.capture(self.cfg.capture_seconds)
            self.state = "calibrate"
            baseline, peak = relaxed["median"], squeezed["p95"]
            self._log(
                "calibrate",
                f"baseline {baseline:.0f}, peak {peak:.0f}, span {peak - baseline:.0f}"
                f" (need {self.cfg.min_calibration_span:.0f})",
            )
            if peak - baseline >= self.cfg.min_calibration_span:
                await self.input.set_calibration(baseline, peak)
                await self._signal("ack")
                self.input.clear()
                return
            await self._signal("error")

    async def select_color(self) -> bool:
        """Returns chess.WHITE/BLACK. 1 short = white, 2 = black; echo + confirm."""
        while True:
            self.state = "wait_color"
            try:
                n = await self._read_group(first_timeout=None)
            except InputCancelled:
                self.input.clear()
                continue
            if n in (1, 2):
                await self._groups_out([n])
                self.state = "confirm_color"
                if await self._confirm():
                    return chess.WHITE if n == 1 else chess.BLACK
            else:
                await self._signal("error")

    async def _resolve_promotion(self) -> int:
        """Promotion query: 1=Q 2=N 3=R 4=B."""
        self.state = "promotion_query"
        while True:
            n = await self._read_choice(prompt_signal="promotion")
            if n in encoding.PROMO_COUNTS:
                return encoding.PROMO_COUNTS[n]
            await self._signal("error")

    async def _buzz_move(self, enc: encoding.MoveEncoding) -> None:
        for group in enc.all_groups():
            await self._groups_out(group)

    async def _interrupt_playback(self, play: asyncio.Task) -> None:
        play.cancel()
        try:
            await play
        except asyncio.CancelledError:
            pass
        await self.output.stop()
        self._log("oracle", "interrupted")

    async def _oracle_answer(self, play: asyncio.Task) -> int:
        """Read one answer group; the first squeeze cuts guess playback short.
        Returns the count (0 = timeout, -1 = long/bypass = bail)."""
        n = 0
        while True:
            ev = await self.input.next_event(self.cfg.group_gap_s if n else self.cfg.confirm_timeout_s)
            if ev is not None and not play.done():
                await self._interrupt_playback(play)
            if ev is None or (ev == "group_break" and n):
                if n:
                    self._log("squeeze_group", str(n))
                return n
            if ev == "group_break":
                continue
            if ev == "long" or ev.startswith("move:"):
                self._log("squeeze_long", "long")
                return -1
            if ev == "short":
                n += 1

    async def _oracle_cycle(self) -> chess.Move | None:
        """Buzz engine-ranked guesses; 1 = accept, 2 = next, anything else = bail.
        Answers may arrive mid-buzz (playback is cut short). Returns the
        accepted move, or None to fall back to manual entry."""
        self.state = "oracle"
        guesses = await self.engine.ranked_moves(self.board, self.cfg.oracle_guesses)
        for move in guesses:
            self._log("oracle", f"guess: {self.board.san(move)}")
            play = asyncio.create_task(self._buzz_move(encoding.encode_move(self.board, move)))
            try:
                n = await self._oracle_answer(play)
            finally:
                if not play.done():
                    await self._interrupt_playback(play)
                elif not play.cancelled():
                    await play  # surface playback errors
            if n == 1:
                return move
            if n != 2:
                return None  # long / timeout / garbage: back to manual
        await self._signal("error")  # guesses exhausted
        return None

    async def input_opponent_move(self) -> chess.Move:
        while True:
            self.state = "wait_opponent_input"
            try:
                groups = await self._read_message(allow_oracle=True)
            except OracleRequested:
                move = await self._oracle_cycle()
                if move is not None and move in self.board.legal_moves:
                    self.pending_candidates = []
                    self._log("decoded", f"opponent: {self.board.san(move)}")
                    return move
                continue
            except MoveBypass as bypass:
                move = chess.Move.from_uci(bypass.uci)
                if move in self.board.legal_moves:
                    return move
                await self._signal("error")
                continue

            cands = encoding.candidates(self.board, *groups)
            self.pending_candidates = cands
            if not cands:
                await self._signal("error")
                continue

            chosen = cands[0]
            # echo the decoded message back for confirmation
            await self._groups_out(groups)
            self.state = "confirm_move"
            if not await self._confirm():
                continue

            promotion = None
            if chosen.needs_promotion:
                promotion = await self._resolve_promotion()
            move = chosen.move(promotion)
            if move in self.board.legal_moves:
                self.pending_candidates = []
                self._log("decoded", f"opponent: {self.board.san(move)}")
                return move
            await self._signal("error")

    async def output_user_move(self, move: chess.Move) -> None:
        """Buzz the recommendation; 1 short = played it, long = replay."""
        self.state = "output_move"
        self._log("engine", f"recommend: {self.board.san(move)}")
        enc = encoding.encode_move(self.board, move)
        while True:
            await self._signal("attention")
            for group in enc.all_groups():
                await self._groups_out(group)
            if enc.gives_check:
                await self._signal("check")
            self.state = "wait_ack"
            try:
                n = await self._read_group(self.cfg.confirm_timeout_s)
            except InputCancelled:
                continue  # long squeeze: replay
            if n == 1:
                return
            # timeout or anything else: replay

    def _result_signal(self) -> str:
        outcome = self.board.outcome()
        if outcome is None or outcome.winner is None:
            return "draw"
        return "win" if outcome.winner == self.user_color else "loss"

    async def run_session(self) -> str:
        """Returns "complete" or, in practice mode, possibly "practice_fail"."""
        await self._signal("ready")
        if not self.cfg.skip_calibration:
            await self.calibrate()
        if self.cfg.initial_color is None:
            self.user_color = await self.select_color()
        else:
            self.user_color = self.cfg.initial_color
            await self._signal("ack")

        while not self.board.is_game_over():
            if self.board.turn == self.user_color:
                self.state = "engine_think"
                move = await self.engine.best_move(self.board)
                await self.output_user_move(move)
            elif self.cfg.practice:
                # AI opponent: engine picks the move, the user must enter it
                self.state = "engine_think"
                expected = await self.engine.best_move(self.board)
                self.expected_move = expected
                self.expected_san = self.board.san(expected)
                self._log("practice", f"opponent plays: {self.expected_san}")
                entered = await self.input_opponent_move()
                if entered != expected:
                    self._log(
                        "practice_fail",
                        f"expected {self.expected_san}, got {self.board.san(entered)}",
                    )
                    await self._signal("loss")
                    self.expected_move = self.expected_san = None
                    self.state = "game_over"
                    return "practice_fail"
                self._log("practice", "correct")
                self.expected_move = self.expected_san = None
                move = entered
            else:
                move = await self.input_opponent_move()
            self._log("move", self.board.san(move))
            self.board.push(move)

        self.state = "game_over"
        await self._signal(self._result_signal())
        return "complete"
