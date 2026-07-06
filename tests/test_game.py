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

    async def stop(self):
        self.played.append(("stop",))


class SlowOutput(ScriptedOutput):
    """play_groups never finishes on its own: forces the interrupt path."""

    async def play_groups(self, counts):
        self.played.append(("groups", list(counts)))
        await __import__("asyncio").sleep(60)


class ScriptedEngine:
    def __init__(self, ucis, ranked=()):
        self.ucis = list(ucis)
        self.ranked = list(ranked)

    async def best_move(self, board):
        return chess.Move.from_uci(self.ucis.pop(0))

    async def ranked_moves(self, board, n):
        return [chess.Move.from_uci(u) for u in self.ranked[:n]]

    async def close(self):
        pass


def groups(*counts):
    """Squeeze events for count groups, each closed by a pause."""
    out = []
    for c in counts:
        out += ["short"] * c + [None]
    return out


CFG = CoachConfig(skip_calibration=True, attention_pause_s=0.0)


def make_coach(events, ucis=(), board=None, user_color=None, cfg=CFG, ranked=()):
    coach = ChessCoach(ScriptedInput(events), ScriptedOutput(), ScriptedEngine(ucis, ranked), cfg)
    if board is not None:
        coach.board = board
    if user_color is not None:
        coach.user_color = user_color
    return coach


