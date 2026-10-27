# MAX-Q

> Your AI coding agent, as a rocket-launch livestream.

When Claude Code stops to ask for permission, it just waits. If you're in
another window, you might not notice for ten minutes. MAX-Q is a small card
that floats above your other windows on macOS and shows what your agent is
doing as a launch broadcast, so you can tell at a glance when it needs you.

![The six MAX-Q states: ascent, hold, caught, anomaly, pad ready and standby](assets/states.png)

## What each scene means

| Your agent is... | The card shows |
|---|---|
| **working** | **Ascent.** The rocket lifts off and climbs. The longer the task runs, the higher it goes, until the sky turns to space. |
| **waiting for your permission** | **Hold.** The countdown freezes at T-10 under a big amber HOLD: the launch is waiting for your "go". This is the one that matters. |
| **back to work after you answer** | **Go given.** The countdown runs from 10 to 1, then the rocket lifts off. |
| **done with a long task** (3+ minutes) | **The catch.** The booster flies home and the launch tower's arms catch it. CAUGHT ✓ |
| **finished, nothing new yet** | **Pad ready.** The rocket sits on the pad at golden hour. |
| **no session running** | **Standby.** Night at the pad, red lights blinking. |

There is also an **anomaly** scene (an explosion, then back to the pad). You
see it in tour mode or by forcing it from the menu. Claude Code's hooks don't
give a reliable "something went wrong" signal, so MAX-Q never shows it on its
own; a false alarm would be worse than none.

**Click the card** to jump to the terminal of the session that needs you.

## Install

```bash
git clone https://github.com/lilwan345/maxq.git && cd maxq
pip install PySide6          # pyobjc is optional: it lets the card float over fullscreen apps
./install_hooks.sh           # connects MAX-Q to Claude Code (backs up settings.json first)
./run.sh
```

Requirements: macOS, Python 3.10+, [Claude Code](https://claude.com/claude-code).
Restart your Claude Code sessions after installing the hooks.

Use the **rocket icon in the menu bar** for tour mode (plays every scene in
turn), forcing one scene, or going back to live. Drag the card to move it; it
remembers where you put it. A task counts as "long" after 180 seconds; set
`WISP_DONE_MIN_SEC` to change that.

## How it works

1. **Claude Code hooks** (`hooks/write_state.sh`) run on events like "user sent
   a prompt", "tool used", "needs permission" and "stopped", and each writes
   one tiny JSON file per session to `~/.ai-desktop-toy/sessions/`.
2. **The card** (`maxq.py`) reads those files twice a second. With several
   sessions open it shows the most urgent one: waiting for you beats working,
   which beats done and idle.
3. **A false-alarm guard:** if the session's transcript was written *after*
   the permission request, you already answered it, so the card stops showing
   Hold.

Nothing leaves your machine: no API calls and no network access.

All the rocket, tower and Earth art is drawn by `bake_vehicle.py` with
NumPy and Pillow (no AI-generated images). Flames, smoke and the telemetry
numbers are drawn live each frame.

## Files

```
maxq.py            the card: state detection, scenes, menu-bar icon
hooks/             write_state.sh, the Claude Code hook
install_hooks.sh   adds the hook to ~/.claude/settings.json
bake_vehicle.py    draws the rocket, tower and Earth sprites into assets/vehicle/
run.sh             launcher
```

## Notes

- A fan homage to rocket launch streams, not affiliated with any company. No
  real logos are used. The "boss cam" in the corner is a drawn cartoon, not a
  real person.
- [Beacon](https://github.com/lilwan345/beacon) reads the same session files
  and shows them as a calm lighthouse instead.

MIT, see [LICENSE](LICENSE).
