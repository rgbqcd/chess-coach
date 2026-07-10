"""Lichess bridge tests — no network: httpx.MockTransport plays the server."""

import httpx

from src.lib.lichess import LichessBridge, opponent_moves_to_apply


def test_opponent_moves_parity_white():
    # we are white: even-index moves are ours (echoes), odd are the opponent's
    moves = ["e2e4", "e7e5", "g1f3", "b8c6"]
    inject, applied = opponent_moves_to_apply(moves, 0, our_color_is_white=True)
    assert inject == ["e7e5", "b8c6"]
    assert applied == 4


def test_opponent_moves_parity_black_with_cursor():
    moves = ["e2e4", "e7e5", "g1f3"]
    inject, applied = opponent_moves_to_apply(moves, 2, our_color_is_white=False)
    assert inject == ["g1f3"]
    assert applied == 3
    # nothing new -> nothing to inject
    inject, applied = opponent_moves_to_apply(moves, applied, our_color_is_white=False)
    assert inject == []


def make_bridge(handler):
    return LichessBridge("test-token", transport=httpx.MockTransport(handler))


async def test_stream_game_parses_ndjson_and_skips_keepalives():
    body = (
        b'{"type":"gameFull","state":{"moves":"e2e4","status":"started"}}\n'
        b"\n"  # keepalive blank line
        b'{"type":"gameState","moves":"e2e4 e7e5","status":"started"}\n'
    )

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.path == "/api/board/game/stream/abc123"
        return httpx.Response(200, content=body)

    bridge = make_bridge(handler)
    msgs = [msg async for msg in bridge.stream_game("abc123")]
    await bridge.close()
    assert [m["type"] for m in msgs] == ["gameFull", "gameState"]
    assert msgs[1]["moves"] == "e2e4 e7e5"


async def test_post_move():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        if "bad" in str(request.url):
            return httpx.Response(400, json={"error": "Not your turn"})
        return httpx.Response(200, json={"ok": True})

    bridge = make_bridge(handler)
    assert await bridge.post_move("abc123", "e2e4") is True
    assert await bridge.post_move("abc123", "badmove") is False
    await bridge.close()
    assert seen[0] == ("POST", "/api/board/game/abc123/move/e2e4")