async def test_full_game_fools_mate():
    # user plays black; engine finds the fool's mate. Script:
    # color: 2 shorts (black), echo confirm 1;
    # white f2f3 (6-2-6-3), confirm; engine e7e5, ack;
    # white g2g4 (7-2-7-4), confirm; engine d8h4#, ack.
    events = (
        groups(2) + groups(1)                     # color select + confirm
        + groups(6, 2, 6, 3) + groups(1)          # f3 + confirm
        + groups(1)                               # ack e5 recommendation
        + groups(7, 2, 7, 4) + groups(1)          # g4 + confirm
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
    assert ("groups", [4, 8, 8, 4]) in played
    assert ("signal", "check") in played


async def test_input_rejects_illegal_then_accepts():
    # e2e5 is illegal from the start position -> error, then e2e4 accepted
    events = groups(5, 2, 5, 5) + groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played


async def test_input_reject_echo_retry():
    # user enters e2e4, rejects the echo (2 shorts), re-enters d2d4, confirms
    events = groups(5, 2, 5, 4) + groups(2) + groups(4, 2, 4, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("d2d4")


async def test_from_square_distinguishes_twin_knights():
    # knights on b1 and f3 both reach d2; the from-square makes it exact
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1")
    events = groups(6, 3, 4, 2) + groups(1)  # f3d2 + confirm
    coach = make_coach(events, board=board, user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("f3d2")
    assert ("groups", [6, 3, 4, 2]) in coach.output.played  # echo, no menu


async def test_castling_input():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    events = groups(5, 1, 7, 1) + groups(1)  # e1g1 = O-O + confirm
    coach = make_coach(events, board=board, user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert board.is_castling(move)


async def test_promotion_input():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    # a7a8 + confirm, then promotion answer: 2 shorts = knight
    events = groups(1, 7, 1, 8) + groups(1) + groups(2)
    coach = make_coach(events, board=board, user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("a7a8n")
    assert ("signal", "promotion") in coach.output.played


async def test_long_squeeze_cancels_message():
    # start entering a move, cancel with a long squeeze, then enter e2e4
    events = ["short", "short", "long"] + groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played


async def test_output_replay_on_long_squeeze():
    coach = make_coach(["long"] + groups(1), board=chess.Board(), user_color=chess.WHITE)
    await coach.output_user_move(chess.Move.from_uci("e2e4"))
    plays = [p for p in coach.output.played if p == ("groups", [5, 2, 5, 4])]
    assert len(plays) == 2  # replayed once


async def test_move_bypass():
    coach = make_coach(["move:g8f6"], board=chess.Board("rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"), user_color=chess.WHITE)
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("g8f6")


async def test_activity_log_and_snapshot():
    events = groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK)
    move = await coach.input_opponent_move()
    coach.board.push(move)

    kinds = [(e["kind"], e["detail"]) for e in coach.log]
    assert ("squeeze_group", "5") in kinds
    assert ("squeeze_group", "2") in kinds
    assert ("buzz_groups", "5-2-5-4") in kinds  # the echo
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
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, practice=True, initial_color=chess.BLACK)
    events = (
        groups(5, 2, 5, 4) + groups(1)   # enter e2e4 + confirm (correct)
        + groups(1)                      # ack the e5 recommendation
        + groups(7, 1, 6, 3) + groups(1) # enter g1f3 + confirm (wrong!)
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
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, practice=True, initial_color=chess.BLACK)
    events = (
        groups(7, 1, 6, 3) + groups(2)   # wrong g1f3, reject echo
        + groups(5, 2, 5, 4) + groups(1) # correct e2e4, confirm
    )
    coach = ChessCoach(ScriptedInput(events), ScriptedOutput(), ScriptedEngine(["e2e4"]), cfg)
    task = __import__("asyncio").ensure_future(coach.run_session())
    try:
        await task
    except (AssertionError, IndexError):  # script/engine exhausted after the correct entry
        pass
    assert [m.uci() for m in coach.board.move_stack] == ["e2e4"]
    assert not [e for e in coach.log if e["kind"] == "practice_fail"]
    task.cancel()


async def test_practice_snapshot_exposes_expected_move():
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, practice=True, initial_color=chess.BLACK)
    coach = ChessCoach(ScriptedInput([]), ScriptedOutput(), ScriptedEngine(["e2e4"]), cfg)
    coach.expected_move = chess.Move.from_uci("e2e4")
    coach.expected_san = "e4"
    snap = coach.snapshot()
    assert snap["practice"] is True
    assert snap["expected_move"] == {"uci": "e2e4", "san": "e4"}


async def test_oracle_accepts_second_guess():
    # long squeeze -> oracle buzzes e2e4 (reject: 2) then d2d4 (accept: 1)
    events = ["long"] + groups(2) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["e2e4", "d2d4", "g1f3"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("d2d4")
    played = coach.output.played
    assert ("groups", [5, 2, 5, 4]) in played  # first guess buzzed
    assert ("groups", [4, 2, 4, 4]) in played  # second guess buzzed


async def test_oracle_bail_to_manual():
    # long -> guess buzzed -> long again bails -> manual 4-group entry works
    events = ["long"] + ["long"] + groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["d2d4"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")


async def test_oracle_exhausted_falls_back():
    # reject both guesses -> error signal -> manual entry
    events = ["long"] + groups(2) + groups(2) + groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["d2d4", "g1f3"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played


async def test_long_after_shorts_still_cancels_not_oracle():
    # shorts already squeezed: long = cancel (error), NOT an oracle request
    events = ["short", "short", "long"] + groups(5, 2, 5, 4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK, ranked=["d2d4"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("signal", "error") in coach.output.played
    assert not [e for e in coach.log if e["kind"] == "oracle"]


async def test_oracle_answer_interrupts_playback():
    # answers arrive while the guess is still buzzing: playback is cut short
    events = ["long"] + groups(2) + groups(1)  # oracle; next; accept
    coach = ChessCoach(ScriptedInput(events), SlowOutput(), ScriptedEngine([], ranked=["e2e4", "d2d4"]), CFG)
    coach.board = __import__("chess").Board()
    coach.user_color = chess.BLACK
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("d2d4")
    played = coach.output.played
    assert played.count(("stop",)) == 2  # both guesses were interrupted
    assert ("groups", [5, 2, 5, 4]) in played and ("groups", [4, 2, 4, 4]) in played
    assert [e["detail"] for e in coach.log if e["kind"] == "oracle"].count("interrupted") == 2


async def test_oracle_slide_file():
    # guessed d2d4, actual e2e4: answer 4 (toward h) slides the move over
    events = ["long"] + groups(4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["d2d4", "g1f3"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")
    assert ("groups", [4, 2, 4, 4]) in coach.output.played  # original guess
    assert ("groups", [5, 2, 5, 4]) in coach.output.played  # slid guess


async def test_oracle_slide_composes():
    # two files off: 4, 4 walks c4 -> d4 -> e4
    events = ["long"] + groups(4) + groups(4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["c2c4"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")


async def test_oracle_slide_off_board_errors_and_reoffers():
    # guessed h2h4: sliding toward h falls off the board -> error, same guess again
    events = ["long"] + groups(4) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["h2h4"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("h2h4")
    assert ("signal", "error") in coach.output.played


async def test_oracle_keep_from_square():
    # guessed e2e3 but the pawn went two squares: 5 = re-guess from e2
    events = ["long"] + groups(5) + groups(1)
    coach = make_coach(events, board=chess.Board(), user_color=chess.BLACK,
                       ranked=["e2e3", "d2d4", "e2e4", "g1f3"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("e2e4")  # d2d4 skipped: wrong from-square


async def test_oracle_keep_to_square():
    # two knights can reach d2; guessed the wrong one: 6 = re-guess to d2,
    # skipping ranked moves to other squares
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1")
    events = ["long"] + groups(6) + groups(1)
    coach = make_coach(events, board=board, user_color=chess.BLACK,
                       ranked=["b1d2", "a2a3", "f3d2"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("f3d2")  # a2a3 skipped: wrong to-square


async def test_oracle_guess_includes_promotion():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    events = ["long"] + groups(1)  # accept first guess
    coach = make_coach(events, board=board, user_color=chess.BLACK, ranked=["a7a8q"])
    move = await coach.input_opponent_move()
    assert move == chess.Move.from_uci("a7a8q")
    assert ("groups", [1]) in coach.output.played  # promotion count group buzzed


async def test_board_ack_correct_click():
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, board_ack=True)
    coach = make_coach(["board:e2e4"], board=chess.Board(), user_color=chess.WHITE, cfg=cfg)
    await coach.output_user_move(chess.Move.from_uci("e2e4"))
    kinds = [(e["kind"], e["detail"]) for e in coach.log]
    assert ("board_ack", "read correctly: e4") in kinds
    # the recommendation is redacted in the log but still buzzed for real
    assert ("engine", "recommend: ●●● (read the buzz)") in kinds
    assert ("buzz_groups", "●-●") in kinds
    assert ("groups", [5, 2, 5, 4]) in coach.output.played


async def test_board_ack_wrong_click_replays():
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, board_ack=True)
    coach = make_coach(["board:d2d4", "board:e2e4"], board=chess.Board(), user_color=chess.WHITE, cfg=cfg)
    await coach.output_user_move(chess.Move.from_uci("e2e4"))
    assert ("signal", "error") in coach.output.played
    assert [e for e in coach.log if e["kind"] == "read_fail" and "d2d4" in e["detail"]]
    buzzes = [p for p in coach.output.played if p == ("groups", [5, 2, 5, 4])]
    assert len(buzzes) == 2  # replayed after the wrong click


async def test_board_ack_squeeze_does_not_ack():
    cfg = CoachConfig(skip_calibration=True, attention_pause_s=0.0, board_ack=True)
    events = ["short", None, "board:e2e4"]
    coach = make_coach(events, board=chess.Board(), user_color=chess.WHITE, cfg=cfg)
    await coach.output_user_move(chess.Move.from_uci("e2e4"))
    assert ("signal", "error") in coach.output.played  # squeeze rebuffed
    assert [e for e in coach.log if e["kind"] == "board_ack"]


async def test_calibration_sets_span():
    coach = make_coach([], board=chess.Board())
    await coach.calibrate()
    assert coach.input.calibration == (100.0, 700.0)
