"""Game-loop tests with scripted input/output/engine (no hardware, no stockfish)."""

import chess
import pytest

from src.lib.game import ChessCoach, CoachConfig


class ScriptedInput:
    """Feeds a fixed list of events. None entries represent pauses (timeouts)."""

    def __init__(self, events):
        self.events = list(events)
        self.calibration = None

    async def next_event(self, timeout):
        if not self.events:
            raise AssertionError("scripted input exhausted")
        return self.events.pop(0)

    async def capture(self, seconds):
        # first capture (relaxed) then squeezed; the same pair passes every time
        return {"median": 100.0, "p95": 700.0, "max": 720.0}

    async def set_calibration(self, baseline, peak):
        self.calibration = (baseline, peak)

    def clear(self):
        pass


class ScriptedOutput:
    def __init__(self):
        self.played = []

    async def play_signal(self, name):
        self.played.append(("signal", name))

    async def play_groups(self, counts):
        self.played.append(("groups", list(counts)))


class ScriptedEngine:
    def __init__(self, ucis):
        self.ucis = list(ucis)

    async def best_move(self, board):
        return chess.Move.from_uci(self.ucis.pop(0))

    async def close(self):
        pass


def groups(*counts):
    """Squeeze events for count groups, each closed by a pause."""
    out = []
    for c in counts:
        out += ["short"] * c + [None]
    return out


CFG = CoachConfig(skip_calibration=True)


def make_coach(events, ucis=(), board=None, user_color=None):
    coach = ChessCoach(ScriptedInput(events), ScriptedOutput(), ScriptedEngine(ucis), CFG)
    if board is not None:
        coach.board = board
    if user_color is not None:
        coach.user_color = user_color
    return coach


async def test_full_game_fools_mate():
    # user plays black; engine finds the fool's mate. Script:
    # color: 2 shorts (black), echo confirm 1;
    # white move f2f3 (pawn,f,3), confirm; engine e7e5, ack;
    # white g2g4 (pawn,g,4), confirm; engine d8h4#, ack.
    events = (
        groups(2) + groups(1)                     # color select + confirm
        + groups(1, 6, 3) + groups(1)             # f3 + confirm
        + groups(1)                               # ack e5 recommendation
        + groups(1, 7, 4) + groups(1)             # g4 + confirm
        + groups(1)                               # ack Qh4# recommendation
    )
    coach = make_coach(events, ucis=["e7e5", "d8h4"])
    await coach.run_session()

    assert coach.board.is_checkmate()
    assert coach.user_color == chess.BLACK
    assert coach.state == "game_over"
    played = coach.output.played
    assert ("signal", "win") in played
    # the mating move was output with its check signal
    assert ("groups", [5, 8, 4]) in played
    assert ("signal", "check") in played


