<img width="1111" height="595" alt="Screenshot 2026-07-04 at 18 56 06" src="https://github.com/user-attachments/assets/ca9d7e0e-1044-4101-927a-d554e6e43996" />



# Chess Playing Butt Plug System

A chess assistant with a fully haptic interface. You play over-the-board chess
against a human opponent; this system is your silent coach:

- You **squeeze the opponent's moves in** on a Minna kGoal Boost (a bluetooth
  kegel trainer with a pressure sensor).
- Stockfish computes your best reply.
- A Lovense Hush 2 (a bluetooth vibrating plug) **buzzes the recommended move
  back to you** in morse-code-like count patterns.

No screen, no camera, no hands. The intended end state is playing entirely
blind on haptics alone; a live web dashboard (below) exists for practicing and
debugging until you get there.

## The building blocks

### Hardware

- **[Minna kGoal Boost](https://www.minnalife.com/products/kgoal-boost)** — the
  input device: a bluetooth kegel trainer with a pressure sensor (0–2000
  counts, streamed over BLE). The sensor model speaks its BLE protocol
  directly, so this exact device is required for input.
- **[Lovense Hush 2](https://www.lovense.com/butt-plug/hush)** — the output
  device: a bluetooth vibrating plug. Output goes through buttplug.io, so any
  vibrator [supported by buttplug](https://iostindex.com) should work — set
  the buzzer's `device_match` attribute to its name.
- **A computer with Bluetooth LE** — both devices connect to it directly; no
  phone or vendor dongle needed. Developed and tested on macOS (Apple
  Silicon); Linux should work but is untested.
- A physical chess set and an unsuspecting opponent (not included).

### buttplug.io

[Buttplug](https://buttplug.io) is an open-source protocol and server for
controlling intimate hardware from software. Its desktop app, **Intiface
Central**, connects to devices over Bluetooth LE and exposes them to any
program through a websocket API (`ws://127.0.0.1:12345`), abstracting away
every vendor's proprietary BLE quirks. This project uses it (via the official
`buttplug` Python client, message spec v4) to drive the Hush's vibration
motor with precise timing and intensity.

The kGoal's pressure sensor is the one place we bypass buttplug: its pressure
stream is subscribe-only in spec v4 and the official Python client can't
subscribe yet, so the sensor model speaks raw BLE with
[bleak](https://github.com/hbldh/bleak) instead — using the packet format
documented in buttplug's own protocol implementation (7-byte notifications,
big-endian u16 pressure, range 0–2000).

### Viam

[Viam](https://viam.com) is an open-source robotics platform: a robot is a
`viam-server` process configured with *components* (hardware: sensors, motors,
cameras…) and *services* (logic), all exposed over a uniform gRPC API with
SDKs in many languages. Custom hardware is added through *modules* that
provide new component/service models.

This repo is one Viam module providing five models — because if a chess
computer's input is a kegel trainer and its output is a butt plug, they should
still show up in your robot config like any respectable sensor and actuator:

| model | api | what it does |
|---|---|---|
| `rgbqcd:chess-playing:kgoal-boost` | `rdk:component:sensor` | BLE pressure stream + squeeze event detection |
| `rgbqcd:chess-playing:hush-buzzer` | `rdk:component:generic` | buzz patterns: counts, morse, signals via `do_command` |
| `rgbqcd:chess-playing:chess-coach` | `rdk:service:generic` | game loop: decode squeezes, ask Stockfish, buzz the reply |
| `rgbqcd:chess-playing:fake-kgoal` | `rdk:component:sensor` | hardware-free input stand-in |
| `rgbqcd:chess-playing:fake-buzzer` | `rdk:component:generic` | hardware-free output stand-in (logs buzzes) |

The chess brain is [Stockfish](https://stockfishchess.org) over UCI via
[python-chess](https://python-chess.readthedocs.io).

## The haptic protocol

Full spec: [docs/PROTOCOL.md](docs/PROTOCOL.md). The short version:

**Squeezes (you → machine).** A squeeze under 500 ms is a *short*, over is a
*long*. Consecutive shorts form a **count group**; a pause of ~1.5 s closes
the group. A long squeeze cancels whatever you were entering (or asks for a
replay of the last recommendation).

**Buzzes (machine → you).** Dots (200 ms) and dashes (600 ms). Count groups
are all dots — you just count them. Anything containing a dash is a **signal**
(attention, error, ack, ambiguity, promotion, check, win/loss/draw), so a
status message can never be mistaken for a number.

**Moves are three count groups** in both directions:

```
piece · destination file · destination rank

piece: 1=pawn 2=knight 3=bishop 4=rook 5=queen 6=king
file:  1–8 = a–h        rank: 1–8
```

So knight to f3 is `2 · 6 · 3`; pawn to e4 is `1 · 5 · 4`. Castling is the
king moving two squares (`6 · 7 · 1` = O-O for white); en passant is just the
pawn to its destination.

**Session flow:** ready signal → calibration (relax 3 s, squeeze 3 s — sets
your personal pressure thresholds) → color select (1 short = white, 2 =
black) → game. Every move you enter is echoed back for a 1-yes/2-no
confirmation. If piece+destination is ambiguous (two knights, multiple pawn
captures), the machine buzzes each candidate's origin square and you pick with
1/2. Pawns reaching the last rank trigger a promotion query (1=Q 2=N 3=R 4=B).
When the machine recommends a move, it leads with the attention signal, buzzes
the groups, appends origin/promotion groups only when needed, adds the check
signal if the move gives check, and waits for your 1-short "I played it".

## The practice dashboard

The core experience is blind — but nobody is born fluent in kegel-to-chess
encoding. For practice and debugging there's a live web view:

```sh
uv run python scripts/dashboard.py   # then open http://localhost:8765
```

It connects to the running viam-server machine and live-updates (~3 Hz):

- **Hint banner** — what the machine expects *right now*, in plain language
  ("SQUEEZE HARD — capturing peak", "enter the opponent's move: piece · file
  · rank", "confirm the move echo: 1 = yes, 2 = no").
- **Board** — rendered from the live game state with the last move
  highlighted, plus SAN move history, your color, and whose turn it is.
- **Squeeze sensor panel** — live pressure value and a rolling 30 s trace with
  your calibrated on/off thresholds drawn in, so you can see exactly why a
  squeeze did or didn't register; squeeze indicator and battery.
- **Activity feed** — every buzz sent and squeeze decoded, timestamped:
  `squeeze 1-5-4 → buzz echo 1-5-4 → squeeze 1 → decoded: opponent e4 →
  engine: recommend e5 → buzz attention → buzz 1-5-5`.

Append `?theme=dark` or `?theme=light` to force a theme. The page is a single
self-contained HTML file served by a zero-dependency Python bridge
(`scripts/dashboard.py`) that polls the robot's gRPC API.

### AI-opponent practice mode

Toggle **practice: on** in the dashboard header (or start with
`viam-server -config viam.practice.json`, or set `practice_mode: true` on the
coach). In practice mode Stockfish plays the opponent too: its move appears in
the banner ("opponent played Nf3 — squeeze it in") with the from/to squares
outlined on the board, and you must enter it correctly with squeezes. Rejected
echoes and undecodable input just retry as usual — but **confirming a wrong
move fails the game**: you get the loss buzz, the board resets, and a new game
auto-starts (same color, no re-calibration). The dashboard tracks your
games/fails tally, and a **new game** button restarts on demand.

## Setup

Python environment (built with [uv](https://docs.astral.sh/uv/)):

```sh
uv sync                     # or ./setup.sh
brew install stockfish
```

**viam-server**:

```sh
# macOS
brew tap viamrobotics/brews && brew install viam-server

# Linux
curl https://storage.googleapis.com/packages.viam.com/apps/viam-server/viam-server-stable-x86_64 -o viam-server && chmod 755 viam-server

# or build from source: clone viamrobotics/rdk, `make server`
```

Runtime prerequisites:

- **Intiface Central** running (`ws://127.0.0.1:12345`) with the Hush
  connected — and *not* holding the kGoal (only one thing can own its BLE
  connection; disconnect "Boost" in Intiface if it grabbed it while scanning)
- kGoal Boost powered on
- macOS: the process needs Bluetooth permission — grant it to the terminal app
  that launches `viam-server` (System Settings → Privacy & Security → Bluetooth)

## Your first game

A full walkthrough, from cold start to (haptic) checkmate, using practice mode
so the dashboard can hold your hand.

### 1. One-time setup

```sh
uv sync                                            # python deps
brew install stockfish                             # the chess brain
brew tap viamrobotics/brews && brew install viam-server   # the robot server
```

Install [Intiface Central](https://intiface.com/central/) and give your
terminal app Bluetooth permission (System Settings → Privacy & Security →
Bluetooth — the first BLE scan will prompt you).

### 2. Pre-flight

1. Devices charged, powered on, and, uh, *installed*.
2. Open Intiface Central and press the big play button (engine running).
3. Devices tab → Start Scanning → wait for the Hush to appear → Stop Scanning.
   If a device called **Boost** appears, disconnect it — the kGoal must stay
   free for our direct BLE connection.
4. Test the Hush right in Intiface with its slider. If it buzzes, you're set.

### 3. Launch

Copy the practice config and point it at your checkout (`*.local.json` files
are gitignored):

```sh
cp viam.practice.json viam.practice.local.json
# edit viam.practice.local.json: set executable_path to <this repo>/run.sh
```

Then three terminals (or two and some `&`):

```sh
viam-server -config viam.practice.local.json  # the robot, practice mode on
uv run python scripts/dashboard.py            # the bridge
open http://localhost:8765                    # the dashboard
```

Wait for all four chips in the dashboard header to go green: **robot
connected · session running · kGoal connected · Hush**. The kGoal takes a few
seconds to be found over BLE; watch its pressure number come alive. The
**practice: on** button should be highlighted (it is with this config — or
toggle it on any time).

### 4. Calibration

The Hush plays the ready signal (`— · —`), then walks you through calibration:

1. **Three short buzzes** → relax completely for 3 seconds.
2. **One long buzz** → squeeze as hard as you can for 3 seconds.
3. **Two quick dots (ack)** → calibrated. (One long weak buzz instead means
   the span was too small — it will repeat the cycle; squeeze harder.)

Watch the pressure trace on the dashboard: your on/off thresholds appear as
dashed lines. Practice a few squeezes and confirm the SQUEEZING indicator and
activity feed register shorts as shorts and longs as longs.

### 5. Pick your color

Squeeze **1 short** for white or **2 shorts** for black. The Hush echoes your
count back; squeeze **1** to confirm (or **2** to redo). The banner tracks
every step of this exchange if you get lost.

### 6. Play

Say you chose white. The first thing you'll feel is the **attention signal**
(`— —`): your own recommendation follows. Count the groups — say
`· | · · · · · | · · · ·` — that's 1·5·4: **pawn to e4**. In a real game you'd
now play it on the physical board; here, just squeeze **1 short** to ack. The
move appears on the dashboard board.

Now the AI opponent moves. The banner shows it in orange — *"opponent played
e5 — squeeze it in"* — with the from/to squares outlined on the board. Encode
it yourself before peeking at the hint: pawn = **1**, e-file = **5**, rank 5 =
**5**. Squeeze `1 · 5 · 5` with ~1.5 s pauses between groups. The Hush echoes
your three groups back; if they match what you meant, confirm with **1**.

That's the whole loop: feel your move, ack it; see the opponent's move,
encode it, confirm it. Along the way you'll meet the special exchanges:

- **Ambiguity** (`— — —` after your input): two of the opponent's pieces could
  make that move. The Hush buzzes the origin square of one candidate
  (file·rank); squeeze **1** if that's the one, **2** to hear the next.
- **Promotion** (`— · —`): answer 1=Q 2=N 3=R 4=B.
- **Check** (3 rapid strong dots): appended when a received move gives check.
- Made a mess mid-entry? **One long squeeze** cancels the message (error buzz
  confirms); start the move over. During an ack wait, a long squeeze replays
  the whole recommendation instead.

### 7. Failing (the point of practice)

Enter a wrong-but-legal move and confirm it, and the game is over: you'll get
the 2-second loss buzz, the activity feed shows **FAIL** with what was
expected versus what you entered, and ~2 seconds later a fresh game starts —
same color, no re-calibration, ready signal and straight back to work. The
games/fails tally sits under the board. When you can get through whole games
without looking at the banner, you're ready to unplug the dashboard and play a
real opponent blind.

If squeezes misregister along the way (shorts reading as longs, groups
splitting), the fix is config, not practice: see the tuning attributes below.

## Try the hardware without viam-server

```sh
uv run python scripts/test_hush.py               # SOS + count groups + signals
uv run python scripts/test_kgoal.py --calibrate  # live pressure bar + squeeze events
uv run python scripts/play_cli.py                # full game at the keyboard, no devices
```

## Run the robot

```sh
viam-server -config viam.json        # real devices
viam-server -config viam.fake.json   # hardware-free (fake models, protocol testable)
```

The checked-in configs are templates: copy one to `viam.local.json` (any
`*.local.json` is gitignored) and set the module's `executable_path` to your
absolute path to this repo's `run.sh`.

The session auto-starts. While it runs, the coach service's `do_command`
offers debugging hooks: `state` (FEN, history, current phase), `reset`,
`set_board`, `simulate_squeeze` / `simulate_groups` (inject synthetic input —
the whole protocol works with zero hardware), `input_move` (UCI bypass), and
`correct_user_move` (for when you didn't play the recommendation).

Everything tunable is a config attribute (all optional except the coach's two
dependency names):

**kgoal-boost** (sensor)
| attribute | default | |
|---|---|---|
| `device_name` | `"Boost"` | BLE advertised name to scan for |
| `device_address` | `""` | connect by address instead of scanning |
| `scan_timeout_s` | `15` | BLE scan timeout per connect attempt |
| `long_press_ms` | `500` | squeeze ≥ this = long |
| `min_press_ms` | `80` | squeezes shorter than this are debounced |
| `on_fraction` / `off_fraction` | `0.35` / `0.20` | hysteresis thresholds as a fraction of calibrated span |
| `ema_alpha` | `0.02` | baseline drift tracking rate (0–1) |

**hush-buzzer** (generic component)
| attribute | default | |
|---|---|---|
| `ws_url` | `ws://127.0.0.1:12345` | Intiface Central websocket |
| `device_match` | `"Hush"` | substring of the device name to use |
| `client_name` | `"chess-playing"` | name shown in Intiface |
| `scan_seconds` | `5` | scan duration when the device isn't connected yet |
| `dot_ms` / `dash_ms` | `200` / `600` | buzz durations |
| `gap_ms` / `group_gap_ms` | `250` / `900` | within-group / between-group silence |
| `intensity` / `error_intensity` | `0.7` / `0.4` | vibration levels (0–1] |

**chess-coach** (generic service)
| attribute | default | |
|---|---|---|
| `input_sensor` / `output_buzzer` | *(required)* | names of the two components |
| `stockfish_path` | `which stockfish` | UCI engine binary |
| `engine_skill` | `5` | Stockfish Skill Level 0–20 |
| `engine_time_s` | `1.0` | think time per move |
| `practice_mode` | `false` | AI opponent mode (see above) |
| `practice_restart_delay_s` | `2` | pause before the next practice game |
| `group_gap_ms` | `1500` | input pause that closes a count group |
| `message_timeout_s` | `45` | max wait between groups of one message |
| `confirm_timeout_s` | `30` | wait for confirm/ack answers |
| `capture_seconds` | `3` | length of each calibration capture |
| `min_calibration_span` | `100` | required relaxed→squeezed pressure span |
| `input_poll_ms` | `100` | how often the coach polls the sensor for events |
| `auto_start` | `true` | start a session on boot |
| `skip_calibration` | `false` | skip the calibration phase |

## Tests

```sh
uv run pytest
```

Covers squeeze detection (drift, debounce, hysteresis, short/long boundaries),
the move codec (knight ambiguity, pawn-capture ambiguity, promotion, castling,
en passant), and the full game loop (fool's mate, disambiguation exchange,
cancel/replay, promotion query, activity log).
