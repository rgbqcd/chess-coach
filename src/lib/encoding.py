"""Move <-> count-group codec.

A move is conveyed as three count groups: piece type, destination file,
destination rank (see docs/PROTOCOL.md). Decoding matches groups against the
board's legal moves; ambiguity (several origins for the same piece+destination)
is surfaced as a candidate list for the disambiguation exchange. Promotion
variants of one from/to pair collapse into a single pending-promotion
candidate resolved by a follow-up group.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

PIECE_COUNTS = {
    chess.PAWN: 1,
    chess.KNIGHT: 2,
    chess.BISHOP: 3,
    chess.ROOK: 4,
    chess.QUEEN: 5,
    chess.KING: 6,
}
COUNT_PIECES = {v: k for k, v in PIECE_COUNTS.items()}

PROMO_COUNTS = {1: chess.QUEEN, 2: chess.KNIGHT, 3: chess.ROOK, 4: chess.BISHOP}
PROMO_TO_COUNT = {v: k for k, v in PROMO_COUNTS.items()}


@dataclass
class Candidate:
    """One decodable interpretation of a piece/file/rank message."""

    from_square: int
    to_square: int
    needs_promotion: bool = False

    def move(self, promotion: int | None = None) -> chess.Move:
        return chess.Move(self.from_square, self.to_square, promotion=promotion)

    def origin_groups(self) -> list[int]:
        return [chess.square_file(self.from_square) + 1, chess.square_rank(self.from_square) + 1]


@dataclass
class MoveEncoding:
    """Everything the buzzer must convey for one recommended move."""

    groups: list[int]  # piece, to-file, to-rank
    origin_groups: list[int] | None = None  # appended when ambiguous on this board
    promotion_count: int | None = None
    gives_check: bool = False

    def all_groups(self) -> list[list[int]]:
        out = [self.groups]
        if self.origin_groups:
            out.append(self.origin_groups)
        if self.promotion_count:
            out.append([self.promotion_count])
        return out


def move_to_groups(board: chess.Board, move: chess.Move) -> list[int]:
    piece_type = board.piece_type_at(move.from_square)
    if piece_type is None:
        raise ValueError(f"no piece on {chess.square_name(move.from_square)}")
    return [
        PIECE_COUNTS[piece_type],
        chess.square_file(move.to_square) + 1,
        chess.square_rank(move.to_square) + 1,
    ]


def candidates(board: chess.Board, piece_count: int, file_count: int, rank_count: int) -> list[Candidate]:
    """Legal-move interpretations of a 3-group message, promotion-collapsed,
    sorted by origin square (file-major) for a deterministic disambiguation order."""
    if piece_count not in COUNT_PIECES or not (1 <= file_count <= 8) or not (1 <= rank_count <= 8):
        return []
    piece_type = COUNT_PIECES[piece_count]
    to_square = chess.square(file_count - 1, rank_count - 1)

    by_origin: dict[int, Candidate] = {}
    for m in board.legal_moves:
        if m.to_square == to_square and board.piece_type_at(m.from_square) == piece_type:
            cand = by_origin.setdefault(m.from_square, Candidate(m.from_square, to_square))
            if m.promotion:
                cand.needs_promotion = True
    return sorted(
        by_origin.values(),
        key=lambda c: (chess.square_file(c.from_square), chess.square_rank(c.from_square)),
    )


def needs_origin_disambiguation(board: chess.Board, move: chess.Move) -> bool:
    """True if piece+destination alone would match more than one legal origin."""
    groups = move_to_groups(board, move)
    return len(candidates(board, *groups)) > 1


def encode_move(board: chess.Board, move: chess.Move) -> MoveEncoding:
    enc = MoveEncoding(groups=move_to_groups(board, move))
    if needs_origin_disambiguation(board, move):
        enc.origin_groups = [
            chess.square_file(move.from_square) + 1,
            chess.square_rank(move.from_square) + 1,
        ]
    if move.promotion:
        enc.promotion_count = PROMO_TO_COUNT[move.promotion]
    enc.gives_check = board.gives_check(move)
    return enc
