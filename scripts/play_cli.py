#!/usr/bin/env python
"""Play a full haptic-protocol game at the keyboard (no hardware needed).

Usage: uv run python scripts/play_cli.py [--stockfish /opt/homebrew/bin/stockfish]
       [--skill 5] [--time 1.0]

You type squeeze groups instead of squeezing, and buzzes are printed instead
of vibrating. Input syntax at the prompt:
    2 6 3      three count groups (knight, f-file, rank 3)
    1          a single group (confirm=1 / reject=2, menu answers)
    l          a long squeeze (cancel / replay)
    m e2e4     debug move bypass
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess

from src.lib.game import ChessCoach, CoachConfig, StockfishEngine


class KeyboardInput:
    def __init__(self):
        self.buffer: list[str | None] = []

    async def next_event(self, timeout):
        while not self.buffer:
            line = (await asyncio.to_thread(input, "squeeze> ")).strip().lower()
            if not line:
                continue
            if line == "l":
                self.buffer.append("long")
            elif line.startswith("m "):
                self.buffer.append(f"move:{line[2:].strip()}")
            else:
                try:
                    counts = [int(tok) for tok in line.split()]
                except ValueError:
                    print("  ? use counts like '2 6 3', 'l' for long, or 'm e2e4'")
                    continue
                for n in counts:
                    self.buffer += ["short"] * n + [None]  # None = pause closes group
        return self.buffer.pop(0)

    async def capture(self, seconds):
        return {"median": 100.0, "p95": 700.0, "max": 720.0}

    async def set_calibration(self, baseline, peak):
        pass

    def clear(self):
        self.buffer.clear()


PIECES = {1: "pawn", 2: "knight", 3: "bishop", 4: "rook", 5: "queen", 6: "king"}


class PrintOutput:
    async def play_signal(self, name):
        print(f"  BZZZ [signal: {name}]")

    async def play_groups(self, counts):
        hint = ""
        if len(counts) == 3 and counts[0] in PIECES and 1 <= counts[1] <= 8 and 1 <= counts[2] <= 8:
            hint = f"  ({PIECES[counts[0]]} -> {'abcdefgh'[counts[1] - 1]}{counts[2]})"
        print(f"  BZZZ groups: {'-'.join(map(str, counts))}{hint}")


class VerboseCoach(ChessCoach):
    async def input_opponent_move(self):
        print(f"\n{self.board}\n")
        print("enter the OPPONENT's move as: piece file rank (e.g. '1 5 4' = pawn e4)")
        move = await super().input_opponent_move()
        print(f"  decoded: {self.board.san(move)}")
        return move

    async def output_user_move(self, move):
        print(f"\n{self.board}\n")
        print(f"engine recommends: {self.board.san(move)} — buzzing it out; '1' to ack, 'l' to replay")
        await super().output_user_move(move)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stockfish", default="/opt/homebrew/bin/stockfish")
    parser.add_argument("--skill", type=int, default=5)
    parser.add_argument("--time", type=float, default=1.0)
    args = parser.parse_args()

    engine = await StockfishEngine.create(args.stockfish, args.skill, args.time)
    coach = VerboseCoach(KeyboardInput(), PrintOutput(), engine, CoachConfig(skip_calibration=True))
    print("color select: squeeze '1' for white, '2' for black (then '1' to confirm)")
    try:
        await coach.run_session()
        print(f"\n{coach.board}\n\ngame over: {coach.board.result()}")
    finally:
        await engine.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\nbye")
