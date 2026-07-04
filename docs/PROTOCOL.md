# Haptic Chess Protocol

How moves travel between the player and the machine, in both directions, using
only squeezes (kGoal Boost pressure sensor) and buzzes (Lovense Hush 2).

## Alphabet

**Input (squeezes)**
- **short**: squeeze held < 500 ms
- **long**: squeeze held ≥ 500 ms
- **group**: consecutive shorts; a pause ≥ 1.5 s closes the group
- a **long squeeze during message entry cancels** the current message (error
  buzz confirms the cancel; start over)

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
| ambiguity | `---` | several origins match; menu follows |
| promotion | `-.-` (after a move) | promotion piece query |
| check | 3 rapid high dots | the conveyed move gives check |
| win | `---` high intensity | you won |
| loss | one 2 s buzz | you lost |
| draw | `-.-.` | draw |

## Count encodings

- **piece**: 1=pawn 2=knight 3=bishop 4=rook 5=queen 6=king
- **file**: 1–8 = a–h · **rank**: 1–8
- **promotion answer**: 1=queen 2=knight 3=rook 4=bishop
- **yes/no**: 1 short = yes/confirm/this-one · 2 shorts = no/reject/next

## Session flow

1. **Ready** signal plays.
2. **Calibration**: relax signal → stay relaxed 3 s → squeeze signal → squeeze
   hard 3 s → ack. If the measured span is too small, error and repeat.
3. **Color select**: squeeze 1 short = you play white, 2 shorts = black.
   The count is echoed back; confirm 1 / reject 2.

## Move messages

A move is **three count groups**: `piece · destination-file · destination-rank`.
Example: knight to f3 = `2 · 6 · 3`. Castling is entered/output as the king
moving two squares (e.g. `6 · 7 · 1` = O-O for white). En passant is just the
pawn to its destination.

### Opponent move (player → machine)

1. Squeeze the three groups.
2. The machine decodes against the legal moves of the current position:
   - **no match** → error signal, start over
   - **one match** → the machine echoes the three groups; confirm 1 / reject 2
   - **several matches** (two knights, multiple pawn captures…) → ambiguity
     signal, then the machine buzzes each candidate's **origin square**
     (file group · rank group) one at a time: 1 = that one, 2 = next
     (wraps around), long = cancel
3. If the move is a pawn reaching the last rank: promotion signal, answer with
   one group (1=Q 2=N 3=R 4=B).

### Recommended move (machine → player)

1. Attention signal, then the three count groups.
2. If piece+destination is ambiguous on the current board, the **origin**
   file/rank groups are appended.
3. If a promotion: promotion signal + one count group for the piece.
4. If the move gives check: check signal (a nice confidence check).
5. Play the move on the real board, then squeeze 1 short to acknowledge —
   or one long squeeze to have the whole thing replayed.

## Game end

Win / loss / draw signal (from your color's perspective), then the session
goes idle. Reset via the chess-coach service's `reset` do_command.

## Future extensions (reserved)

- Opening-book shortcuts: a long squeeze *starting* a message switches to book
  mode with short Huffman codes for common openings.
- Morse output mode (`output_encoding: "morse"`): piece by first letter
  (P/N/B/R/Q/K), squares as morse characters.
