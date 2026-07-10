# Haptic Chess Protocol

How moves travel between the player and the machine, in both directions, using
only squeezes (kGoal Boost pressure sensor) and buzzes (Lovense Hush 2).

## Alphabet

**Input (squeezes)**
- **short**: squeeze held < 1 s
- **long**: squeeze held ≥ 1 s
- **group**: consecutive shorts; a pause ≥ 1.5 s closes the group
- a **long squeeze during message entry cancels** the current message (error
  buzz confirms the cancel; start over)
- a **long squeeze on an *empty* message starts the oracle** (see below)

**Output (buzzes)**
- **dot**: 200 ms buzz · **dash**: 600 ms buzz
- gap within a group: 250 ms · gap between groups: 900 ms
- count groups are all dots; **reserved signals always contain a dash** (or are
  a single long low-intensity buzz), so a signal can never be miscounted as a
  count group

All timings and intensities are Viam config attributes.

## Signals (Hush → player)

| signal | pattern | meaning |
|---|---|---|
| ready | `-.-` | session starting |
| calibrate: relax | `...` | relax for 3 s |
| calibrate: squeeze | `-` | squeeze max for 3 s |
| ack | `..` | understood / calibration OK |
| error | one long low buzz | invalid input, try again |
| attention | `--` | recommended move follows |
| promotion | `-.-` (after a move) | promotion piece query |
| check | 3 rapid high dots | the conveyed move gives check |
| win | `---` high intensity | you won |
| loss | one 2 s buzz | you lost |
| draw | `-.-.` | draw |

## Count encodings

- **file**: 1–8 = a–h · **rank**: 1–8
- **promotion answer**: 1=queen 2=knight 3=rook 4=bishop
- **yes/no**: 1 short = yes/confirm · 2 shorts = no/reject

## Session flow

1. **Ready** signal plays.
2. **Calibration**: relax signal → stay relaxed 3 s → squeeze signal → squeeze
   hard 3 s → ack. If the measured span is too small, error and repeat.
3. **Color select**: squeeze 1 short = you play white, 2 shorts = black.
   The count is echoed back; confirm 1 / reject 2.

## Move messages

A move is **four count groups**: the from-square then the to-square, each as
`file · rank` (the same order you read the squares: e2e4 = `5 · 2 · 5 · 4`).
The message completes on the fourth group. A from/to pair identifies exactly
one move, so there is never any ambiguity to resolve.

- **Castling** is the king's two-square move: `5·1·7·1` = e1g1 = O-O for
  white; `5·8·3·8` = e8c8 = O-O-O for black.
- **En passant** is the capturing pawn's from/to (e.g. `5·5·4·6` = e5xd6 ep).
- **Promotion**: all promotion pieces share the same from/to, so the move is
  entered/output normally and the piece is resolved by a follow-up query.

### Opponent move (player → machine)

1. Squeeze the four groups.
2. The machine validates against the legal moves of the current position:
   - **no legal match** (empty from-square, illegal destination, out-of-range
     count…) → error signal, start over
   - **exactly one match** (always, when legal) → the machine echoes the four
     groups back; confirm 1 / reject 2
3. If the move is a pawn reaching the last rank: promotion signal, answer with
   one group (1=Q 2=N 3=R 4=B).

### Oracle shortcut (machine guesses)

Instead of entering the move, squeeze **one long** before anything else. The
machine ranks the legal moves with the engine and buzzes its best guess as a
normal 4-group move (with promotion group if applicable). Answer with one
group:

| answer | meaning |
|---|---|
| 1 | that's the move — entered (the guess buzz was the echo) |
| 2 | next guess |
| 3 | close! same move slid **one file toward the a-file** |
| 4 | close! same move slid **one file toward the h-file** |
| 5 | the **from-square is right** — re-guess moves from there |
| 6 | the **to-square is right** — re-guess moves landing there |
| long / timeout | give up, enter manually |

Edits chain: a slid or re-guessed move buzzes like any guess and takes the
same answers (slide twice for two files, etc.). An impossible edit (slide off
the board / no other move from that square) error-buzzes and re-offers the
current guess. You can answer **while the guess is still buzzing** — your
first squeeze cuts playback short. After the guess pool runs dry it
error-buzzes and falls back to manual entry. In the opening the opponent's
move is usually the first or second guess, making a typical entry one long +
one short.

### Recommended move (machine → player)

1. Attention signal, a distinct pause (~1.5 s, `attention_pause_ms`), then
   the four count groups (from-square, to-square).
2. If a promotion: promotion signal + one count group for the piece.
3. If the move gives check: check signal (a nice confidence check).
4. Play the move on the real board, then squeeze 1 short to acknowledge —
   or one long squeeze to have the whole thing replayed.

**Blind-read mode** (`board_ack`, toggled from the dashboard): the
recommendation is hidden from the dashboard and squeeze-ack is disabled —
you must prove you read the buzz by clicking the move on the dashboard board
(from-square, then to-square). A wrong click error-buzzes and replays the
recommendation; a correct click plays the move and reveals it in the feed.
Long squeeze still replays on demand.

## Online relay mode

With `relay_mode` on there is no engine: the machine is a pure haptic relay
for a real (online) game. The opponent's move is entered on the dashboard
board and **delivered to you** exactly like a recommendation (attention →
pause → four groups → check signal; 1 short = got it, long = replay). Your
reply is a normal squeezed move message (oracle disabled — no engine, no
assistance) and is shown on the dashboard for a helper to play online — or,
with `lichess_token` set, exchanged directly with lichess.org over the Board
API (opponent moves stream in and buzz automatically; your moves are posted
back; off-board endings buzz win/loss/draw).

## Game end

Win / loss / draw signal (from your color's perspective), then the session
goes idle. Reset via the chess-coach service's `reset` do_command.

## Future extensions (reserved)

- Morse output mode (`output_encoding: "morse"`): squares as morse characters.
