# MAX-Q 🚀

> Your AI coding agent, as a launch livestream.

A tiny always-on-top mission-stream card for macOS that mirrors your
**Claude Code** session state in real time — driven by real filesystem
hooks, not animations on a timer:

| Your agent | On the stream |
|---|---|
| working | **ASCENT → STAGE SEP → ON ORBIT** — raptor plume, MAX-Q condensation collar, hot-stage separation at 66 KM / 5,800 KM/H, then an orbital coast with day/night laps, city lights and aurora (long tasks never look static) |
| needs your permission | **HOLD** — giant amber hold, frozen T-10, pad strobes. It's waiting for *your* GO |
| you answer the prompt | **GO GIVEN** — green countdown ticks T-10…1, pad vents breathe, then ignition |
| task complete (≥3 min) | **THE CATCH** — the booster comes home and the tower's chopstick arms close around it. CAUGHT ✓ |
| tool trouble | **RUD** — fireball, debris, the feed cuts to static |
| everything quiet | night at the pad, red beacons, SCRUB CONFIRMED |

**Click the card** to jump to the terminal of the session that needs you —
the asking one is exactly the prompt waiting for input. Live ALTITUDE /
VELOCITY readouts, a FLT counter that ticks per completed task per day,
camera-cut transitions, screen shake. It floats above fullscreen apps and
is safe over light wallpapers by construction.

*Born as a desktop toy for the "my agent is waiting on a permission prompt
and I didn't notice for ten minutes" problem.*

## Install

```bash
git clone https://github.com/lilwan345/maxq.git && cd maxq
pip install PySide6          # pyobjc optional (over-fullscreen support)
./install_hooks.sh           # wires Claude Code hooks (backs up settings.json)
./run.sh
```

Requirements: macOS, Python 3.10+, [Claude Code](https://claude.com/claude-code).
Restart your Claude Code sessions after installing the hooks.

Menu-bar rocket icon → **Tour mode** (auto-cycles every state), Force state,
Live. Drag to move (position persists). A task has to run at least
`WISP_DONE_MIN_SEC` seconds (default 180, read by the hook) to end in the
catch celebration.

## How it works

- Claude Code hooks (`hooks/write_state.sh`) write one tiny JSON per
  session to `~/.ai-desktop-toy/sessions/`; the card aggregates them by
  priority (asking > working > done > idle). No API calls, no polling
  your account, nothing leaves your machine.
- All vehicle/tower/Earth art is **baked by `bake_vehicle.py`** — a pure
  numpy/PIL pipeline (stainless shading, weld seams, heat-shield tiles,
  fractal continents). No AI-generated images. Re-run it to restyle.
- Flame, smoke, telemetry and transitions are procedural and live.
- Click-to-focus: Terminal.app and iTerm2 jump to the exact tab;
  VS Code raises the right window.

## Notes

- **The Boss Cam** in this build is a procedural cartoon. (Demo videos
  used public-domain NASA/USAF photos, which are not distributed here.)
- Fan art: this is an unaffiliated homage. No SpaceX marks or logos are
  used or implied.

## Related

[Beacon](https://github.com/lilwan345/beacon) reads the same session files
and shows them as a calm lighthouse diorama instead of a launch stream.

MIT — see LICENSE.
