import chess
import pytest

from src.lib import encoding


def test_pawn_push_groups():
    board = chess.Board()
    assert encoding.move_to_groups(board, chess.Move.from_uci("e2e4")) == [1, 5, 4]


def test_knight_move_groups():
    board = chess.Board()
    assert encoding.move_to_groups(board, chess.Move.from_uci("g1f3")) == [2, 6, 3]


def test_unambiguous_decode():
    board = chess.Board()
    cands = encoding.candidates(board, 1, 5, 4)  # pawn to e4
    assert len(cands) == 1
    assert cands[0].move() == chess.Move.from_uci("e2e4")


def test_knight_ambiguity():
    # white knights on b1 and f3 can both reach the empty d2 square
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1")
    cands = encoding.candidates(board, 2, 4, 2)
    assert [chess.square_name(c.from_square) for c in cands] == ["b1", "f3"]  # file-major order
    assert encoding.needs_origin_disambiguation(board, chess.Move.from_uci("f3d2"))


def test_pawn_capture_ambiguity():
    # white pawns on d4 and f4 can both capture e5
    board = chess.Board("k7/8/8/4p3/3P1P2/8/8/K7 w - - 0 1")
    cands = encoding.candidates(board, 1, 5, 5)
    assert [chess.square_name(c.from_square) for c in cands] == ["d4", "f4"]


def test_promotion_collapses_to_one_candidate():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    cands = encoding.candidates(board, 1, 1, 8)
    assert len(cands) == 1
    assert cands[0].needs_promotion
    assert cands[0].move(chess.KNIGHT) == chess.Move.from_uci("a7a8n")


def test_castling_is_king_to_g1():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    move = chess.Move.from_uci("e1g1")
    assert move in board.legal_moves
    assert encoding.move_to_groups(board, move) == [6, 7, 1]
    cands = encoding.candidates(board, 6, 7, 1)
    assert len(cands) == 1
    assert cands[0].move() == move


def test_en_passant():
    board = chess.Board("k7/8/8/3pP3/8/8/8/K7 w - d6 0 2")
    cands = encoding.candidates(board, 1, 4, 6)
    assert len(cands) == 1
    assert board.is_en_passant(cands[0].move())


def test_illegal_message_has_no_candidates():
    board = chess.Board()
    assert encoding.candidates(board, 5, 4, 5) == []  # queen to d5 from start: illegal
    assert encoding.candidates(board, 7, 4, 5) == []  # no such piece count
    assert encoding.candidates(board, 1, 9, 5) == []  # file out of range


def test_encode_move_with_check_and_origin():
    # rooks on a1/h1 with nothing between them: both reach d1, and Rd1+ checks d8
    board = chess.Board("3k4/8/8/8/8/8/4K3/R6R w - - 0 1")
    move = chess.Move.from_uci("a1d1")  # rook a1 or h1 to d1: ambiguous, gives check
    enc = encoding.encode_move(board, move)
    assert enc.groups == [4, 4, 1]
    assert enc.origin_groups == [1, 1]
    assert enc.gives_check
    assert enc.all_groups() == [[4, 4, 1], [1, 1]]


def test_encode_promotion_move():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    enc = encoding.encode_move(board, chess.Move.from_uci("a7a8q"))
    assert enc.groups == [1, 1, 8]
    assert enc.promotion_count == 1
    assert enc.all_groups()[-1] == [1]