async def test_input_rejects_illegal_then_accepts():
    # queen to d5 from the start position is illegal -> error, then e4 accepted
    events = groups(5, 4, 5) + groups(1, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played


async def test_input_reject_echo_retry():
    # user enters e4, rejects the echo (2 shorts), re-enters d4, confirms
    events = groups(1, 5, 4) + groups(2) + groups(1, 4, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("d2d4")


async def test_disambiguation_picks_second_candidate():
    # knights b1 and f3 both reach the empty d2 square; user skips b1, accepts f3
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1")
    events = groups(2, 4, 2) + groups(2) + groups(1)
    coach = make_coach(events, board=board, user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("f3d2")
    played = coach.output.played
    assert ("signal", "ambiguity") in played
    assert ("groups", [2, 1]) in played  # b1 offered first
    assert ("groups", [6, 3]) in played  # then f3


async def test_promotion_input():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    # pawn a8 + confirm, then promotion answer: 2 shorts = knight
    events = groups(1, 1, 8) + groups(1) + groups(2)
    coach = make_coach(events, board=board, user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("a7a8n")
    assert ("signal", "promotion") in coach.output.played


async def test_long_squeeze_cancels_message():
    # start entering a move, cancel with a long squeeze, then enter e4
    events = ["short", "short", "long"] + groups(1, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played


async def test_output_replay_on_long_squeeze():
    coach = make_coach(["long"] + groups(1), board=chess.Board(), user_color=chess.WHITE)
    await coach.output_user_move(chess.Move.from_uci("e2e4"))
    plays = [p for p in coach.output.played if p == ("groups", [1, 5, 4])]
    assert len(plays) == 2  # replayed once


async def test_move_bypass():
    coach = make_coach(["move:g8f6"], board=chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"), user_color=chess.WHITE)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("g8f6")


async def test_activity_log_and_snapshot():
    events = groups(1, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    coach.board.push(move)

    kinds = [(e["kind"], e["detail"]) for e in coach.log]
    assert ("squeeze_group", "1") in kinds
    assert ("squeeze_group", "5") in kinds
    assert ("buzz_groups", "1-5-4") in kinds  # the echo
    assert ("decoded", "opponent: e4") in kinds
    assert all({"seq", "t", "kind", "detail"} <= set(e) for e in coach.log)

    snap = coach.snapshot()
    assert snap["hint"]
    assert snap["turn"] == "black"
    assert snap["log"][-1]["kind"] == "decoded"


async def test_practice_fail_on_confirmed_wrong_move():
    # user=black (preset). AI opponent plays e4; user enters it correctly.
    # Engine recommends e5; user acks. AI opponent plays d4; user enters
    # Nf3 (legal but wrong) and CONFIRMS it -> practice fail, session ends.
    cfg = CoachConfig(skip_calibration=True, practice=True, initial_color=chess.BLACK)
    events = (
        groups(1, 5, 4) + groups(1)   # enter e4 + confirm (correct)
        + groups(1)                   # ack the e5 recommendation
        + groups(2, 6, 3) + groups(1) # enter Nf3 + confirm (wrong!)
    )
    coach = ChessCoach(ScriptedInput(events), ScriptedOutput(), ScriptedEngine(["e2e4", "e7e5", "d2d4"]), cfg)
    result = await coach.run_session()

    assert result == "practice_fail"
    assert coach.state == "game_over"
    assert [m.uci() for m in coach.board.move_stack] == ["e2e4", "e7e5"]  # wrong move not pushed
    assert ("signal", "loss") in coach.output.played
    fails = [e for e in coach.log if e["kind"] == "practice_fail"]
    assert fails and "expected d4, got Nf3" in fails[0]["detail"]
    assert coach.expected_move is None  # cleared after fail


async def test_practice_rejected_echo_is_not_a_fail():
    # user enters the wrong move but REJECTS the echo, then enters the right one
    cfg = CoachConfig(skip_calibration=True, practice=True, initial_color=chess.BLACK)
    events = (
        groups(2, 6, 3) + groups(2)   # wrong Nf3, reject echo
        + groups(1, 5, 4) + groups(1) # correct e4, confirm
    )
    coach = ChessCoach(ScriptedInput(events), ScriptedOutput(), ScriptedEngine(["e2e4"]), cfg)
    # run just the opponent-entry part of the loop by exhausting the script after it
    task = __import__("asyncio").ensure_future(coach.run_session())
    try:
        await task
    except (AssertionError, IndexError):  # script/engine exhausted after the correct entry
        pass
    assert [m.uci() for m in coach.board.move_stack] == ["e2e4"]
    assert not [e for e in coach.log if e["kind"] == "practice_fail"]
    task.cancel()


async def test_practice_snapshot_exposes_expected_move():
    cfg = CoachConfig(skip_calibration=True, practice=True, initial_color=chess.BLACK)
    coach = ChessCoach(ScriptedInput([]), ScriptedOutput(), ScriptedEngine(["e2e4"]), cfg)
    coach.expected_move = chess.Move.from_uci("e2e4")
    coach.expected_san = "e4"
    snap = coach.snapshot()
    assert snap["practice"] is True
    assert snap["expected_move"] == {"uci": "e2e4", "san": "e4"}


async def test_calibration_sets_span():
    coach = make_coach([], board=chess.Board())
    await coach.calibrate()
    assert coach.input.calibration == (100.0, 700.0)
