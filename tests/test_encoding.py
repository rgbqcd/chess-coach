import chess

from src.lib import encoding


def test_pawn_push_groups():
    assert encoding.move_to_groups(chess.Move.from_uci("e2e4")) == [5, 2, 5, 4]


def test_knight_move_groups():
    assert encoding.move_to_groups(chess.Move.from_uci("g1f3")) == [7, 1, 6, 3]


def test_decode_pawn_push():
    board = chess.Board()
    cands = encoding.candidates(board, 5, 2, 5, 4)  # e2 -> e4
    assert len(cands) == 1
    assert cands[0].move() == chess.Move.from_uci("e2e4")


def test_from_square_makes_knights_unambiguous():
    # both knights (b1, f3) can reach d2, but the from-square pins it down
    board = chess.Board("rnbqkbnr/pppppppp/8/8/8/5N2/PPP1PPPP/RNBQKB1R w KQkq - 0 1")
    b1 = encoding.candidates(board, 2, 1, 4, 2)  # b1d2
    f3 = encoding.candidates(board, 6, 3, 4, 2)  # f3d2
    assert [c.move().uci() for c in b1] == ["b1d2"]
    assert [c.move().uci() for c in f3] == ["f3d2"]


def test_promotion_collapses_to_one_candidate():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    cands = encoding.candidates(board, 1, 7, 1, 8)  # a7 -> a8
    assert len(cands) == 1
    assert cands[0].needs_promotion
    assert cands[0].move(chess.KNIGHT) == chess.Move.from_uci("a7a8n")


def test_castling_is_king_two_squares():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    move = chess.Move.from_uci("e1g1")
    assert encoding.move_to_groups(move) == [5, 1, 7, 1]
    cands = encoding.candidates(board, 5, 1, 7, 1)
    assert len(cands) == 1
    assert cands[0].move() == move
    assert board.is_castling(cands[0].move())


def test_en_passant():
    board = chess.Board("k7/8/8/3pP3/8/8/8/K7 w - d6 0 2")
    cands = encoding.candidates(board, 5, 5, 4, 6)  # e5 -> d6
    assert len(cands) == 1
    assert board.is_en_passant(cands[0].move())


def test_illegal_messages_have_no_candidates():
    board = chess.Board()
    assert encoding.candidates(board, 5, 2, 5, 5) == []  # e2e5: too far
    assert encoding.candidates(board, 5, 3, 5, 4) == []  # e3: empty from-square
    assert encoding.candidates(board, 5, 7, 5, 5) == []  # e7e5: not your piece
    assert encoding.candidates(board, 9, 2, 5, 4) == []  # file out of range
    assert encoding.candidates(board, 5, 2, 5, 0) == []  # rank out of range


def test_encode_move_with_check():
    board = chess.Board("3k4/8/8/8/8/8/4K3/R6R w - - 0 1")
    enc = encoding.encode_move(board, chess.Move.from_uci("a1d1"))
    assert enc.groups == [1, 1, 4, 1]
    assert enc.gives_check
    assert enc.all_groups() == [[1, 1, 4, 1]]


def test_encode_promotion_move():
    board = chess.Board("8/P7/8/8/8/8/8/k1K5 w - - 0 1")
    enc = encoding.encode_move(board, chess.Move.from_uci("a7a8q"))
    assert enc.groups == [1, 7, 1, 8]
    assert enc.promotion_count == 1
    assert enc.all_groups() == [[1, 7, 1, 8], [1]]
