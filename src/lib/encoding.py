"""Move <-> count-group codec.

A move is conveyed as four count groups: from-file, from-rank, to-file,
to-rank (UCI order: e2e4 = 5·2·5·4; see docs/PROTOCOL.md). A from/to pair
uniquely identifies a move, so decoding never needs disambiguation — a
message either matches exactly one legal move or nothing. Promotion variants
of one from/to pair collapse into a single pending-promotion candidate
resolved by a follow-up group.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

PROMO_COUNTS = {1: chess.QUEEN, 2: chess.KNIGHT, 3: chess.ROOK, 4: chess.BISHOP}
PROMO_TO_COUNT = {v: k for k, v in PROMO_COUNTS.items()}


@dataclass
class Candidate:
    """The decoded interpretation of a from/to message."""

    from_square: int
    to_square: int
    needs_promotion: bool = False

    def move(self, promotion: int | None = None) -> chess.Move:
        return chess.Move(self.from_square, self.to_square, promotion=promotion)


@dataclass
class MoveEncoding:
    """Everything the buzzer must convey for one recommended move."""

    groups: list[int]  # from-file, from-rank, to-file, to-rank
    promotion_count: int | None = None
    gives_check: bool = False

    def all_groups(self) -> list[list[int]]:
        out = [self.groups]
        if self.promotion_count:
            out.append([self.promotion_count])
        return out


def square_groups(square: int) -> list[int]:
    return [chess.square_file(square) + 1, chess.square_rank(square) + 1]


def move_to_groups(move: chess.Move) -> list[int]:
    return square_groups(move.from_square) + square_groups(move.to_square)


def candidates(
    board: chess.Board, from_file: int, from_rank: int, to_file: int, to_rank: int
) -> list[Candidate]:
    """Legal-move interpretations of a 4-group message: an empty list or a
    single candidate (promotion variants collapsed)."""
    for count in (from_file, from_rank, to_file, to_rank):
        if not 1 <= count <= 8:
            return []
    from_square = chess.square(from_file - 1, from_rank - 1)
    to_square = chess.square(to_file - 1, to_rank - 1)

    cand: Candidate | None = None
    for m in board.legal_moves:
        if m.from_square == from_square and m.to_square == to_square:
            if cand is None:
                cand = Candidate(from_square, to_square)
            if m.promotion:
                cand.needs_promotion = True
    return [cand] if cand else []


def encode_move(board: chess.Board, move: chess.Move) -> MoveEncoding:
    enc = MoveEncoding(groups=move_to_groups(move))
    if move.promotion:
        enc.promotion_count = PROMO_TO_COUNT[move.promotion]
    enc.gives_check = board.gives_check(move)
    return enc
