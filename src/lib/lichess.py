"""Minimal Lichess Board API client for relay mode.

The Board API (https://lichess.org/api#tag/Board) is Lichess's sanctioned
interface for physical/alternative boards playing as a normal human account:
stream your games, receive moves, post moves. Requires a personal access
token with the `board:play` scope (create one at
https://lichess.org/account/oauth/token).
"""

from __future__ import annotations

import json

import httpx


class LichessBridge:
    def __init__(self, token: str, base_url: str = "https://lichess.org", transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(15, read=None),  # streams idle between keepalives
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def stream(self, path: str):
        """Yield parsed objects from an NDJSON stream (skipping keepalive blanks)."""
        async with self._client.stream("GET", path) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if line:
                    yield json.loads(line)

    def stream_events(self):
        return self.stream("/api/stream/event")

    def stream_game(self, game_id: str):
        return self.stream(f"/api/board/game/stream/{game_id}")

    async def post_move(self, game_id: str, uci: str) -> bool:
        resp = await self._client.post(f"/api/board/game/{game_id}/move/{uci}")
        return resp.status_code == 200


def opponent_moves_to_apply(moves: list[str], applied: int, our_color_is_white: bool) -> tuple[list[str], int]:
    """Given the full UCI move list from a game stream and a cursor of moves
    already handled, return the opponent's not-yet-applied moves (our own
    echoed moves are skipped by ply parity: even index = white's move) and
    the new cursor."""
    out = [
        moves[i]
        for i in range(applied, len(moves))
        if (i % 2 == 0) != our_color_is_white
    ]
    return out, len(moves)
