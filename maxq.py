#!/usr/bin/env python3
"""MAX-Q — your AI agent, as a launch livestream.

A miniature mission-stream card floats above all your windows and mirrors
your Claude Code state (read from the same filesystem hooks as the Wisp,
~/.ai-desktop-toy/sessions/*.json). An homage to a certain steel rocket.

    working   ASCENT — raptor plume, shock diamonds; the longer the task,
              the higher it flies: the sky fades to space and stars come out
    asking    HOLD — the countdown freezes at T-00:00:10, amber strobes on
              the tower: "GO/NO-GO — awaiting your call" (a permission
              prompt IS a launch poll)
    done      THE CATCH — the booster comes home and the chopstick arms
              close around it
    idle      pad-ready, golden hour, lazy vents
    confused  RUD (rapid unscheduled disassembly) — anomaly chip, debris,
              then back to the pad ("data is the payload")
    resting   night at the pad, red beacons blinking, stars

Craft notes:
  * Transitions are CAMERA CUTS — a one-frame feed flicker + scanline,
    because livestreams cut between cameras. Diegetic, and it makes scene
    changes feel intentional instead of jumpy.
  * Everything luminous lives INSIDE the screen bezel (a solid device
    frame), so nothing ever glows onto your wallpaper — light-background
    safe by construction (a hard-learned lesson from the Wisp).
  * The FLT counter on the telemetry bar increments every completed task
    (per-day). Your day in flights.

Click = jump to the terminal of the session driving the state (the asking
one is exactly the terminal that needs you). Drag to move. Menu-bar icon:
Tour / Live / Force state / Quit. Writes only to ~/.maxq/.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import random
import struct
import subprocess
import threading
import time
import wave as wavemod
from math import sin, cos, pi, exp
from pathlib import Path
from typing import Iterator, Optional

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QBrush, QPen, QLinearGradient, QRadialGradient,
    QPainterPath, QFont, QPixmap, QIcon, QPolygonF, QAction,
)
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu

# ---------------------------------------------------------------- paths / geo
SESSIONS_DIR = Path.home() / ".ai-desktop-toy" / "sessions"   # shared truth
HOME = Path.home() / ".maxq"
SOUND_DIR = HOME / "sounds"
WINDOW_JSON = HOME / "window.json"
FLIGHT_JSON = HOME / "flights.json"
ASSETS = Path(__file__).resolve().parent / "assets"

W, H = 252, 366
BEZEL = 9                      # device frame thickness
VX, VY = BEZEL, BEZEL + 14     # viewport (the "screen") origin
VW, VH = W - 2 * BEZEL, H - 2 * BEZEL - 14 - 40
TELE_Y = VY + VH               # telemetry strip top
TELE_H = 40
GROUND_Y = VY + VH - 26        # pad ground line inside viewport
FRAME_MS = 33
STATES = ["idle", "working", "asking", "done", "confused", "resting"]

# ------------------------------------------------------------------ detection
# Mirrors ai-desktop-toy/detect.py semantics exactly — same session files,
# same staleness windows, same priority, same false-asking transcript guard.
TRANSCRIPT_FRESH_SEC = 15.0
HOOK_GRACE_SEC = 5.0
WORKING_FALLBACK_SEC = 300.0
ASKING_STALE_SEC = 86400.0
IDLE_STALE_SEC = 30.0
EVENT_STALE_SEC = 6.0
_EVENT_STATES = ("confused", "done")
_PRIORITY = {"asking": 3, "confused": 2.5, "working": 2, "done": 1.5,
             "idle": 1, "resting": 0}


def _effective_states(now: float) -> Iterator[tuple]:
    if not SESSIONS_DIR.exists():
        return
    for f in SESSIONS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            ts = float(d["ts"])
            state = str(d["state"])
            transcript = str(d.get("transcript", ""))
        except (json.JSONDecodeError, KeyError, OSError, ValueError, TypeError):
            continue
        age = now - ts
        try:
            tmtime = os.path.getmtime(transcript) if transcript else 0.0
        except OSError:
            tmtime = 0.0
        fresh = (now - tmtime) <= TRANSCRIPT_FRESH_SEC if tmtime else False
        after_event = tmtime > ts + 0.5

        eff: Optional[str] = None
        if state == "asking":
            if age > ASKING_STALE_SEC:
                continue
            if after_event:
                if fresh:
                    eff = "working"
                elif age <= IDLE_STALE_SEC:
                    eff = "idle"
            else:
                eff = "asking"
        elif state == "working":
            if age <= HOOK_GRACE_SEC or fresh or age <= WORKING_FALLBACK_SEC:
                eff = "working"
            elif age <= IDLE_STALE_SEC:
                eff = "idle"
        elif state in _EVENT_STATES:
            if age <= EVENT_STALE_SEC:
                eff = state
            elif age <= IDLE_STALE_SEC:
                eff = "idle"
        else:
            if age <= IDLE_STALE_SEC:
                eff = "idle"
        if eff is not None:
            yield eff, d


def detect_state() -> str:
    now = time.time()
    effs = [e for e, _ in _effective_states(now)]
    if not effs:
        return "resting"
    return max(effs, key=lambda s: _PRIORITY.get(s, 0))


def winning_session() -> Optional[dict]:
    now = time.time()
    pairs = list(_effective_states(now))
    if not pairs:
        return None
    win = max((e for e, _ in pairs), key=lambda s: _PRIORITY.get(s, 0))
    best = max((r for e, r in pairs if e == win),
               key=lambda r: float(r.get("ts", 0)))
    best = dict(best)
    best["effective_state"] = win
    return best


# ---------------------------------------------------------------------- focus
# Same contract as the Wisp's focus.py: clicking jumps to the exact terminal.
# Writes the SAME focus_request.json the Wisp VSCode extension watches.
FOCUS_REQUEST = os.path.expanduser("~/.ai-desktop-toy/focus_request.json")

_ITERM_SCPT = """
on run argv
    set targetID to item 1 of argv
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (id of s) is targetID then
                        select s
                        select t
                        select w
                        activate
                        return true
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return false
end run
"""

_TERMINAL_SCPT = """
on run argv
    set targetTTY to item 1 of argv
    tell application "Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                if (tty of t) is targetTTY then
                    set selected of t to true
                    set frontmost of w to true
                    activate
                    return true
                end if
            end repeat
        end repeat
    end tell
    return false
end run
"""


def _spawn(args) -> None:
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


def focus_session(rec: dict) -> bool:
    if not rec:
        return False
    term = (rec.get("term") or "").strip()
    cwd = (rec.get("cwd") or "").strip()
    app_id = (rec.get("app_id") or "").strip()
    iterm_id = (rec.get("iterm_id") or "").strip()
    tty = (rec.get("tty") or "").strip()
    try:
        if term == "vscode":
            wrote = False
            try:
                os.makedirs(os.path.dirname(FOCUS_REQUEST), exist_ok=True)
                with open(FOCUS_REQUEST, "w") as f:
                    json.dump({"cwd": cwd, "ppids": rec.get("ppids", ""),
                               "session": str(rec.get("session", "")),
                               "ts": time.time()}, f)
                wrote = True
            except Exception:
                pass
            if app_id:
                _spawn(["open", "-b", app_id])
            return wrote or bool(app_id)
        if term == "iTerm.app" and iterm_id:
            _spawn(["osascript", "-e", _ITERM_SCPT, iterm_id])
            return True
        if term == "Apple_Terminal" and tty:
            _spawn(["osascript", "-e", _TERMINAL_SCPT, tty])
            return True
        if app_id:
            _spawn(["open", "-b", app_id])
            return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------- sounds
GAIN = 0.20
SR = 44100


def _write_wav(path: Path, samples) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wavemod.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        buf = bytearray(len(samples) * 2)
        for i, s in enumerate(samples):
            struct.pack_into("<h", buf, i * 2,
                             max(-32767, min(32767, int(s * 32767))))
        w.writeframes(bytes(buf))


def _env(t, dur, a, r):
    if t < a:
        return t / a
    if t > dur - r:
        return max(0.0, (dur - t) / r)
    return 1.0


def _gen_rumble(dur=2.2):
    """Raptor ignition — brown-noise rumble swelling in, with a 40Hz body."""
    rng = random.Random(9); n = int(SR * dur); out = [0.0] * n; acc = 0.0
    for i in range(n):
        t = i / SR
        acc = 0.988 * acc + 0.012 * (rng.random() - 0.5)
        swell = min(1.0, t / 0.7) * _env(t, dur, 0.05, 0.6)
        out[i] = (acc * 11.0 + 0.30 * sin(2 * pi * 40 * t)
                  + 0.12 * sin(2 * pi * 63 * t)) * swell * GAIN
    return out


def _gen_poll(dur=1.3):
    """GO/NO-GO attention chirp — two clean pips over a faint pad hiss."""
    rng = random.Random(4); n = int(SR * dur); out = [0.0] * n; lp = 0.0
    for i in range(n):
        t = i / SR
        lp = 0.92 * lp + 0.08 * (rng.random() - 0.5)
        out[i] = lp * 0.5 * GAIN
    for st in (0.0, 0.42):
        for i in range(int(st * SR), min(n, int((st + 0.22) * SR))):
            t = i / SR - st
            a = exp(-t * 16)
            out[i] += (0.55 * sin(2 * pi * 1180 * t)
                       + 0.22 * sin(2 * pi * 1770 * t)) * a * GAIN
    return out


def _gen_catch(dur=1.8):
    """The catch — landing-burn rumble, a metallic CLUNK, then a rising
    three-note 'nailed it' chime."""
    rng = random.Random(2); n = int(SR * dur); out = [0.0] * n; acc = 0.0
    for i in range(int(0.55 * SR)):
        t = i / SR
        acc = 0.986 * acc + 0.014 * (rng.random() - 0.5)
        out[i] += acc * 9.0 * (1.0 - t / 0.55) * GAIN
    ct = 0.55
    for i in range(int(ct * SR), min(n, int((ct + 0.3) * SR))):
        t = i / SR - ct
        a = exp(-t * 22)
        out[i] += (0.6 * sin(2 * pi * 165 * t) + 0.3 * sin(2 * pi * 247 * t)
                   + 0.25 * (rng.random() - 0.5) * exp(-t * 60)) * a * GAIN * 1.4
    for st, f0 in ((0.85, 523.3), (1.0, 659.3), (1.15, 784.0)):
        for i in range(int(st * SR), n):
            t = i / SR - st
            out[i] += 0.30 * exp(-t * 4.0) * (sin(2 * pi * f0 * t)
                                              + 0.4 * sin(2 * pi * f0 * 2 * t)) * GAIN
    return out


def _gen_rud(dur=1.5):
    """Muffled distant boom + debris patter."""
    rng = random.Random(7); n = int(SR * dur); out = [0.0] * n; acc = 0.0
    for i in range(n):
        t = i / SR
        acc = 0.984 * acc + 0.016 * (rng.random() - 0.5)
        body = acc * 16.0 * exp(-t * 2.6)
        thud = 0.5 * exp(-t * 7) * sin(2 * pi * 48 * t)
        out[i] = (body + thud) * _env(t, dur, 0.004, 0.4) * GAIN
    return out


def _gen_vent(dur=1.2):
    """Pad ambience — wind + a soft nitrogen vent hiss."""
    rng = random.Random(12); n = int(SR * dur); out = [0.0] * n
    lp = 0.0; bp = 0.0; prev = 0.0
    for i in range(n):
        t = i / SR
        w_ = rng.random() - 0.5
        lp = 0.96 * lp + 0.04 * w_
        bp = 0.90 * bp + 0.10 * w_
        hiss = (bp - prev); prev = bp
        vent = hiss * 3.0 * max(0.0, sin(pi * min(1.0, t / 0.9)))
        out[i] = (lp * 1.6 + vent * 0.5) * _env(t, dur, 0.1, 0.3) * GAIN
    return out


def _gen_night(dur=2.0):
    rng = random.Random(3); n = int(SR * dur); out = [0.0] * n; lp = 0.0
    for i in range(n):
        t = i / SR
        lp = 0.97 * lp + 0.03 * (rng.random() - 0.5)
        sw = 0.5 + 0.5 * sin(2 * pi * 0.5 * t - pi / 2)
        out[i] = lp * 2.2 * sw * _env(t, dur, 0.2, 0.4) * GAIN * 0.7
    return out


def _gen_blip(dur=0.12):
    n = int(SR * dur); out = [0.0] * n
    for i in range(n):
        t = i / SR
        out[i] = 0.5 * exp(-t * 40) * sin(2 * pi * 1450 * t) * GAIN
    return out


SOUND_GENS = {"idle": _gen_vent, "working": _gen_rumble, "asking": _gen_poll,
              "done": _gen_catch, "confused": _gen_rud, "resting": _gen_night,
              "blip": _gen_blip}


def ensure_sounds() -> dict:
    paths = {}
    for name, gen in SOUND_GENS.items():
        p = SOUND_DIR / f"{name}.wav"
        if not p.exists():
            _write_wav(p, gen())
        paths[name] = p
    return paths


def play(paths: dict, name: str) -> None:
    p = paths.get(name)
    if p is not None:
        try:
            subprocess.Popen(["afplay", str(p)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


# ------------------------------------------------------------------- palettes
# 3-stop skies sampled from the reference renders (desaturated grey-mauve
# mid band — the 2-stop ramps read like candy by comparison).
PALETTES = {
    "idle": dict(
        sky_top=(96, 128, 175), sky_mid=(163, 152, 156),
        sky_bot=(227, 204, 186),
        ground=(96, 84, 74), pad=(56, 58, 64),
        star_a=0.0, strobe_a=0.0, beacon_a=0.0,
        chip=(146, 186, 205), chip_txt="PAD · READY",
        amb=1.0, vent_a=0.8, cloud_a=0.9,
    ),
    "working": dict(
        sky_top=(96, 140, 196), sky_mid=(146, 164, 196),
        sky_bot=(208, 198, 178),
        ground=(96, 84, 74), pad=(56, 58, 64),
        star_a=0.0, strobe_a=0.0, beacon_a=0.0,
        chip=(125, 190, 215), chip_txt="ASCENT",
        amb=1.05, vent_a=0.0, cloud_a=0.9,
    ),
    "asking": dict(
        sky_top=(73, 63, 95), sky_mid=(154, 134, 135),
        sky_bot=(201, 136, 86),
        ground=(120, 98, 84), pad=(56, 56, 64),
        star_a=0.10, strobe_a=1.0, beacon_a=0.0,
        chip=(255, 186, 80), chip_txt="HOLD · GO/NO-GO",
        amb=0.88, vent_a=1.0, cloud_a=0.6,
    ),
    "done": dict(
        sky_top=(100, 134, 182), sky_mid=(168, 158, 160),
        sky_bot=(232, 206, 182),
        ground=(96, 84, 74), pad=(56, 58, 64),
        star_a=0.0, strobe_a=0.0, beacon_a=0.0,
        chip=(120, 240, 150), chip_txt="CAUGHT",
        amb=1.08, vent_a=0.3, cloud_a=0.7,
    ),
    "confused": dict(
        sky_top=(54, 58, 74), sky_mid=(92, 92, 98),
        sky_bot=(120, 116, 118),
        ground=(74, 66, 60), pad=(48, 48, 54),
        star_a=0.0, strobe_a=0.4, beacon_a=0.0,
        chip=(255, 110, 110), chip_txt="ANOMALY · RUD",
        amb=0.7, vent_a=0.0, cloud_a=0.9,
    ),
    "resting": dict(
        sky_top=(12, 18, 40), sky_mid=(19, 25, 51),
        sky_bot=(44, 51, 74),
        ground=(23, 24, 28), pad=(26, 28, 34),
        star_a=1.0, strobe_a=0.0, beacon_a=1.0,
        chip=(160, 166, 182), chip_txt="STANDBY",
        amb=0.4, vent_a=0.0, cloud_a=0.0,
    ),
}


def _lerp(a, b, f):
    return a + (b - a) * f


def blend_pal(pa: dict, pb: dict, f: float) -> dict:
    out = {}
    for k, va in pa.items():
        vb = pb[k]
        if isinstance(va, tuple):
            out[k] = tuple(int(_lerp(va[i], vb[i], f)) for i in range(3))
        elif isinstance(va, str):
            out[k] = vb if f > 0.5 else va
        else:
            out[k] = _lerp(va, vb, f)
    return out


def C(rgb, a=255) -> QColor:
    return QColor(rgb[0], rgb[1], rgb[2], int(max(0, min(255, a))))


def mul(rgb, m) -> tuple:
    return (int(min(255, rgb[0] * m)), int(min(255, rgb[1] * m)),
            int(min(255, rgb[2] * m)))


# ----------------------------------------------------------------- anim state
class Anim:
    def __init__(self, seed=7):
        rng = random.Random(seed)
        # (x, y, phase, size, base_alpha, cool_tint)
        self.stars = [(rng.uniform(0.03, 0.97), rng.uniform(0.02, 0.72),
                       rng.uniform(0, 6.3), rng.uniform(0.45, 1.1),
                       rng.uniform(0.35, 1.0), rng.random() < 0.10)
                      for _ in range(120)]
        # night skyline: distant buildings + a few lit windows
        self.skyline = []
        x = 0.0
        while x < 1.0:
            w_ = rng.uniform(0.03, 0.09)
            self.skyline.append((x, w_, rng.uniform(5, 16)))
            x += w_ + rng.uniform(0.01, 0.05)
        self.windows = [(rng.uniform(0.02, 0.95), rng.uniform(2, 12),
                         rng.random() < 0.6) for _ in range(11)]
        # GO-given countdown beat (asking → working transition)
        self.go_given = False
        self.cd = 0.0
        self.clouds = [dict(x=rng.uniform(0, 1), y=rng.uniform(0.18, 0.52),
                            s=rng.uniform(0.7, 1.3), sp=rng.uniform(0.8, 1.2))
                       for _ in range(3)]
        self.smoke = []          # pad smoke / steam particles
        self.debris = []         # RUD fragments
        self.alt = 0.0           # 0 pad … 1 space (working climb)
        self.shake = 0.0         # screen shake amount
        self.cut_t0 = -9.0       # camera-cut flicker timestamp
        self.rud_fired = False
        self.catch_clunked = False
        # Orbital coast (long working tasks): stage sep + day/night cycle.
        self.sep_done = False
        self.sep_t0 = 0.0
        self.orbit_clouds = [dict(u=rng.uniform(0, 1),
                                  lat=rng.uniform(0.08, 0.92),
                                  w=rng.uniform(0.07, 0.20),
                                  sp=rng.uniform(0.6, 1.4))
                             for _ in range(7)]
        self.cities = [(rng.uniform(0, 1), rng.uniform(0.15, 0.9))
                       for _ in range(26)]


def _spawn_smoke(A: Anim, x, y, n, spread, rise, big=False, bank=False):
    if len(A.smoke) > 220:                       # particle budget (CPU cap)
        return
    for _ in range(n):
        if bank:
            # liftoff cumulus bank: huge, slow-fading, two sculpted greys,
            # billowing outward both ways (the reference frame-1 look)
            A.smoke.append(dict(
                x=x + random.uniform(-spread, spread),
                y=y + random.uniform(-3, 3),
                vx=random.choice((-1.0, 1.0)) * random.uniform(18, 50),
                vy=-random.uniform(2, 10),
                r=random.uniform(8, 16), a=random.uniform(0.55, 0.85),
                t=0.0, grow=14.0, dec=0.16,
                col=random.choice(((222, 224, 226), (150, 153, 156))),
            ))
        else:
            A.smoke.append(dict(
                x=x + random.uniform(-spread, spread),
                y=y + random.uniform(-2, 2),
                vx=random.uniform(-14, 14) * (1.6 if big else 1.0),
                vy=-random.uniform(4, rise),
                r=random.uniform(4, 9) * (1.7 if big else 1.0),
                a=random.uniform(0.5, 0.9), t=0.0,
                grow=9.0, dec=0.55, col=(232, 233, 236),
            ))


def update_anim(A: Anim, state: str, t: float, dt: float, st_t: float,
                pal: dict) -> None:
    for c in A.clouds:
        c["x"] += dt * 0.012 * c["sp"]
        if c["x"] > 1.2:
            c["x"] = -0.2
    # Smoke particles.
    for s in A.smoke[:]:
        s["t"] += dt
        s["x"] += s["vx"] * dt
        s["y"] += s["vy"] * dt
        s["vx"] *= (1 - 1.0 * dt)
        s["r"] += s["grow"] * dt
        s["a"] -= s["dec"] * dt
        if s["a"] <= 0:
            A.smoke.remove(s)
    # Debris (confused RUD).
    for d in A.debris[:]:
        d["t"] += dt
        d["x"] += d["vx"] * dt
        d["y"] += d["vy"] * dt
        d["vy"] += 220 * dt
        if d["y"] > GROUND_Y + 30:
            A.debris.remove(d)

    A.shake = max(0.0, A.shake - 3.2 * dt)

    if state == "working":
        # GO-given countdown beat → 1.8s ignition → climb.
        A.cd = 3.5 if A.go_given else 0.0
        lift = A.cd + 1.8                        # liftoff timestamp
        if st_t < A.cd:
            # countdown: the pad breathes — vents along the stack, at the
            # tower QD arm, and ground wisps (no shake yet)
            A.alt = 0.0
            A.sep_done = False
            if random.random() < 0.8:
                src = random.random()
                if src < 0.5:                    # stack-body vents
                    _spawn_smoke(A, GCX_PAD + random.choice((-10, 9)),
                                 GROUND_Y - MOUNT_H -
                                 random.choice((52, 88, 124)), 1, 2, 10)
                elif src < 0.8:                  # tower QD arm
                    _spawn_smoke(A, TOWER_X - 10, GROUND_Y - 88, 1, 3, 12)
                else:                            # ground wisps
                    _spawn_smoke(A, GCX_PAD + random.uniform(-30, 26),
                                 GROUND_Y - 5, 1, 4, 8)
        elif st_t < lift:
            A.alt = 0.0
            A.sep_done = False                   # fresh flight, fresh stack
            _spawn_smoke(A, GCX_PAD, GROUND_Y - 4,
                         3 if random.random() < 0.8 else 4, 24, 30, bank=True)
            A.shake = min(1.0, A.shake + 2.0 * dt)
        else:
            if A.alt == 0.0:                     # first frame past liftoff:
                A.cut_t0 = t                     # cut pad cam → chase cam
            A.alt = min(1.0, (st_t - lift) / 40.0)
            if A.alt >= 1.0 and not A.sep_done:
                A.sep_done = True                # STAGE SEP — booster away,
                A.sep_t0 = t                     # cut to the orbital ship cam
                A.cut_t0 = t
            if A.alt < 0.12 and random.random() < 0.5:
                _spawn_smoke(A, GCX_PAD, GROUND_Y - 4, 2, 26, 26, bank=True)
    elif state == "done":
        A.alt = 0.0
        if 2.2 <= st_t <= 3.0 and random.random() < 0.5:   # landing burn dust
            _spawn_smoke(A, TOWER_X - 21, GROUND_Y - 4, 2, 14, 24)
    else:
        A.alt = 0.0
        A.sep_done = False
        if pal["vent_a"] > 0.1 and random.random() < 0.05 * pal["vent_a"]:
            _spawn_smoke(A, GCX_PAD + 11, GROUND_Y - 86, 1, 2, 12)

    if state == "confused" and not A.rud_fired and st_t > 0.3:
        A.rud_fired = True
        A.shake = 1.0
        bx, by = GCX_PAD, GROUND_Y - 110
        for _ in range(12):
            ang = random.uniform(0, 2 * pi)
            sp = random.uniform(40, 130)
            A.debris.append(dict(x=bx, y=by, vx=cos(ang) * sp,
                                 vy=sin(ang) * sp - 70, t=0.0))
        _spawn_smoke(A, bx, by, 14, 12, 30, big=True)
    if state != "confused":
        A.rud_fired = False
    if state != "done":
        A.catch_clunked = False


# ----------------------------------------------------------------- baked AI art
# Same convention as the Wisp: drop PNGs in assets/vehicle/ and they take
# over from the procedural vehicle at startup:
#   assets/vehicle/stack.png    photoreal full stack (booster+ship, standing)
#   assets/vehicle/booster.png  booster alone (the catch)
#   assets/vehicle/tower.png    Mechazilla lattice (chopsticks stay
#                               procedural ON TOP so the catch still animates)
# Plume / smoke / shake stay procedural over the sprites so motion stays live.
# (The Boss Cam is fully procedural — an animated cartoon, no assets.
# load_assets() still accepts optional portraits in assets/boss/<state>.png.)
SPRITES: dict = {}


def load_assets() -> None:
    """Call once after QApplication exists (QPixmap needs it)."""
    bdir = ASSETS / "boss"
    if bdir.is_dir():
        for st in STATES:
            f = bdir / f"{st}.png"
            if f.exists():
                pm = QPixmap(str(f))
                if not pm.isNull():
                    BOSS_PIX[st] = pm
    vdir = ASSETS / "vehicle"
    if vdir.is_dir():
        for name in ("stack", "booster", "ship", "tower",
                     "stack_night", "booster_night", "ship_night",
                     "tower_night", "earth", "clouds", "clouds2"):
            f = vdir / f"{name}.png"
            if f.exists():
                pm = QPixmap(str(f))
                if not pm.isNull():
                    SPRITES[name] = pm


def _draw_sprite(p: QPainter, pm: QPixmap, cx, base_y, target_h) -> None:
    """Bottom-center-anchored sprite, height-normalized to the procedural
    dimensions so all existing motion math keeps working."""
    sc = target_h / pm.height()
    w_ = pm.width() * sc
    p.drawPixmap(QRectF(cx - w_ / 2, base_y - target_h, w_, target_h),
                 pm, QRectF(pm.rect()))


# ------------------------------------------------------------------- renderer
GCX_PAD = VX + VW * 0.38          # pad / stack center x
TOWER_X = VX + VW * 0.66          # Mechazilla x
# Sprite scale: the 231-unit vehicle renders 168 px tall (0.57 of VH).
SCALE = 168.0 / 231.0
STACK_TGT = 235.0 * SCALE         # stack sprite canvas → screen px (171)
BOOST_TGT = 132.0 * SCALE         # booster sprite canvas → screen px (96)
MOUNT_H = 12.0                    # launch-table height
TOWER_LAT = 170.0                 # tower lattice height on screen
TOWER_TGT = TOWER_LAT * 240.0 / 224.0   # tower sprite canvas → screen px


def _steel(p: QPainter, x, y, w_, h_, amb, r=3.0):
    """Stainless cylinder with a vertical sheen."""
    g = QLinearGradient(x, 0, x + w_, 0)
    g.setColorAt(0.0, C(mul((150, 154, 160), amb)))
    g.setColorAt(0.28, C(mul((214, 218, 224), amb)))
    g.setColorAt(0.55, C(mul((176, 180, 188), amb)))
    g.setColorAt(1.0, C(mul((120, 124, 132), amb)))
    p.setBrush(QBrush(g))
    p.drawRoundedRect(QRectF(x, y, w_, h_), r, r)


def _draw_stack(p: QPainter, cx, base_y, amb, burning, t,
                ship=True, fins=True):
    """Booster (+ ship) standing on `base_y`. Returns nothing; pure draw."""
    bw = 22.0                     # booster width
    bh = 78.0                     # booster height
    # Photoreal sprite takes over when baked art exists (plume stays
    # procedural below so the flame keeps flickering).
    spr = SPRITES.get("stack") if ship else SPRITES.get("booster")
    if spr is not None:
        target_h = STACK_TGT if ship else BOOST_TGT
        _draw_sprite(p, spr, cx, base_y, target_h)
        # Sprites don't react to the palette's ambient level — crossfade
        # to the pre-baked night variant as amb drops (dusk/storm/night).
        dim = max(0.0, min(1.0, (1.0 - amb) / 0.6))
        nspr = (SPRITES.get("stack_night") if ship
                else SPRITES.get("booster_night"))
        if nspr is not None and dim > 0.02:
            p.setOpacity(dim)
            _draw_sprite(p, nspr, cx, base_y, target_h)
            p.setOpacity(1.0)
        _draw_plume(p, cx, base_y, 16.0, burning, t)
        return
    x = cx - bw / 2
    by = base_y - bh
    _steel(p, x, by, bw, bh, amb)
    p.setPen(QPen(C(mul((110, 114, 122), amb), 110), 0.8))
    for i in range(1, 6):                        # weld lines
        yy = by + bh * i / 6
        p.drawLine(QPointF(x + 1, yy), QPointF(x + bw - 1, yy))
    p.setPen(Qt.NoPen)
    if fins:                                     # grid fins near the top
        p.setBrush(QBrush(C(mul((92, 96, 104), amb))))
        p.drawRoundedRect(QRectF(x - 4.5, by + 7, 5, 9), 1.5, 1.5)
        p.drawRoundedRect(QRectF(x + bw - 0.5, by + 7, 5, 9), 1.5, 1.5)
    p.setBrush(QBrush(C(mul((70, 72, 80), amb))))   # engine skirt
    p.drawRect(QRectF(x, base_y - 7, bw, 7))

    if ship:                                     # ship on top
        sh = 56.0
        sy = by - sh
        _steel(p, x, sy + 8, bw, sh - 8, amb)
        nose = QPainterPath(QPointF(x, sy + 12))  # ogive nosecone
        nose.cubicTo(QPointF(x + 1, sy + 2), QPointF(x + bw * 0.32, sy),
                     QPointF(x + bw * 0.5, sy))
        nose.cubicTo(QPointF(x + bw * 0.68, sy), QPointF(x + bw - 1, sy + 2),
                     QPointF(x + bw, sy + 12))
        nose.closeSubpath()
        p.setBrush(QBrush(C(mul((196, 200, 208), amb))))
        p.drawPath(nose)
        # Black tile stripe (leeward) + forward flaps.
        p.setBrush(QBrush(C(mul((44, 46, 54), amb))))
        p.drawRoundedRect(QRectF(x + 1.5, sy + 9, 6.5, sh - 11), 2.5, 2.5)
        p.setBrush(QBrush(C(mul((60, 62, 72), amb))))
        p.drawPolygon(QPolygonF([QPointF(x - 5.5, sy + 16), QPointF(x, sy + 12),
                                 QPointF(x, sy + 30), QPointF(x - 5.5, sy + 33)]))
        p.drawPolygon(QPolygonF([QPointF(x + bw + 5.5, sy + 16),
                                 QPointF(x + bw, sy + 12),
                                 QPointF(x + bw, sy + 30),
                                 QPointF(x + bw + 5.5, sy + 33)]))

    _draw_plume(p, cx, base_y, bw, burning, t)


def _draw_plume(p: QPainter, cx, base_y, bw, burning, t):
    """Raptor plume + shock diamonds — always procedural (it must flicker)."""
    if burning > 0.01:
        flick = 1.0 + 0.10 * sin(t * 31) + 0.06 * sin(t * 47 + 1.3)
        pl = 44.0 * burning * flick
        pw = bw * 0.92
        # Outer heat glow (inside the bezel → wallpaper-safe).
        gg = QRadialGradient(cx, base_y + pl * 0.35, pl * 0.95)
        gg.setColorAt(0.0, QColor(255, 180, 90, int(64 * burning)))
        gg.setColorAt(1.0, QColor(255, 140, 60, 0))
        p.setBrush(QBrush(gg))
        p.drawEllipse(QPointF(cx, base_y + pl * 0.35), pl * 0.95, pl * 0.95)
        plume = QPainterPath(QPointF(cx - pw / 2, base_y - 2))
        plume.quadTo(QPointF(cx - pw * 0.62, base_y + pl * 0.55),
                     QPointF(cx, base_y + pl))
        plume.quadTo(QPointF(cx + pw * 0.62, base_y + pl * 0.55),
                     QPointF(cx + pw / 2, base_y - 2))
        plume.closeSubpath()
        g = QLinearGradient(0, base_y, 0, base_y + pl)
        g.setColorAt(0.0, QColor(255, 236, 180, int(235 * burning)))
        g.setColorAt(0.45, QColor(255, 160, 70, int(190 * burning)))
        g.setColorAt(1.0, QColor(255, 110, 40, 0))
        p.setBrush(QBrush(g))
        p.drawPath(plume)
        core_w = pw * 0.34                        # bright core + shock diamonds
        g2 = QLinearGradient(0, base_y, 0, base_y + pl * 0.8)
        g2.setColorAt(0.0, QColor(255, 255, 240, int(245 * burning)))
        g2.setColorAt(1.0, QColor(255, 220, 150, 0))
        p.setBrush(QBrush(g2))
        p.drawPolygon(QPolygonF([QPointF(cx - core_w / 2, base_y - 1),
                                 QPointF(cx + core_w / 2, base_y - 1),
                                 QPointF(cx, base_y + pl * 0.8)]))
        for k in range(3):
            dy = base_y + pl * (0.18 + 0.20 * k)
            dr = core_w * (0.42 - 0.10 * k) * flick
            p.setBrush(QBrush(QColor(255, 250, 220,
                                     int(200 * burning * (1 - k * 0.25)))))
            p.drawEllipse(QPointF(cx, dy), dr, dr * 1.5)


def _draw_tower(p: QPainter, x, ground, amb, arms_open, arms_y_frac,
                strobe_a, beacon_a, t):
    """Mechazilla: lattice tower + chopstick arms.
    arms_open 0=closed on booster, 1=fully open; arms_y_frac 0=catch height,
    1=top of tower."""
    th = TOWER_LAT
    tw = 18.0                                    # lattice width on screen
    top = ground - th
    spr = SPRITES.get("tower")
    if spr is not None:
        # Baked lattice + crane; the chopsticks below stay procedural so
        # the catch still animates.
        _draw_sprite(p, spr, x, ground, TOWER_TGT)
        dim = max(0.0, min(1.0, (1.0 - amb) / 0.6))
        nspr = SPRITES.get("tower_night")
        if nspr is not None and dim > 0.02:
            p.setOpacity(dim)
            _draw_sprite(p, nspr, x, ground, TOWER_TGT)
            p.setOpacity(1.0)
    else:
        p.setBrush(QBrush(C(mul((52, 56, 66), amb))))
        p.drawRect(QRectF(x - tw / 2, top, tw, th))
        p.setPen(QPen(C(mul((86, 92, 104), amb), 200), 1.0))
        yy = top
        while yy < ground - 8:                   # cross bracing
            p.drawLine(QPointF(x - tw / 2 + 1, yy),
                       QPointF(x + tw / 2 - 1, yy + 11))
            p.drawLine(QPointF(x + tw / 2 - 1, yy),
                       QPointF(x - tw / 2 + 1, yy + 11))
            yy += 11
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(C(mul((60, 64, 74), amb))))
        p.drawRect(QRectF(x - tw / 2 - 2, top - 5, tw + 4, 5))

    # Chopstick arms — TWO tapered truss arms in a V from a pivot carriage
    # on the rocket-side chord, each tip carrying a round knob bumper.
    # arms_open 1 = V open (+25/−28°), 0 = closed near-horizontal grip.
    ay = ground - th * (0.52 + 0.34 * arms_y_frac)
    px_ = x - tw / 2 + 1.5                       # pivot on the pad side
    p.setBrush(QBrush(C(mul((48, 52, 62), amb))))
    p.drawRoundedRect(QRectF(px_ - 3.0, ay - 5.5, 6.5, 11.0), 1.5, 1.5)
    alen = 27.0
    arm_col = C(mul((58, 63, 74), amb))
    knob_col = C(mul((40, 43, 52), amb))
    for ang_open, ang_closed in ((25.0, 4.0), (-28.0, -4.0)):
        a_ = (ang_closed + (ang_open - ang_closed) * arms_open) * pi / 180.0
        ex = px_ - alen * cos(a_)
        ey = ay - alen * sin(a_)
        # tapered truss polygon (root 4.6 → tip 2.4 wide, ⟂ to the arm)
        nx_, ny_ = sin(a_), cos(a_)              # unit normal
        r_, t_ = 3.0, 1.4
        p.setBrush(QBrush(arm_col))
        p.drawPolygon(QPolygonF([
            QPointF(px_ + nx_ * r_, ay + ny_ * r_),
            QPointF(ex + nx_ * t_, ey + ny_ * t_),
            QPointF(ex - nx_ * t_, ey - ny_ * t_),
            QPointF(px_ - nx_ * r_, ay - ny_ * r_),
        ]))
        p.setPen(QPen(C(mul((84, 90, 102), amb), 190), 0.8))
        p.drawLine(QPointF(px_, ay), QPointF(ex, ey))     # truss chord hint
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(knob_col))                       # knob bumper
        p.drawEllipse(QPointF(ex, ey), 2.3, 2.3)

    # Amber HOLD strobes — double-flash like real pad strobes.
    if strobe_a > 0.02:
        ph = (t * 1.6) % 1.0
        on = 1.0 if (ph < 0.07 or 0.12 < ph < 0.19) else 0.0
        if on:
            for sy in (top + 4, top + th * 0.45):
                g = QRadialGradient(x, sy, 9)
                g.setColorAt(0, QColor(255, 190, 80, int(230 * strobe_a)))
                g.setColorAt(1, QColor(255, 190, 80, 0))
                p.setBrush(QBrush(g))
                p.drawEllipse(QPointF(x, sy), 9, 9)
                p.setBrush(QBrush(QColor(255, 220, 140, int(250 * strobe_a))))
                p.drawEllipse(QPointF(x, sy), 1.8, 1.8)
    # Night beacons — alternate red blink with a soft halo.
    if beacon_a > 0.02:
        for i, bx_ in enumerate((x - tw / 2 + 1, x + tw / 2 - 1)):
            on = 0.25 + 0.75 * (1.0 if sin(t * 2.2 + i * pi) > 0.45 else 0.0)
            hg = QRadialGradient(bx_, top - 2, 5.5)
            hg.setColorAt(0.0, QColor(232, 84, 68, int(120 * beacon_a * on)))
            hg.setColorAt(1.0, QColor(232, 84, 68, 0))
            p.setBrush(QBrush(hg))
            p.drawEllipse(QPointF(bx_, top - 2), 5.5, 5.5)
            p.setBrush(QBrush(QColor(232, 84, 68,
                                     int(230 * beacon_a * on))))
            p.drawEllipse(QPointF(bx_, top - 2), 1.6, 1.6)




def _draw_watcher(p: QPainter, x, gy, state, st_t, t, amb, alt=0.0):
    """The Boss — a tiny silhouette caricature watching his rocket, in
    tribute to the famous from-behind launch photos. Respectful cartoon
    silhouette only (no realistic likeness): black jacket, hands behind
    back; he turns to face YOU when the count is holding on your GO.

      idle/working  back to camera, hands clasped behind, head follows
                    the vehicle up as it climbs
      asking        faces the camera, arms crossed, foot tapping
      done          arms shoot up at the catch, little hop
      confused      both hands on head (everyone's RUD pose)
    """
    hgt = 34.0
    head_r = 5.0
    jacket = mul((30, 32, 40), max(0.75, amb))
    hair = mul((52, 42, 36), max(0.75, amb))
    face = mul((222, 184, 158), amb)
    sway = 0.8 * sin(t * 0.9)
    hop = 0.0
    if state == "done" and st_t > 2.9:
        hdt = st_t - 2.9
        hop = 7.0 * abs(sin(hdt * pi * 2.6)) * exp(-hdt * 1.6)
    base = QPointF(x + sway * 0.4, gy - hop)
    hip_y = base.y() - hgt * 0.45
    sh_y = base.y() - hgt * 0.78        # shoulder line
    head_c = QPointF(base.x(), sh_y - head_r - 1.5)

    # Head pitch: follow the rocket up during ascent/ignition.
    look_up = 0.0
    if state == "working":
        look_up = min(1.0, st_t / 3.0) * 0.6 + alt * 0.4
    head_c = QPointF(head_c.x() + look_up * 1.5, head_c.y() - look_up * 1.0)

    p.setPen(Qt.NoPen)
    # Legs.
    p.setBrush(QBrush(C(jacket)))
    for sgn in (-1.0, 1.0):
        lx = base.x() + sgn * 2.6
        p.drawRoundedRect(QRectF(lx - 1.7, hip_y, 3.4, base.y() - hip_y), 1.6, 1.6)
    # Foot tap while holding for your GO.
    if state == "asking":
        tap = max(0.0, sin(t * 6.0)) * 2.6
        p.drawRoundedRect(QRectF(base.x() + 1.0, base.y() - 1.8 - tap, 5.4, 2.4),
                          1.2, 1.2)
    # Torso (jacket) — slight taper.
    torso = QPolygonF([QPointF(base.x() - 6.2, sh_y),
                       QPointF(base.x() + 6.2, sh_y),
                       QPointF(base.x() + 4.6, hip_y + 1),
                       QPointF(base.x() - 4.6, hip_y + 1)])
    p.drawPolygon(torso)

    pen = QPen(C(jacket), 3.2, Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    if state == "done":
        # Arms up in a V — the catch celebration.
        for sgn in (-1.0, 1.0):
            p.drawLine(QPointF(base.x() + sgn * 4.6, sh_y + 1.5),
                       QPointF(base.x() + sgn * 9.5, sh_y - 9.5))
    elif state == "confused":
        # Hands on head.
        for sgn in (-1.0, 1.0):
            p.drawLine(QPointF(base.x() + sgn * 5.2, sh_y + 2.0),
                       QPointF(base.x() + sgn * 2.6, head_c.y() - 2.5))
    elif state == "asking":
        # Arms crossed in front.
        p.drawLine(QPointF(base.x() - 5.4, sh_y + 2.2),
                   QPointF(base.x() + 3.6, sh_y + 6.4))
        p.drawLine(QPointF(base.x() + 5.4, sh_y + 2.2),
                   QPointF(base.x() - 3.6, sh_y + 6.4))
    else:
        # Hands clasped behind the back (the canonical watching pose) —
        # upper arms hug the torso, forearms meet behind.
        for sgn in (-1.0, 1.0):
            p.drawLine(QPointF(base.x() + sgn * 5.6, sh_y + 2.0),
                       QPointF(base.x() + sgn * 2.0, hip_y - 1.5))
    p.setPen(Qt.NoPen)

    # Head: hair from behind, or face + hair cap when he turns to you.
    if state in ("asking", "done", "confused"):
        p.setBrush(QBrush(C(face)))
        p.drawEllipse(head_c, head_r - 0.4, head_r + 0.2)
        p.setBrush(QBrush(C(hair)))
        cap = QPainterPath()
        cap.moveTo(head_c.x() - head_r, head_c.y() + 1.6)
        cap.arcTo(QRectF(head_c.x() - head_r, head_c.y() - head_r - 0.4,
                         head_r * 2, head_r * 2 + 2.0), 180, -180)
        cap.closeSubpath()
        p.drawPath(cap)
    else:
        p.setBrush(QBrush(C(hair)))
        p.drawEllipse(head_c, head_r, head_r + 0.6)


ORBIT_CYCLE = 110.0               # seconds per day/night lap while coasting


def _draw_earth_disc(p: QPainter, A: Anim, t: float, ecx, ecy, R,
                     dayness, trans, term_x, into_night, dark_frac) -> None:
    """Earth with baked texture strips, limb haze and atmosphere glow.
    Shared by the late-ascent limb (day, no terminator) and the orbital
    coast (terminator sweep, night side cities + aurora)."""

    def L(a, b, f):
        return int(a + (b - a) * f)

    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(L(8, 30, dayness), L(11, 72, dayness),
                             L(20, 128, dayness))))
    p.drawEllipse(QPointF(ecx, ecy), R, R)

    p.save()                                     # surface details, clipped
    cp = QPainterPath()
    cp.addEllipse(QPointF(ecx, ecy), R, R)
    p.setClipPath(cp, Qt.IntersectClip)
    limb_y = ecy - R
    band_h = max(20.0, (VY + VH) - limb_y) * 0.62

    etex = SPRITES.get("earth")
    if etex is not None:
        dest_h = band_h + 40
        for pm, speed, dy_ in ((etex, 5.0, 0.0),
                               (SPRITES.get("clouds2"), 8.0, -4.0),
                               (SPRITES.get("clouds"), 12.0, -8.0)):
            if pm is None:
                continue
            sc_ = dest_h / pm.height()
            dw = pm.width() * sc_
            x_ = VX - ((t * speed) % dw)
            while x_ < VX + VW:
                p.drawPixmap(QRectF(x_, limb_y + dy_, dw, dest_h),
                             pm, QRectF(pm.rect()))
                x_ += dw
        ga = int(70 * max(0.0, dayness - 0.25))  # sun glint on the ocean
        if ga > 3:
            gx_, gy_ = ecx + VW * 0.14, limb_y + band_h * 0.55
            gl = QRadialGradient(gx_, gy_, 40)
            gl.setColorAt(0.0, QColor(255, 250, 230, ga))
            gl.setColorAt(1.0, QColor(255, 250, 230, 0))
            p.setBrush(QBrush(gl))
            p.drawEllipse(QPointF(gx_, gy_), 40, 14)
    else:
        for c in A.orbit_clouds:                 # no-asset fallback
            cu = (c["u"] + t * 0.004 * c["sp"]) % 1.2 - 0.1
            p.setBrush(QBrush(QColor(255, 255, 255,
                                     int(72 * (0.30 + 0.70 * dayness)))))
            p.drawEllipse(QPointF(VX + cu * VW, limb_y + c["lat"] * band_h),
                          c["w"] * VW, 3.2)

    # limb haze INSIDE the disc — the air is thickest at the horizon
    hz = QRadialGradient(ecx, ecy, R)
    hz.setColorAt(max(0.0, (R - 46) / R), QColor(150, 205, 245, 0))
    hz.setColorAt((R - 10) / R, QColor(165, 215, 250, 60))
    hz.setColorAt(1.0, QColor(205, 235, 255, 150))
    p.setBrush(QBrush(hz))
    p.drawEllipse(QPointF(ecx, ecy), R, R)

    # night creeps in behind the terminator (soft edge)
    if term_x is not None and dark_frac > 0.001:
        tg = QLinearGradient(term_x - 30, 0, term_x + 14, 0)
        dark = QColor(5, 8, 18, 178)
        clear = QColor(5, 8, 18, 0)
        tg.setColorAt(0.0, dark if into_night else clear)
        tg.setColorAt(1.0, clear if into_night else dark)
        p.setBrush(QBrush(tg))
        p.drawRect(QRectF(VX, limb_y - 8, VW, band_h + 60))

        # city lights + aurora live on the dark side only
        p.setBrush(QBrush(QColor(255, 214, 140, 200)))
        for cu0, clat in A.cities:
            cxx = VX + ((cu0 + t * 0.0035) % 1.0) * VW
            on_dark = ((cxx < term_x - 14) if into_night
                       else (cxx > term_x + 14))
            if on_dark:
                p.drawRect(QRectF(cxx, limb_y + clat * band_h, 1.6, 1.6))
        night_a = max(0.0, dark_frac * 1.3 - 0.3)
        if night_a > 0.05:
            aa = int(46 * night_a * (0.6 + 0.4 * sin(t * 0.7)))
            p.setBrush(QBrush(QColor(110, 235, 160, aa)))
            for k in range(2):                   # aurora curtains
                ax = (VX + VW * (0.25 + 0.4 * k)
                      + 10 * sin(t * 0.21 + k * 2.4))
                if (ax >= term_x - 14) if into_night else (ax <= term_x + 14):
                    continue                     # stay on the night side
                cur = QPainterPath(QPointF(ax, limb_y - 6))
                cur.cubicTo(QPointF(ax + 9, limb_y + 8),
                            QPointF(ax - 7, limb_y + 18),
                            QPointF(ax + 4, limb_y + 30))
                cur.lineTo(QPointF(ax + 14, limb_y + 30))
                cur.cubicTo(QPointF(ax + 5, limb_y + 16),
                            QPointF(ax + 19, limb_y + 8),
                            QPointF(ax + 11, limb_y - 6))
                cur.closeSubpath()
                p.drawPath(cur)
    p.restore()

    # atmosphere glow band hugging the limb — bright thin line over a soft
    # falloff, sunset-orange while the terminator is in view
    Ro = R + 9.0
    rim = (L(120, 255, trans), L(205, 170, trans), L(255, 110, trans))
    ag = QRadialGradient(ecx, ecy, Ro)
    ag.setColorAt((R - 7.0) / Ro, QColor(rim[0], rim[1], rim[2], 0))
    ag.setColorAt((R - 1.0) / Ro, QColor(rim[0], rim[1], rim[2], 195))
    ag.setColorAt(min(1.0, (R + 4.0) / Ro), QColor(rim[0], rim[1], rim[2], 60))
    ag.setColorAt(1.0, QColor(rim[0], rim[1], rim[2], 0))
    p.setBrush(QBrush(ag))
    p.drawEllipse(QPointF(ecx, ecy), Ro, Ro)


def _draw_orbit(p: QPainter, A: Anim, t: float, pal: dict) -> None:
    """The long-task scene. STAGE SEP reads as an axis-aligned separation
    of a ~32-deg-tilted stack with a hot interstage glow (the reference
    storyboard), then the ship swings into coast attitude while the Earth
    turns underneath — terminator sweep, city lights, aurora, sunrise
    (one lap per ORBIT_CYCLE)."""
    sep_age = t - A.sep_t0
    rise = min(1.0, sep_age / 4.0)
    rise = rise * rise * (3 - 2 * rise)

    ecx = VX + VW * 0.5
    R = VW * 1.65
    ecy = VY + VH + R - VH * (0.26 + 0.05 * rise)

    lap = max(0.0, sep_age - 5.0) / ORBIT_CYCLE
    s2 = (lap * 2.0) % 1.0
    into_night = int(lap * 2.0) % 2 == 0
    term_x = VX - 40 + s2 * (VW + 80)
    dark_frac = s2 if into_night else 1.0 - s2
    dayness = 1.0 - dark_frac
    trans = max(0.0, 1.0 - abs(term_x - ecx) / (VW * 0.75))

    _draw_earth_disc(p, A, t, ecx, ecy, R, dayness, trans,
                     term_x, into_night, dark_frac)

    # ---- stage separation: tilted axis, hot-stage glow, booster recedes
    AXIS = 32.0
    ar = AXIS * pi / 180.0
    ux_, uy_ = sin(ar), -cos(ar)                 # along-axis (nose-ward)
    bx0 = VX + VW * 0.52
    by0 = VY + VH * 0.40

    if sep_age < 4.5:
        rec = 24.0 * sep_age + 13.0 * sep_age * sep_age
        p.save()
        p.translate(bx0 - ux_ * rec * 0.7, by0 - uy_ * rec * 0.5
                    + 14.0 * sep_age)
        p.rotate(AXIS + 8.0 * sep_age)
        p.setOpacity(max(0.0, 1.0 - sep_age / 4.2))
        _draw_stack(p, 0, BOOST_TGT * 0.5, pal["amb"], 0.0, t, ship=False)
        p.restore()

    # ---- the ship: slides forward along the axis, then settles to coast
    slide = min(1.0, sep_age / 1.5) * 34.0
    settle = min(1.0, max(0.0, (sep_age - 2.2) / 2.8))
    settle = settle * settle * (3 - 2 * settle)
    bob = (2.6 * sin(t * 0.5) + 1.4 * sin(t * 0.23 + 2.0)) * settle
    sx_ = (bx0 + ux_ * (44 + slide)) * (1 - settle) \
        + (VX + VW * 0.52) * settle
    sy_ = (by0 + uy_ * (44 + slide)) * (1 - settle) \
        + (VY + VH * 0.30 + bob) * settle
    rot = AXIS * (1 - settle) + (58.0 + 2.5 * sin(t * 0.31)) * settle
    p.save()
    p.translate(sx_, sy_)
    p.rotate(rot)
    spr = SPRITES.get("ship")
    if spr is not None:
        _draw_sprite(p, spr, 0, 33, 66.0)
    else:
        _draw_stack(p, 0, 60, pal["amb"], 0.0, t)    # no-asset fallback
    burn = max(0.0, 1.0 - sep_age / 2.2)
    if burn > 0.01:                              # push-away burn, then MECO
        _draw_plume(p, 0, 33, 12.0, burn * 0.9, t)
    elif settle >= 1.0:
        ph = (sep_age - 5.0) % 7.3               # RCS puffs, alternating
        if 0.0 <= ph < 0.4:
            side = int((sep_age - 5.0) / 7.3) % 2
            dir_ = -1.0 if side else 1.0
            yy0 = -33.0 if side else 33.0
            p.setBrush(QBrush(QColor(235, 245, 255,
                                     int(170 * (1.0 - ph / 0.4)))))
            puff = QPainterPath(QPointF(3, yy0))
            puff.lineTo(QPointF(10, yy0 + dir_ * 7))
            puff.lineTo(QPointF(-2, yy0 + dir_ * 5))
            puff.closeSubpath()
            p.drawPath(puff)
    p.restore()
    if sep_age < 2.2:                            # hot-stage glow ON TOP,
        ga = max(0.0, 1.0 - sep_age / 2.0)       # bridging the open gap
        gxp = bx0 + ux_ * (16 + 9 * sep_age)
        gyp = by0 + uy_ * (16 + 9 * sep_age)
        gg = QRadialGradient(gxp, gyp, 15)
        gg.setColorAt(0.0, QColor(246, 231, 208, int(235 * ga)))
        gg.setColorAt(0.4, QColor(235, 170, 90, int(170 * ga)))
        gg.setColorAt(1.0, QColor(235, 170, 90, 0))
        p.setBrush(QBrush(gg))
        p.drawEllipse(QPointF(gxp, gyp), 15, 15)


BOSS_W, BOSS_H = 84.0, 58.0       # Boss Cam PiP — the real-photo crops
                                  # in assets/boss/ are cut to this aspect

# Real-photo Boss Cam (the user's pick): assets/boss/<state>.png, PD/CC
# photos (see ATTRIBUTION.md). Loaded in load_assets(); when present they
# are the cam; the procedural cartoon below is only the no-asset fallback.
BOSS_PIX: dict = {}
BOSS_FALLBACK = {
    "idle": ("idle",),
    "working": ("working", "idle"),
    "go": ("idle", "done"),               # the GO-given grin: LET'S FLY.
    "asking": ("asking", "idle"),
    "done": ("done", "idle"),
    "confused": ("working", "idle"),     # glitches into SIGNAL LOST anyway
    "resting": (),                        # cam offline — he went home
}


def _cover_src(pm: QPixmap, tw: float, th: float) -> QRectF:
    """Center-cropped source rect covering a tw:th aspect target."""
    pr = pm.width() / pm.height()
    tr = tw / th
    if pr > tr:                                  # source wider → crop sides
        sh = pm.height()
        sw = sh * tr
        return QRectF((pm.width() - sw) / 2, 0, sw, sh)
    sw = pm.width()
    sh = sw / tr
    return QRectF(0, (pm.height() - sh) / 2, sw, sh)

# The Boss's hair silhouette, TRACED from the user's pixel-art reference
# (hatching-density column scan — hair = texture, forehead = smooth skin;
# uniform scale, hairline anchored at -12.5 on our forehead). Swept crest
# peaking left of center, high forehead, sideburn drop on the right.
_HAIR_TOP = [
    (-12.7, -17.5), (-11.6, -18.8), (-10.6, -19.7), (-9.6, -20.3),
    (-8.5, -20.7), (-7.5, -20.8), (-6.5, -20.7), (-5.4, -20.3),
    (-4.4, -20.0), (-3.4, -20.0), (-2.3, -20.0), (-1.3, -20.0),
    (-0.3, -20.0), (0.8, -20.0), (1.8, -20.0), (2.8, -19.9),
    (3.9, -19.2), (4.9, -18.0), (5.9, -16.5), (7.0, -15.8),
    (8.0, -15.8), (9.1, -15.7), (10.1, -15.1), (11.1, -13.4),
]
_HAIR_BOT = [
    (-12.7, -7.2), (-11.6, -8.5), (-10.6, -9.1), (-9.6, -10.6),
    (-8.5, -11.8), (-7.5, -12.3), (-6.5, -12.2), (-5.4, -12.2),
    (-4.4, -12.4), (-3.4, -12.3), (-2.3, -12.4), (-1.3, -12.5),
    (-0.3, -12.5), (0.8, -12.5), (1.8, -12.5), (2.8, -12.5),
    (3.9, -12.3), (4.9, -11.5), (5.9, -10.6), (7.0, -9.3),
    (8.0, -7.1), (9.1, -1.8), (10.1, 2.0), (11.1, 2.0),
]


def _draw_boss_cartoon(p: QPainter, bx, by, state: str, st_t: float,
                       t: float, alt: float = 0.0) -> None:
    """The Boss as an ANIMATED cartoon bust — fully procedural, no images.
    He blinks, his gaze tracks the rocket's actual altitude, he talks to
    the webcast, raises a skeptical eyebrow at you, facepalms at a RUD and
    fist-pumps the catch. Drawn into the cam box (bx,by .. BOSS_W,BOSS_H).

      idle      relaxed smirk, wandering gaze, blinks
      working   3/4 turn toward the pad, eyes climb with A.alt, gives
                commentary (mouth talk cycle every ~8s)
      asking    leans into the lens, one eyebrow up: your call
      done      grin + happy ∩∩ eyes + double fist pump + cam confetti
      confused  0.6s shock (wide eyes, o-mouth) → facepalm → feed glitch
      resting   (cam is offline, never drawn)
    """
    SKIN = QColor(243, 205, 178)
    SKIN_D = QColor(212, 166, 136)
    HAIR = QColor(66, 47, 36)
    BROW = QColor(52, 38, 30)
    INK = QColor(38, 30, 26)
    SUIT = QColor(27, 29, 36)
    SUIT_D = QColor(17, 19, 25)

    cx = bx + BOSS_W * 0.5

    # Backdrop: the flat friendly blue of the sticker caricatures — the
    # figure pops instead of sinking into a murky control room.
    g = QLinearGradient(0, by, 0, by + BOSS_H)
    g.setColorAt(0.0, QColor(94, 112, 158))
    g.setColorAt(1.0, QColor(62, 78, 120))
    p.setBrush(QBrush(g))
    p.drawRect(QRectF(bx, by, BOSS_W, BOSS_H))

    # ---- acting parameters
    facing = 0.0                     # 0 = at the lens, + = toward the pad
    brow_l = brow_r = 0.0            # raise (px up)
    brow_tilt = 0.0                  # inner ends dip = stern focus
    mouth = "smile"
    gaze_x = 0.6 * sin(t * 0.23)     # idle wander
    gaze_y = 0.2 * sin(t * 0.31)
    lean = 1.0
    bob = 0.8 * sin(t * 1.1)
    tilt = 2.2 * sin(t * 0.6)
    talk = 0.0
    palm_u = 0.0                     # facepalm progress 0..1
    shock = False

    if state == "working":
        facing = 0.55
        mouth = "flat"
        gaze_x, gaze_y = 1.1, -1.0 - 2.6 * alt   # eyes climb with the ship
        brow_tilt = 0.9
        ph = t % 8.0                              # commentary every ~8s
        talk = max(0.0, min(1.0, (ph - 1.2) * 3.0, (3.4 - ph) * 3.0))
        tilt = 1.2 * sin(t * 0.8)
    elif state == "asking":
        lean = 1.06 + 0.05 * max(0.0, 1.0 - st_t / 0.5) + 0.012 * sin(t * 2.4)
        brow_l = 1.8 + 0.5 * sin(t * 2.1)         # the skeptical eyebrow
        mouth = "smile"                           # confident smirk, not a
        gaze_x, gaze_y = 0.0, 0.2                 # grump; dead at the lens
        tilt = 3.0 + 1.2 * sin(t * 0.9)
    elif state == "done":
        mouth = "grin"
        brow_l = brow_r = 1.8
        bob = -2.6 * abs(sin(t * 5.4)) * max(0.35, exp(-st_t * 0.22))
        tilt = 3.5 * sin(t * 2.7)
    elif state == "confused":
        if st_t < 0.6:
            shock = True
            mouth = "o"
            brow_l = brow_r = 2.6
            gaze_x, gaze_y = 0.0, -0.8
        else:
            palm_u = max(0.0, min(1.0, (st_t - 0.6) / 0.5))
            mouth = "tight"
            brow_l = brow_r = -0.6
            tilt = 4.0 * palm_u
            bob += 1.5 * palm_u

    # Lean zoom, anchored at the cam's bottom-center. Base 1.05 fills the
    # frame like the reference stickers.
    lean *= 1.05
    p.save()
    p.translate(cx, by + BOSS_H)
    p.scale(lean, lean)
    p.translate(-cx, -(by + BOSS_H))
    p.setPen(Qt.NoPen)

    # sticker drop shadow behind the head
    p.setBrush(QBrush(QColor(20, 26, 46, 60)))
    p.drawEllipse(QPointF(cx + facing * 3.5 + 2.5, by + 27.0), 17.5, 20.0)

    # ---- body (static; the head animates on the neck)
    nx = cx + facing * 3.0
    p.setBrush(QBrush(SKIN_D))                            # neck
    p.drawRect(QRectF(nx - 5.2, by + 42, 10.4, 9))
    p.setBrush(QBrush(SUIT))                              # blazer
    p.drawPolygon(QPolygonF([QPointF(cx - 30, by + BOSS_H + 4),
                             QPointF(cx - 17, by + 46),
                             QPointF(cx + 17, by + 46),
                             QPointF(cx + 30, by + BOSS_H + 4)]))
    p.setBrush(QBrush(QColor(236, 233, 228)))             # white shirt V
    p.drawPolygon(QPolygonF([QPointF(nx - 7, by + 46),
                             QPointF(nx + 7, by + 46),
                             QPointF(nx, by + 58)]))
    p.setBrush(QBrush(SUIT_D))                            # lapels
    p.drawPolygon(QPolygonF([QPointF(nx - 8.5, by + 45),
                             QPointF(nx - 1.5, by + 46.5),
                             QPointF(nx - 5.5, by + 58)]))
    p.drawPolygon(QPolygonF([QPointF(nx + 8.5, by + 45),
                             QPointF(nx + 1.5, by + 46.5),
                             QPointF(nx + 5.5, by + 58)]))
    p.setBrush(QBrush(QColor(228, 96, 60)))               # tiny Mars pin
    p.drawEllipse(QPointF(cx + 13.0, by + 51), 1.3, 1.3)

    # ---- head group (likeness pass: quiff, wide cheeks, broad chin —
    #      proportions taken from the classic vector caricatures)
    hx = cx + facing * 3.5
    hy = by + 30.0 + bob
    p.save()
    p.translate(hx, hy)
    p.rotate(tilt)
    fdx = facing * 4.5                                    # feature slide

    # ears (half-hidden behind the cheeks)
    p.setBrush(QBrush(SKIN_D))
    p.drawEllipse(QPointF(-14.4 + fdx * 0.4, 0.5), 2.4, 3.6)
    if facing < 0.3:
        p.drawEllipse(QPointF(14.4 + fdx * 0.4, 0.5), 2.4, 3.6)

    # head — wide at the cheeks, broad rounded jaw/chin
    head = QPainterPath(QPointF(-13.6 + fdx * 0.4, -8.0))
    head.cubicTo(QPointF(-15.6 + fdx * 0.5, -1.0),
                 QPointF(-14.6 + fdx * 0.6, 9.0),
                 QPointF(-9.5 + fdx * 0.8, 15.0))
    head.cubicTo(QPointF(-5.5 + fdx, 18.2), QPointF(5.5 + fdx, 18.2),
                 QPointF(9.5 + fdx * 0.8, 15.0))
    head.cubicTo(QPointF(14.6 + fdx * 0.6, 9.0),
                 QPointF(15.6 + fdx * 0.5, -1.0),
                 QPointF(13.6 + fdx * 0.4, -8.0))
    head.cubicTo(QPointF(12.0 + fdx * 0.3, -15.5),
                 QPointF(-12.0 + fdx * 0.3, -15.5),
                 QPointF(-13.6 + fdx * 0.4, -8.0))
    p.setBrush(QBrush(SKIN))
    p.drawPath(head)

    # two-tone shading — darker plane down the far cheek + jaw
    p.setBrush(QBrush(QColor(SKIN_D.red(), SKIN_D.green(),
                             SKIN_D.blue(), 80)))
    shade = QPainterPath(QPointF(13.6 + fdx * 0.4, -8.0))
    shade.cubicTo(QPointF(15.6 + fdx * 0.5, -1.0),
                  QPointF(14.6 + fdx * 0.6, 9.0),
                  QPointF(9.5 + fdx * 0.8, 15.0))
    shade.cubicTo(QPointF(11.8 + fdx * 0.8, 9.5),
                  QPointF(12.4 + fdx * 0.5, -1.0),
                  QPointF(11.4 + fdx * 0.4, -8.5))
    shade.closeSubpath()
    p.drawPath(shade)

    # hair — THE QUIFF, built like the vector cartoons: a darker BACK MASS
    # over the skull, then a lighter FRONT LOCK that rises off the forehead,
    # crests, and sweeps back. Two tones = volume; the lock's wavy bottom
    # edge = the swoosh.
    # Traced silhouette, display-size cleanup: trim the wide left flare,
    # cap the deep temple notches (they read as bites at 1x), extend the
    # right edge to the temple, and pull the bottom onto OUR skull.
    hairp = QPainterPath()
    first = True
    for x_, y_ in _HAIR_TOP:                                # silhouette top
        if x_ < -13.2:
            continue
        pt = QPointF(x_ + fdx * 0.7, y_)
        if first:
            hairp.moveTo(pt)
            first = False
        else:
            hairp.lineTo(pt)
    hairp.lineTo(QPointF(11.8 + fdx * 0.7, -13.0))          # right temple
    hairp.lineTo(QPointF(12.4 + fdx * 0.7, -7.5))
    for x_, y_ in reversed(_HAIR_BOT):                      # hairline back
        if x_ < -13.2:
            continue
        yc = y_
        if abs(x_) <= 4.5:
            ys_ = -15.4 * (1.0 - (x_ / 12.6) ** 2) ** 0.5
            yc = max(y_, ys_ + 2.9)                         # onto the skull
        elif abs(x_) < 11.5:
            yc = max(y_, -11.0)                             # no deep notch
        hairp.lineTo(QPointF(x_ + fdx * 0.7, yc))
    hairp.closeSubpath()
    lg = QLinearGradient(0, -21.0, 0, -2.0)
    lg.setColorAt(0.0, QColor(138, 99, 67))                 # lit caramel
    lg.setColorAt(0.55, QColor(102, 72, 50))
    lg.setColorAt(1.0, QColor(74, 53, 39))
    p.setBrush(QBrush(lg))
    p.drawPath(hairp)

    # chunky highlight locks along the sweep (the pixel-art texture)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(174, 130, 90, 190), 1.4,
                  Qt.SolidLine, Qt.RoundCap))
    s1 = QPainterPath(QPointF(-8.5 + fdx, -13.5))
    s1.quadTo(QPointF(-5.0 + fdx, -18.5), QPointF(-0.5 + fdx, -19.0))
    p.drawPath(s1)
    s2 = QPainterPath(QPointF(-2.0 + fdx, -13.5))
    s2.quadTo(QPointF(2.0 + fdx, -17.0), QPointF(6.0 + fdx, -16.4))
    p.drawPath(s2)
    s3 = QPainterPath(QPointF(5.0 + fdx, -12.4))
    s3.quadTo(QPointF(7.5 + fdx, -14.4), QPointF(9.6 + fdx, -13.2))
    p.drawPath(s3)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(74, 53, 39)))                  # sideburns hug
    p.drawRect(QRectF(-13.9 + fdx * 0.5, -6.0, 1.5, 6.0))   # the face edge
    if facing < 0.3:
        p.drawRect(QRectF(12.4 + fdx * 0.5, -4.5, 1.5, 5.0))

    # eyes — narrow, heavy-lidded; blink on two interleaved clocks
    bp = (t * 0.27 + 0.13) % 1.0
    bp2 = (t * 0.41 + 0.71) % 1.0
    closed = bp < 0.045 or bp2 < 0.03
    eye_l = QPointF(-6.4 + fdx, -2.4)
    eye_r = QPointF(6.4 + fdx, -2.4)
    if state == "done":                                   # happy ∩∩
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(INK, 1.7, Qt.SolidLine, Qt.RoundCap))
        for e in (eye_l, eye_r):
            arc = QPainterPath(QPointF(e.x() - 3.0, e.y() + 1.2))
            arc.quadTo(QPointF(e.x(), e.y() - 2.9),
                       QPointF(e.x() + 3.0, e.y() + 1.2))
            p.drawPath(arc)
        p.setPen(Qt.NoPen)
    elif closed:
        p.setPen(QPen(INK, 1.3, Qt.SolidLine, Qt.RoundCap))
        for e in (eye_l, eye_r):
            p.drawLine(QPointF(e.x() - 2.8, e.y() + 0.7),
                       QPointF(e.x() + 2.8, e.y() + 0.7))
        p.setPen(Qt.NoPen)
    else:
        wide = 1.35 if shock else 1.0
        for e in (eye_l, eye_r):
            p.setBrush(QBrush(QColor(250, 250, 248)))
            p.drawEllipse(e, 3.3, 2.2 * wide)
            pup = QPointF(e.x() + max(-1.7, min(1.7, gaze_x)),
                          e.y() + max(-1.1, min(1.1, gaze_y)))
            p.setBrush(QBrush(QColor(86, 54, 38)))        # brown iris
            p.drawEllipse(pup, 1.65, 1.65)
            p.setBrush(QBrush(INK))
            p.drawEllipse(pup, 0.95 if not shock else 0.7,
                          0.95 if not shock else 0.7)
            p.setBrush(QBrush(QColor(255, 255, 255, 235)))
            p.drawEllipse(QPointF(pup.x() - 0.5, pup.y() - 0.5), 0.5, 0.5)
        # heavy upper lids
        p.setPen(QPen(QColor(170, 128, 102), 1.2, Qt.SolidLine, Qt.RoundCap))
        for e in (eye_l, eye_r):
            p.drawLine(QPointF(e.x() - 3.1, e.y() - 2.0 * wide),
                       QPointF(e.x() + 3.1, e.y() - 1.8 * wide))
        p.setPen(Qt.NoPen)

    # brows — thicker, gently arched; the acting muscles
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(BROW, 2.4, Qt.SolidLine, Qt.RoundCap))
    bl = QPainterPath(QPointF(eye_l.x() + 3.8, -6.0 - brow_l + brow_tilt))
    bl.quadTo(QPointF(eye_l.x() + 0.2, -8.0 - brow_l),
              QPointF(eye_l.x() - 3.6, -6.4 - brow_l))
    p.drawPath(bl)
    br = QPainterPath(QPointF(eye_r.x() - 3.8, -6.0 - brow_r + brow_tilt))
    br.quadTo(QPointF(eye_r.x() - 0.2, -8.0 - brow_r),
              QPointF(eye_r.x() + 3.6, -6.4 - brow_r))
    p.drawPath(br)
    p.setPen(Qt.NoPen)

    # nose — straight bridge with a side shadow, rounded tip
    p.setPen(QPen(SKIN_D.darker(108), 1.2, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(0.7 + fdx, -1.8), QPointF(1.3 + fdx, 3.2))
    tip = QPainterPath(QPointF(-1.2 + fdx, 4.8))
    tip.quadTo(QPointF(0.4 + fdx, 6.0), QPointF(2.0 + fdx, 4.5))
    p.setBrush(Qt.NoBrush)
    p.drawPath(tip)
    # chin crease
    p.drawLine(QPointF(fdx * 0.8 - 1.0, 14.8), QPointF(fdx * 0.8 + 1.2, 14.8))
    p.setPen(Qt.NoPen)

    # mouth — small and low on the face
    mx = fdx * 0.9
    if mouth == "grin":
        mp = QPainterPath(QPointF(mx - 5.6, 8.6))
        mp.quadTo(QPointF(mx, 9.9), QPointF(mx + 5.6, 8.6))
        mp.quadTo(QPointF(mx + 1.0, 15.2), QPointF(mx - 5.6, 8.6))
        p.setBrush(QBrush(INK))
        p.drawPath(mp)
        p.setBrush(QBrush(QColor(248, 246, 242)))
        p.drawRoundedRect(QRectF(mx - 4.0, 9.2, 8.0, 2.2), 1.1, 1.1)
    elif mouth == "o":
        p.setBrush(QBrush(INK))
        p.drawEllipse(QPointF(mx, 11.0), 2.2, 2.8)
    elif mouth == "flat" and talk > 0.05:
        p.setBrush(QBrush(INK))                  # commentary
        p.drawEllipse(QPointF(mx, 10.6),
                      3.0, 0.8 + 2.2 * talk * abs(sin(t * 10.5)))
    else:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(INK, 1.6, Qt.SolidLine, Qt.RoundCap))
        if mouth == "smile":                     # the smirk
            sp = QPainterPath(QPointF(-4.4 + mx, 10.0))
            sp.cubicTo(QPointF(-1.2 + mx, 11.8), QPointF(2.6 + mx, 11.6),
                       QPointF(4.8 + mx, 9.4))
            p.drawPath(sp)
        else:                                    # tight / flat
            p.drawLine(QPointF(mx - 3.4, 10.3), QPointF(mx + 3.6, 10.1))
        p.setPen(Qt.NoPen)
        # fuller lower lip — shadow dab beneath
        p.setPen(QPen(SKIN_D.darker(106), 1.2, Qt.SolidLine, Qt.RoundCap))
        lp = QPainterPath(QPointF(-2.6 + mx, 13.0))
        lp.quadTo(QPointF(0.4 + mx, 13.9), QPointF(3.2 + mx, 12.7))
        p.drawPath(lp)
        p.setPen(Qt.NoPen)

    p.restore()                                  # end head group

    # facepalm — hand sweeps up over the face
    if palm_u > 0.0:
        u = palm_u
        hx_t, hy_t = hx + 1.5, hy - 1.0
        hx_0, hy_0 = cx + 22.0, by + 54.0
        px_ = hx_0 + (hx_t - hx_0) * u
        py_ = hy_0 + (hy_t - hy_0) * (u * u * (3 - 2 * u))
        p.setPen(QPen(SUIT, 6.5, Qt.SolidLine, Qt.RoundCap))   # sleeve
        p.drawLine(QPointF(cx + 26, by + BOSS_H + 2), QPointF(px_ + 3, py_ + 4))
        p.setPen(Qt.NoPen)
        p.save()
        p.translate(px_, py_)
        p.rotate(-24 + 16 * u)
        p.setBrush(QBrush(SKIN))
        p.drawRoundedRect(QRectF(-4.6, -6.2, 9.2, 12.4), 3.5, 3.5)
        p.setPen(QPen(SKIN_D, 0.9))
        for fx_ in (-2.2, 0.0, 2.2):                            # fingers
            p.drawLine(QPointF(fx_, -5.4), QPointF(fx_, 2.0))
        p.setPen(Qt.NoPen)
        p.restore()

    # the catch celebration — double fist pump from the bottom corners
    if state == "done":
        pop = min(1.0, st_t / 0.35)
        pump = 3.2 * abs(sin(t * 5.4)) * max(0.35, exp(-st_t * 0.22))
        for sgn in (-1.0, 1.0):
            fx_ = cx + sgn * 27.0
            fy_ = by + 47.0 - 11.0 * pop - pump
            p.setPen(QPen(SUIT, 6.5, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx + sgn * 31, by + BOSS_H + 4),
                       QPointF(fx_, fy_ + 3))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(SKIN))
            p.drawEllipse(QPointF(fx_, fy_), 4.0, 4.4)
        for i in range(9):                                      # cam confetti
            cxx = bx + (i * 9.3 + 4.0 + 6.0 * sin(t * 0.9 + i)) % BOSS_W
            cyy = by + ((t * 26.0 + i * 19.7) % BOSS_H)
            col = ((120, 240, 150), (255, 210, 110),
                   (140, 200, 255))[i % 3]
            p.setBrush(QBrush(QColor(*col, 190)))
            p.drawRect(QRectF(cxx, cyy, 2.0, 2.0))

    p.restore()                                  # end lean


def _draw_boss_cam(p: QPainter, state: str, st_t: float, t: float,
                   alt: float = 0.0) -> None:
    """The BOSS CAM — a picture-in-picture host camera, the way real launch
    webcasts cut to the founder watching from the firing room. The face is
    the animated cartoon from _draw_boss_cartoon; this wraps it in the
    broadcast frame: scanlines, REC dot, chyron, glitch → SIGNAL LOST on a
    RUD, OFFLINE card at night."""
    bx = VX + 5.0
    by = VY + VH - BOSS_H - 5.0
    frame = QRectF(bx - 1.5, by - 1.5, BOSS_W + 3, BOSS_H + 3)
    inner = QRectF(bx, by, BOSS_W, BOSS_H)

    p.setPen(QPen(QColor(95, 102, 118, 200), 1.0))
    p.setBrush(QBrush(QColor(10, 12, 16)))
    p.drawRoundedRect(frame, 5, 5)
    p.setPen(Qt.NoPen)

    f = QFont("Menlo", 7)
    f.setBold(True)

    if state == "resting":                       # cam off for the night
        p.setFont(f)
        p.setPen(QColor(110, 120, 140, 210))
        p.drawText(inner.adjusted(0, -8, 0, -8), Qt.AlignCenter, "BOSS CAM")
        p.setPen(QColor(150, 160, 180, 150))
        p.drawText(inner.adjusted(0, 8, 0, 8), Qt.AlignCenter, "OFFLINE")
        p.setPen(Qt.NoPen)
        on = sin(t * 2.2) > 0.6
        p.setBrush(QBrush(QColor(255, 70, 60, 200 if on else 50)))
        p.drawEllipse(QPointF(bx + 7, by + 7), 1.8, 1.8)
        return

    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(inner, 4, 4)
    p.setClipPath(clip, Qt.IntersectClip)

    pm = None
    for cand in BOSS_FALLBACK.get(state, ()):
        pm = BOSS_PIX.get(cand)
        if pm is not None:
            break

    lost = state == "confused" and st_t > (0.45 if pm is not None else 2.2)
    if lost:
        # Static snow — the anomaly took the feed down too.
        for _ in range(110):
            g_ = random.randint(20, 150)
            p.setBrush(QBrush(QColor(g_, g_, g_)))
            p.drawRect(QRectF(bx + random.uniform(0, BOSS_W - 2),
                              by + random.uniform(0, BOSS_H - 2), 2.2, 2.2))
        if sin(t * 5.0) > -0.3:
            p.setFont(f)
            p.setPen(QColor(255, 245, 245, 235))
            p.drawText(inner, Qt.AlignCenter, "SIGNAL LOST")
            p.setPen(Qt.NoPen)
    else:
        if pm is not None:
            # THE REAL FACE — Ken Burns drift + scanlines + REC dot sell
            # "live camera" on a still photo.
            src = _cover_src(pm, BOSS_W, BOSS_H)
            zoom = 1.06 + 0.04 * sin(st_t * 0.13 + (hash(state) % 7))
            if state == "asking":                # he leans toward the lens
                zoom += 0.03 + 0.10 * max(0.0, 1.0 - st_t / 0.5)
            elif state == "done":
                zoom += 0.08 * max(0.0, 1.0 - st_t / 0.4)
            dx_ = 0.018 * src.width() * sin(st_t * 0.07 + 1.2)
            dy_ = 0.012 * src.height() * sin(st_t * 0.05 + 4.0)
            sw = src.width() / zoom
            sh = src.height() / zoom
            sx = src.x() + (src.width() - sw) / 2 + dx_
            sy = src.y() + (src.height() - sh) / 2 + dy_
            sx = max(0.0, min(pm.width() - sw, sx))
            sy = max(0.0, min(pm.height() - sh, sy))
            zsrc = QRectF(sx, sy, sw, sh)
            if state == "confused":              # glitching out (pre-loss)
                for _ in range(6):
                    ty = by + random.uniform(0, BOSS_H - 5)
                    hh = random.uniform(3, 9)
                    ox = random.uniform(-7, 7)
                    ssy = zsrc.y() + (ty - by) / BOSS_H * zsrc.height()
                    p.drawPixmap(QRectF(bx + ox, ty, BOSS_W, hh), pm,
                                 QRectF(zsrc.x(), ssy, zsrc.width(),
                                        hh / BOSS_H * zsrc.height()))
            else:
                p.drawPixmap(inner, pm, zsrc)
        else:
            # No photos on disk → the animated cartoon fallback.
            _draw_boss_cartoon(p, bx, by, state, st_t, t, alt)
            if state == "confused" and st_t > 1.6:   # feed starts tearing
                for _ in range(5):
                    gy_ = by + random.uniform(0, BOSS_H - 4)
                    col = ((90, 240, 255), (255, 80, 200),
                           (255, 255, 255))[random.randint(0, 2)]
                    p.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                             random.randint(60, 130))))
                    p.drawRect(QRectF(bx, gy_, BOSS_W,
                                      random.uniform(1.5, 5)))

        if state == "asking":                    # amber alert wash
            aa = int(30 + 26 * (0.5 + 0.5 * sin(t * 4.0)))
            p.setBrush(QBrush(QColor(255, 170, 60, aa)))
            p.drawRect(inner)
        elif state == "done" and st_t < 0.5:     # entry flash
            p.setBrush(QBrush(QColor(255, 255, 255,
                                     int(150 * (1 - st_t / 0.5)))))
            p.drawRect(inner)

        chy = {"asking": "YOUR CALL.", "go": "LET'S FLY.",
               "done": "LET'S GO!!"}.get(state, "")
        if chy:                                  # lower-third chyron
            g = QLinearGradient(0, by + BOSS_H - 17, 0, by + BOSS_H)
            g.setColorAt(0.0, QColor(8, 10, 14, 0))
            g.setColorAt(1.0, QColor(8, 10, 14, 215))
            p.setBrush(QBrush(g))
            p.drawRect(QRectF(bx, by + BOSS_H - 17, BOSS_W, 17))
            f6 = QFont("Menlo", 6)
            f6.setBold(True)
            p.setFont(f6)
            p.setPen(QColor(255, 200, 110, 245) if state == "asking"
                     else QColor(235, 240, 248, 235))
            p.drawText(QRectF(bx, by + BOSS_H - 13, BOSS_W, 11),
                       Qt.AlignCenter, chy)
            p.setPen(Qt.NoPen)

    # Cam's own scanlines.
    p.setBrush(QBrush(QColor(0, 0, 0, 22)))
    yy = by
    while yy < by + BOSS_H:
        p.drawRect(QRectF(bx, yy, BOSS_W, 1))
        yy += 3

    # REC dot + label.
    if not lost and sin(t * 3.4) > -0.4:
        p.setBrush(QBrush(QColor(255, 60, 60, 235)))
        p.drawEllipse(QPointF(bx + 7, by + 7), 2.0, 2.0)
    f7 = QFont("Menlo", 6)
    f7.setBold(True)
    p.setFont(f7)
    p.setPen(QColor(235, 240, 248, 205))
    p.drawText(QRectF(bx + 12, by + 2, BOSS_W - 14, 10),
               Qt.AlignVCenter | Qt.AlignLeft, "BOSS CAM")
    p.setPen(Qt.NoPen)
    p.restore()


def render_card(p: QPainter, state: str, t: float, st_t: float, A: Anim,
                pal: dict, flight_n: int, caption: str = "") -> None:
    """The whole device. Pure draw — testable offscreen."""
    # Device bezel (solid → safe over any wallpaper).
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(26, 28, 34)))
    p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 14, 14)
    p.setPen(QPen(QColor(80, 86, 100, 90), 1.0))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(1.2, 1.2, W - 2.4, H - 2.4), 13, 13)
    p.setPen(Qt.NoPen)

    # Header: LIVE dot + title.
    pulse = 0.55 + 0.45 * sin(t * 2.6)
    p.setBrush(QBrush(QColor(255, 60, 60, int(160 + 90 * pulse))))
    p.drawEllipse(QPointF(VX + 7, BEZEL + 7), 3.0, 3.0)
    f = QFont("Menlo", 8); f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(225, 232, 242, 220))
    p.drawText(QRectF(VX + 14, BEZEL, 120, 14), Qt.AlignVCenter | Qt.AlignLeft,
               "MAX-Q · LIVE")
    p.setPen(QColor(140, 150, 165, 180))
    p.drawText(QRectF(VX, BEZEL, VW - 4, 14), Qt.AlignVCenter | Qt.AlignRight,
               f"FLT {flight_n:03d}")
    p.setPen(Qt.NoPen)

    # ---------------- viewport (clipped) ----------------
    p.save()
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(VX, VY, VW, VH), 8, 8)
    p.setClipPath(clip)

    # Screen shake.
    if A.shake > 0.01:
        p.translate(random.uniform(-1, 1) * 2.2 * A.shake,
                    random.uniform(-1, 1) * 1.8 * A.shake)

    alt = A.alt
    # Sky: 3-stop gradient (grey-mauve mid band) → space-black with altitude.
    # alt^1.5 keeps the sky blue through the early ascent (the references
    # are still daylit at T+11); space takes over in the upper half.
    sky_k = alt ** 1.5
    top_c = tuple(int(_lerp(pal["sky_top"][i], 6, sky_k)) for i in range(3))
    mid_c = tuple(int(_lerp(pal["sky_mid"][i], 10, sky_k)) for i in range(3))
    bot_c = tuple(int(_lerp(pal["sky_bot"][i], 18, sky_k * 0.92))
                  for i in range(3))
    sky = QLinearGradient(0, VY, 0, VY + VH)
    sky.setColorAt(0.0, C(top_c))
    sky.setColorAt(0.55, C(mid_c))
    sky.setColorAt(1.0, C(bot_c))
    p.setBrush(QBrush(sky))
    p.drawRect(QRectF(VX, VY, VW, VH))

    # Stars — night palette or altitude; varied sizes, a few cool-tinted.
    # They only come out above ~alt 0.3 (still daylight at T+11).
    star_vis = max(pal["star_a"], (alt - 0.3) * 1.7)
    if star_vis > 0.03:
        for sx_, sy_, ph, sz, ba, cool in A.stars:
            tw_ = 0.5 + 0.5 * sin(t * 1.9 + ph)
            a = min(1.0, star_vis) * ba * (0.35 + 0.65 * tw_)
            col = (210, 220, 255) if cool else (255, 255, 255)
            p.setBrush(QBrush(QColor(col[0], col[1], col[2], int(230 * a))))
            p.drawEllipse(QPointF(VX + sx_ * VW, VY + sy_ * VH * 0.8), sz, sz)

    # Clouds (fade with altitude — you climb past them).
    ca = pal["cloud_a"] * max(0.0, 1.0 - alt * 2.2)
    if ca > 0.03:
        for c in A.clouds:
            cx = VX + c["x"] * VW
            cy = VY + c["y"] * VH + alt * VH * 1.5   # rush down as we climb
            s = c["s"]
            p.setBrush(QBrush(QColor(255, 255, 255, int(100 * ca))))
            cl = QPainterPath()
            cl.setFillRule(Qt.WindingFill)
            for dx, dy, r in ((-12, 2, 8), (0, -2, 11), (11, 2, 8)):
                cl.addEllipse(QPointF(cx + dx * s, cy + dy * s),
                              r * s, r * s * 0.6)
            p.drawPath(cl)

    in_flight = (state == "working" and st_t >= A.cd + 1.8)
    ground_off = min(VH * 1.4, (alt * 3.0) * VH) if in_flight else 0.0
    gy = GROUND_Y + ground_off

    # Ground + pad hardware (scrolls away during ascent).
    if gy < VY + VH + 30:
        amb = pal["amb"]
        night = max(0.0, min(1.0, (0.62 - amb) / 0.3))
        if night > 0.05:
            # horizon glow band + distant skyline with a few lit windows
            hg = QLinearGradient(0, gy - 22, 0, gy)
            hg.setColorAt(0.0, QColor(44, 51, 74, 0))
            hg.setColorAt(1.0, QColor(44, 51, 74, int(190 * night)))
            p.setBrush(QBrush(hg))
            p.drawRect(QRectF(VX, gy - 22, VW, 22))
            p.setBrush(QBrush(QColor(34, 41, 70, int(255 * night))))
            for bx_, bw_, bh_ in A.skyline:
                p.drawRect(QRectF(VX + bx_ * VW, gy - bh_, bw_ * VW, bh_))
            for wx_, wy_, warm in A.windows:
                col = (190, 170, 120, int(200 * night)) if warm \
                    else (150, 160, 190, int(160 * night))
                p.setBrush(QBrush(QColor(*col)))
                p.drawRect(QRectF(VX + wx_ * VW, gy - wy_, 1.2, 1.2))
        g = QLinearGradient(0, gy, 0, VY + VH)
        g.setColorAt(0.0, C(mul(pal["ground"], 1.0)))
        g.setColorAt(1.0, C(mul(pal["ground"], 0.55)))
        p.setBrush(QBrush(g))
        p.drawRect(QRectF(VX, gy, VW, VY + VH - gy + 2))

        # Launch table: charcoal A-frame with splayed legs (daylight shows
        # under the booster, like the reference ignition frame).
        pad_c = C(mul(pal["pad"], 1.0))
        p.setBrush(QBrush(pad_c))
        p.drawRect(QRectF(GCX_PAD - 12, gy - MOUNT_H, 24, MOUNT_H))
        p.setBrush(QBrush(C(mul(pal["pad"], 1.35))))
        p.drawRect(QRectF(GCX_PAD - 13, gy - MOUNT_H, 26, 2))

        # Tank farm + low buildings (pad environs, right of the tower).
        for i, tx_ in enumerate((0.855, 0.905, 0.955)):
            tx = VX + VW * tx_
            tk = C(mul((150, 151, 153), amb))
            p.setBrush(QBrush(tk))
            p.drawRoundedRect(QRectF(tx - 4.5, gy - 19, 9, 19), 3.5, 3.5)
            p.setBrush(QBrush(C(mul((196, 198, 200), amb))))
            p.drawRoundedRect(QRectF(tx - 3.2, gy - 18, 2.2, 16), 1.1, 1.1)
        p.setBrush(QBrush(C(mul((118, 116, 118), amb))))
        p.drawRoundedRect(QRectF(VX + VW * 0.80, gy - 7, 17, 7), 2, 2)
        p.setBrush(QBrush(C(mul((86, 84, 90), amb))))
        p.drawRect(QRectF(VX + VW * 0.015, gy - 7, 16, 7))
        # Tower.
        arms_open, arms_y = 1.0, 1.0
        if state == "done":
            cs = min(1.0, max(0.0, (st_t - 2.4) / 0.7))
            arms_open = 1.0 - cs                  # close around the booster
            arms_y = 0.45                         # catch height on the tower
        elif state == "asking":
            arms_open, arms_y = 0.55, 0.55
        elif state in ("idle", "resting"):
            arms_open, arms_y = 0.75, 0.55        # parked open, standing by
        if state == "done":
            # Booster first, tower after — the closing chopsticks must
            # overlap the booster for the grip to read.
            catch_x = TOWER_X - 26
            catch_ay = gy - TOWER_LAT * (0.52 + 0.34 * 0.45)
            final_by = catch_ay - 6               # dangles in the grip
            drop = min(1.0, st_t / 2.4)
            ease = 1 - (1 - drop) ** 3
            by = VY - 130 + ease * (final_by - (VY - 130))
            burn = 0.9 if st_t < 2.4 else max(0.0, 0.9 - (st_t - 2.4) * 3)
            p.save()
            p.translate(catch_x, by + 6)          # pivot at the grip point
            p.rotate(2.5 * ease)                  # hangs slightly canted
            _draw_stack(p, 0, BOOST_TGT - 6, pal["amb"], burn, t,
                        ship=False)
            p.restore()
        _draw_tower(p, TOWER_X, gy, pal["amb"], arms_open, arms_y,
                    pal["strobe_a"], pal["beacon_a"], t)

        # The Boss in the wide shot (gone at night) — silhouette fallback
        # only; with the real-photo cam loaded he's on the Boss Cam.
        if state != "resting" and not BOSS_PIX:
            _draw_watcher(p, VX + VW * 0.86, gy + 14, state, st_t, t,
                          pal["amb"], A.alt)

        # The vehicle on/near the pad.
        if state == "working":
            if st_t < A.cd + 1.8:                 # countdown + ignition
                burn = max(0.0, min(1.0, (st_t - A.cd) / 0.9))
                if burn > 0.3:
                    # fire light spilling onto the pad smoke
                    fg = QRadialGradient(GCX_PAD, gy - 4, 42)
                    fg.setColorAt(0.0, QColor(255, 201, 160,
                                              int(120 * burn)))
                    fg.setColorAt(1.0, QColor(255, 160, 90, 0))
                    p.setBrush(QBrush(fg))
                    p.drawEllipse(QPointF(GCX_PAD, gy - 4), 46, 26)
                _draw_stack(p, GCX_PAD, gy - MOUNT_H, amb, burn, t)
                if burn > 0.05:
                    # hold-down steam jets shooting sideways off the stack
                    for hgt in (44, 64, 84):
                        for sgn in (-1.0, 1.0):
                            ln = (24 + 9 * sin(t * 13 + hgt)) * burn
                            jx = GCX_PAD + sgn * 8.5
                            jy = gy - MOUNT_H - hgt
                            jg = QLinearGradient(jx, 0, jx + sgn * ln, 0)
                            jg.setColorAt(0.0, QColor(228, 222, 216, 190))
                            jg.setColorAt(1.0, QColor(228, 222, 216, 0))
                            p.setBrush(QBrush(jg))
                            p.drawPolygon(QPolygonF([
                                QPointF(jx, jy - 1.5),
                                QPointF(jx + sgn * ln, jy - 3.5),
                                QPointF(jx + sgn * ln, jy + 4.5),
                                QPointF(jx, jy + 2.5)]))
            # else: vehicle is the flight sprite below
        elif state == "done":
            # grip-contact flashes as the chopsticks clamp on
            if 2.9 < st_t < 3.6:
                fa = max(0.0, 1.0 - (st_t - 2.9) / 0.7)
                for sgn in (-1.0, 1.0):
                    fgl = QRadialGradient(TOWER_X - 26 + sgn * 9,
                                          gy - TOWER_LAT * 0.673, 8)
                    fgl.setColorAt(0.0, QColor(255, 240, 200,
                                               int(230 * fa)))
                    fgl.setColorAt(1.0, QColor(255, 190, 110, 0))
                    p.setBrush(QBrush(fgl))
                    p.drawEllipse(QPointF(TOWER_X - 26 + sgn * 9,
                                          gy - TOWER_LAT * 0.673), 8, 8)
        elif state == "confused":
            if st_t > 0.3:
                pass                              # it... isn't there anymore
            else:
                _draw_stack(p, GCX_PAD, gy - MOUNT_H, amb, 0.0, t)
        else:
            _draw_stack(p, GCX_PAD, gy - MOUNT_H, amb, 0.0, t)

    # In-flight vehicle — ascent chase-cam, then orbital coast after sep.
    if in_flight and A.sep_done:
        _draw_orbit(p, A, t, pal)
    elif in_flight:
        # Earth's limb rises into view during the LATE ascent — the
        # curvature appears before stage sep, like the reference frames.
        if alt > 0.55:
            r01 = (alt - 0.55) / 0.45
            r01 = r01 * r01 * (3 - 2 * r01)
            R_ = VW * 1.65
            _draw_earth_disc(p, A, t, VX + VW * 0.5,
                             VY + VH + R_ - VH * 0.26 * r01, R_,
                             1.0, 0.0, None, False, 0.0)
        fy = VY + VH * 0.52 - min(1.0, (st_t - A.cd - 1.8) / 2.0) * VH * 0.10
        p.save()
        p.translate(VX + VW * 0.5, fy)
        p.rotate(2.0 + 1.2 * sin(t * 0.8))        # gentle gravity-turn lean
        # Exhaust cone — translucent, widening to ~4x body width.
        tw_ = 26.0 + 38.0 * alt
        cone_len = VH * 0.50
        trail = QLinearGradient(0, 86, 0, 86 + cone_len)
        trail.setColorAt(0.0, QColor(255, 214, 150, 100))
        trail.setColorAt(0.30, QColor(215, 225, 232, 60))
        trail.setColorAt(1.0, QColor(215, 225, 232, 0))
        p.setBrush(QBrush(trail))
        tpath = QPainterPath(QPointF(-5, 86))
        tpath.quadTo(QPointF(-tw_ * 0.5, 86 + cone_len * 0.55),
                     QPointF(-tw_ * 0.62, 86 + cone_len))
        tpath.lineTo(QPointF(tw_ * 0.62, 86 + cone_len))
        tpath.quadTo(QPointF(tw_ * 0.5, 86 + cone_len * 0.55),
                     QPointF(5, 86))
        tpath.closeSubpath()
        p.drawPath(tpath)
        _draw_stack(p, 0, 85, pal["amb"], 1.0, t)
        # MAX-Q — the condensation collar in the thick air. Name-drop.
        if 0.06 < alt < 0.22:
            qa = max(0.0, min(1.0, 1.0 - abs(alt - 0.14) / 0.08))
            qa *= 0.7 + 0.3 * sin(t * 23)
            p.setBrush(QBrush(QColor(255, 255, 255, int(95 * qa))))
            p.drawEllipse(QPointF(0, -64), 16 + 6 * qa, 6.5 + 3 * qa)
            f8 = QFont("Menlo", 6)
            f8.setBold(True)
            p.setFont(f8)
            p.setPen(QColor(255, 255, 255, int(150 * qa)))
            p.drawText(QRectF(-40, -100, 80, 10), Qt.AlignCenter, "MAX-Q")
            p.setPen(Qt.NoPen)
        p.restore()

    # Smoke / steam (sculpted two-grey cumulus during liftoff);
    # tinted down by the scene's ambient so night vents don't glow.
    for s in A.smoke:
        col = mul(s["col"], max(0.45, pal["amb"]))
        p.setBrush(QBrush(QColor(col[0], col[1], col[2],
                                 int(150 * max(0.0, s["a"])))))
        p.drawEllipse(QPointF(s["x"], s["y"] + (ground_off if in_flight else 0)),
                      s["r"], s["r"] * 0.85)

    # RUD: white flash → orange fireball core → ember + dark debris.
    if state == "confused":
        fdt = st_t - 0.3
        if 0.0 <= fdt <= 0.35:
            fa = exp(-fdt * 9)
            p.setBrush(QBrush(QColor(255, 230, 190, int(200 * fa))))
            p.drawRect(QRectF(VX, VY, VW, VH))
        if 0.0 <= fdt <= 1.4:
            fb = max(0.0, 1.0 - fdt / 1.4)
            fr = 20 + 26 * fdt
            fg2 = QRadialGradient(GCX_PAD, GROUND_Y - 110, fr)
            fg2.setColorAt(0.0, QColor(255, 243, 214, int(235 * fb)))
            fg2.setColorAt(0.45, QColor(255, 178, 77, int(180 * fb)))
            fg2.setColorAt(1.0, QColor(226, 84, 44, 0))
            p.setBrush(QBrush(fg2))
            p.drawEllipse(QPointF(GCX_PAD, GROUND_Y - 110), fr, fr)
        for i, d in enumerate(A.debris):
            if i % 2:
                p.setBrush(QBrush(QColor(255, 150, 70, 235)))   # embers
            else:
                p.setBrush(QBrush(QColor(70, 64, 60, 230)))
            p.drawRect(QRectF(d["x"] - 1.5, d["y"] - 1.5, 3, 3))

    # Two-tier mission overlays: HOLD (asking) and the GO-GIVEN countdown
    # beat (working start, right after the user answers) share the type.
    def _mission_text(big, sub, color, blink=True):
        f2 = QFont("Menlo", 16)
        f2.setBold(True)
        p.setFont(f2)
        if blink:
            p.setPen(QColor(20, 16, 8, 170))
            p.drawText(QRectF(VX + 1, VY + 17, VW, 24), Qt.AlignCenter, big)
            p.setPen(QColor(color[0], color[1], color[2], 250))
            p.drawText(QRectF(VX, VY + 16, VW, 24), Qt.AlignCenter, big)
        f3 = QFont("Menlo", 7)
        f3.setBold(True)
        p.setFont(f3)
        p.setPen(QColor(color[0], color[1], color[2], 220))
        p.drawText(QRectF(VX, VY + 42, VW, 13), Qt.AlignCenter, sub)
        p.setPen(Qt.NoPen)

    in_cd = state == "working" and st_t < A.cd
    if state == "asking":
        # amber = it needs YOU (vs green = GO given): instantly different
        _mission_text("HOLD", "T-00:00:10 — AWAITING YOUR GO",
                      (250, 196, 98), blink=sin(t * 4.0) > -0.2)
    elif in_cd:
        cd_secs = max(1, 10 - int(st_t / 0.35))  # ticks 10 → 1
        _mission_text(f"T-00:00:{cd_secs:02d}",
                      "GO GIVEN — INITIATING COUNTDOWN SEQUENCE",
                      (123, 216, 138))

    # CAUGHT stamp.
    if state == "done" and st_t > 3.0:
        f2 = QFont("Menlo", 13); f2.setBold(True)
        p.setFont(f2)
        p.setPen(QColor(10, 30, 16, 180))
        p.drawText(QRectF(VX + 1, VY + 25, VW, 20), Qt.AlignCenter, "CAUGHT ✓")
        p.setPen(QColor(150, 245, 170, 250))
        p.drawText(QRectF(VX, VY + 24, VW, 20), Qt.AlignCenter, "CAUGHT ✓")
        p.setPen(Qt.NoPen)

    # Phase status pill — bottom-right inside the viewport (every reference
    # frame has one). Forced/Tour text overrides the phase text.
    pill = caption
    if not pill:
        if state == "idle":
            pill = "PAD · READY"
        elif state == "working":
            if in_cd:
                pill = "AUTONOMOUS · EXECUTING"
            elif not A.sep_done:
                if 0.06 < A.alt < 0.22:
                    pill = "MAX-Q"
                elif st_t < A.cd + 12.0:
                    pill = "FLIGHT · NOMINAL"
                else:
                    pill = "NOMINAL ASCENT"
            else:
                so = t - A.sep_t0
                pill = ("STAGE SEP" if so < 3.5 else
                        "UPPER STAGE BURN" if so < 9.0 else
                        "COAST · NOMINAL")
        elif state == "asking":
            pill = "GO/NO-GO POLL"
        elif state == "done":
            pill = "BOOSTER RETURN" if st_t < 2.9 else "CATCH COMPLETE"
        elif state == "confused":
            pill = "ANOMALY · RUD"
        elif state == "resting":
            pill = "SCRUB CONFIRMED"
    if pill:
        f5 = QFont("Menlo", 7)
        f5.setBold(True)
        p.setFont(f5)
        wpx = len(pill) * 5 + 16
        rect = QRectF(VX + VW - wpx - 5, VY + VH - 20, wpx, 15)
        p.setBrush(QBrush(QColor(16, 17, 21, 210)))
        p.drawRoundedRect(rect, 7.5, 7.5)
        p.setPen(QColor(206, 209, 214, 240))
        p.drawText(rect, Qt.AlignCenter, pill)
        p.setPen(Qt.NoPen)

    # The Boss Cam PiP (drawn before the cut flicker so feed cuts wash over
    # it too). Hidden during working AND idle — the pad/flight is the star;
    # the cam cuts in when HE matters: your GO ("LET'S FLY."), the catch,
    # the RUD, and the OFFLINE card at night.
    if state not in ("working", "idle") or in_cd:
        _draw_boss_cam(p, "go" if in_cd else state, st_t, t, A.alt)

    # Camera-cut flicker — a livestream switches feeds, we lean into it.
    cdt = t - A.cut_t0
    if 0.0 <= cdt <= 0.16:
        fa = 1.0 - cdt / 0.16
        p.setBrush(QBrush(QColor(255, 255, 255, int(120 * fa))))
        p.drawRect(QRectF(VX, VY, VW, VH))
        ly = VY + (cdt / 0.16) * VH
        p.setBrush(QBrush(QColor(255, 255, 255, int(160 * fa))))
        p.drawRect(QRectF(VX, ly, VW, 2.2))

    # Subtle scanlines — it's a feed.
    p.setBrush(QBrush(QColor(0, 0, 0, 7)))
    yy = VY
    while yy < VY + VH:
        p.drawRect(QRectF(VX, yy, VW, 1))
        yy += 4

    p.restore()   # ---------------- end viewport ----------------

    # Telemetry strip: [state chip][ALT box][VEL box]……[T-clock].
    p.setBrush(QBrush(QColor(12, 14, 18)))
    p.drawRoundedRect(QRectF(VX, TELE_Y + 4, VW, TELE_H - 8), 7, 7)
    chip_c = pal["chip"]
    solid = None                                 # (bg, fg) for solid chips
    txt = pal["chip_txt"]
    if state == "working":
        if in_cd:
            txt = "COUNTDOWN · EXECUTING"
            solid = ((105, 185, 100), (8, 24, 10))
        elif A.sep_done:
            txt = "STAGE SEP" if (t - A.sep_t0) < 3.5 else "ON ORBIT"
        else:
            txt = "ASCENT"
    elif state == "idle":
        solid = ((38, 52, 66), (146, 186, 205))
    elif state == "resting":
        solid = ((42, 46, 58), (160, 166, 182))
    f4 = QFont("Menlo", 7)
    f4.setBold(True)
    p.setFont(f4)
    cw = len(txt) * 5 + 14
    if solid:
        p.setBrush(QBrush(QColor(*solid[0])))
        p.drawRoundedRect(QRectF(VX + 6, TELE_Y + 10, cw, 16), 8, 8)
        p.setPen(QColor(*solid[1], 250))
    else:
        p.setBrush(QBrush(C(chip_c, 36)))
        p.drawRoundedRect(QRectF(VX + 6, TELE_Y + 10, cw, 16), 8, 8)
        p.setPen(C(chip_c, 245))
    p.drawText(QRectF(VX + 6, TELE_Y + 10, cw, 16), Qt.AlignCenter, txt)
    p.setPen(Qt.NoPen)

    # ALTITUDE / VELOCITY readouts — flight only (hidden on the pad).
    show_boxes = ((state == "working" and st_t >= A.cd + 1.8)
                  or state == "done")
    if show_boxes:
        if state == "working":
            if A.sep_done:
                u_ = min(1.0, (t - A.sep_t0) / 30.0)
                altkm = 65 + 85 * u_ + 2.0 * sin(t * 0.045)
                vel = 5800 + 21000 * u_ + 35 * sin(t * 0.06)
            else:
                altkm = 66.0 * (A.alt ** 2.0)
                vel = 5800.0 * (A.alt ** 1.8)
        else:
            u_ = min(1.0, st_t / 2.4)
            altkm = max(0.0, 2.2 * (1.0 - u_ * u_))
            vel = max(0.0, 1100.0 * (1.0 - u_))
        bx0 = VX + 6 + cw + 3
        f6 = QFont("Menlo", 5)
        f6v = QFont("Menlo", 6)
        f6v.setBold(True)
        for wbox, label, value in ((42, "ALTITUDE:", f"{altkm:.0f} KM"),
                                   (64, "VELOCITY:", f"{vel:,.0f} KM/H")):
            p.setPen(QPen(QColor(88, 92, 104, 160), 1.0))
            p.setBrush(QBrush(QColor(8, 9, 13)))
            p.drawRoundedRect(QRectF(bx0, TELE_Y + 8, wbox, 22), 3, 3)
            p.setPen(QColor(138, 142, 152, 220))
            p.setFont(f6)
            p.drawText(QRectF(bx0 + 4, TELE_Y + 9, wbox - 6, 9),
                       Qt.AlignVCenter | Qt.AlignLeft, label)
            p.setPen(QColor(235, 239, 246, 245))
            p.setFont(f6v)
            p.drawText(QRectF(bx0 + 4, TELE_Y + 18, wbox - 6, 11),
                       Qt.AlignVCenter | Qt.AlignLeft, value)
            p.setPen(Qt.NoPen)
            bx0 += wbox + 3

    # T-clock: frozen while holding; counts down during the GO beat.
    f4b = QFont("Menlo", 8)
    f4b.setBold(True)
    p.setFont(f4b)
    if state == "asking":
        clock = "T-00:00:10"
    elif in_cd:
        clock = f"T-00:00:{max(1, 10 - int(st_t / 0.35)):02d}"
    else:
        mm, ss = divmod(int(st_t), 60)
        clock = f"T+{mm:02d}:{ss:02d}"
    p.setPen(QColor(225, 232, 242, 235))
    p.drawText(QRectF(VX, TELE_Y + 10, VW - 8, 16),
               Qt.AlignVCenter | Qt.AlignRight, clock)
    p.setPen(Qt.NoPen)


# --------------------------------------------------------------------- widget
class MaxQ(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)

        self._t0 = time.time()
        self._last_frame = self._t0
        self.A = Anim()
        self.sounds = ensure_sounds()

        self._live = detect_state()
        self._shown = self._live
        self._state_t0 = time.time()
        self._forced: Optional[str] = None
        self._tour = False
        self._tour_i = 0
        self._tour_next = 0.0
        self._pal_src = dict(PALETTES[self._shown])
        self._pal_dst = PALETTES[self._shown]
        self._fade_t0 = -9.0
        self._next_poll = 0.0
        self._flight_n = self._load_flights()

        self._press_pos = None
        self._press_global = None
        self._dragging = False

        self._restore_position()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(FRAME_MS)
        self._level_timer = QTimer(self)
        self._level_timer.timeout.connect(self._apply_macos_level)
        self._level_timer.start(500)
        self._apply_macos_level()

    # ----- flight counter (per-day, increments on each 'done')
    def _load_flights(self) -> int:
        today = datetime.date.today().isoformat()
        try:
            d = json.loads(FLIGHT_JSON.read_text())
            if d.get("day") == today:
                return int(d.get("n", 0))
        except Exception:
            pass
        return 0

    def _bump_flights(self):
        self._flight_n += 1
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            FLIGHT_JSON.write_text(json.dumps(
                {"day": datetime.date.today().isoformat(),
                 "n": self._flight_n}))
        except OSError:
            pass

    # ----- macOS over-fullscreen (the proven Wisp recipe)
    def _apply_macos_level(self):
        try:
            import objc
            from ctypes import c_void_p
            from AppKit import (
                NSApplication,
                NSWindowCollectionBehaviorCanJoinAllSpaces,
                NSWindowCollectionBehaviorFullScreenAuxiliary,
                NSWindowCollectionBehaviorIgnoresCycle,
            )
        except Exception:
            return
        try:
            NSApplication.sharedApplication().setActivationPolicy_(1)
            view = objc.objc_object(c_void_p=int(self.winId()))
            nswindow = view.window()
            if nswindow is None:
                return
            nswindow.setCollectionBehavior_(
                NSWindowCollectionBehaviorCanJoinAllSpaces
                | NSWindowCollectionBehaviorFullScreenAuxiliary
                | NSWindowCollectionBehaviorIgnoresCycle)
            nswindow.setLevel_(1000)
            nswindow.setHidesOnDeactivate_(False)
            nswindow.setCanHide_(False)
            nswindow.orderFrontRegardless()
        except Exception:
            pass

    # ----- position persistence
    def _restore_position(self):
        try:
            d = json.loads(WINDOW_JSON.read_text())
            self.move(int(d["x"]), int(d["y"]))
            return
        except Exception:
            pass
        scr = QApplication.primaryScreen()
        if scr:
            g = scr.availableGeometry()
            self.move(g.right() - W - 36, g.bottom() - H - 30)

    def _save_position(self):
        try:
            HOME.mkdir(parents=True, exist_ok=True)
            WINDOW_JSON.write_text(json.dumps({"x": self.x(), "y": self.y()}))
        except OSError:
            pass

    # ----- state plumbing
    def _display_state(self, now: float) -> str:
        if self._tour:
            if now >= self._tour_next:
                self._tour_i = (self._tour_i + 1) % len(STATES)
                self._tour_next = now + 7.0
            return STATES[self._tour_i]
        if self._forced:
            return self._forced
        if now >= self._next_poll:
            self._live = detect_state()
            self._next_poll = now + 0.5
        return self._live

    def _begin_cut(self, new_state: str, now: float):
        # GO answered → countdown beat before ignition.
        self.A.go_given = (new_state == "working"
                           and self._shown == "asking")
        self._last_cd_sec = 99
        self._pal_src = self._current_pal(now)
        self._pal_dst = PALETTES[new_state]
        self._fade_t0 = now
        self._state_t0 = now
        self.A.cut_t0 = now - self._t0      # feed-cut flicker
        self.A.smoke.clear()
        self.A.debris.clear()
        play(self.sounds, new_state)
        if new_state == "done":
            self._bump_flights()

    def _current_pal(self, now: float) -> dict:
        f = (now - self._fade_t0) / 1.1
        if f >= 1.0:
            return dict(self._pal_dst)
        f = max(0.0, min(1.0, f))
        f = f * f * (3 - 2 * f)
        return blend_pal(self._pal_src, self._pal_dst, f)

    # ----- interactions
    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self._press_pos = ev.position().toPoint()
            self._press_global = ev.globalPosition().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, ev):
        if self._press_pos is None:
            return
        delta = ev.globalPosition().toPoint() - self._press_global
        if not self._dragging and delta.manhattanLength() > 4:
            self._dragging = True
        if self._dragging:
            self.move(ev.globalPosition().toPoint() - self._press_pos)

    def mouseReleaseEvent(self, ev):
        if self._press_pos is None:
            return
        if self._dragging:
            self._save_position()
        else:
            self._tap()
        self._press_pos = None
        self._dragging = False

    def _tap(self):
        self.A.shake = 0.5
        play(self.sounds, "blip")
        rec = winning_session()
        if rec:
            threading.Thread(target=focus_session, args=(rec,),
                             daemon=True).start()

    def keyPressEvent(self, ev):
        k = ev.key()
        if k == Qt.Key_Escape:
            self.hide()
        elif Qt.Key_1 <= k <= Qt.Key_6:
            self._forced = STATES[k - Qt.Key_1]
            self._tour = False
        elif k == Qt.Key_0:
            self._forced = None
            self._tour = False
        elif k == Qt.Key_T:
            self._tour = not self._tour
            self._forced = None
            self._tour_next = 0.0

    # ----- paint
    def paintEvent(self, ev):
        now = time.time()
        t = now - self._t0
        state = self._display_state(now)
        if state != self._shown:
            self._begin_cut(state, now)
            self._shown = state
        pal = self._current_pal(now)
        st_t = now - self._state_t0
        dt = max(0.0, min(0.06, now - self._last_frame))
        self._last_frame = now
        update_anim(self.A, state, t, dt, st_t, pal)

        # The catch CLUNK, timed to the arms closing.
        if (state == "done" and st_t > 2.9
                and not self.A.catch_clunked):
            self.A.catch_clunked = True
            self.A.shake = 0.8

        # Countdown ticks (GO-given beat): one blip per displayed second.
        if state == "working" and st_t < self.A.cd:
            sec = max(1, 10 - int(st_t / 0.35))
            if sec != getattr(self, "_last_cd_sec", 99):
                self._last_cd_sec = sec
                play(self.sounds, "blip")

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        caption = ""
        if self._tour:
            caption = f"TOUR · {state.upper()}"
        elif self._forced:
            caption = f"FORCED · {state.upper()}"
        render_card(p, state, t, st_t, self.A, pal, self._flight_n, caption)
        p.end()


# ----------------------------------------------------------------------- tray
def _tray_icon() -> QIcon:
    pm = QPixmap(22, 22)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(210, 214, 222)))
    p.drawRoundedRect(QRectF(9, 5, 4.5, 12), 2, 2)
    p.drawPolygon(QPolygonF([QPointF(9, 6.5), QPointF(13.5, 6.5),
                             QPointF(11.25, 2.5)]))
    p.setBrush(QBrush(QColor(255, 150, 60)))
    p.drawPolygon(QPolygonF([QPointF(9.6, 17), QPointF(12.9, 17),
                             QPointF(11.25, 20.5)]))
    p.end()
    return QIcon(pm)


def main():
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    load_assets()                       # boss photos + vehicle sprites
    w = MaxQ()
    w.show()

    tray = QSystemTrayIcon(_tray_icon())
    menu = QMenu()
    act_show = QAction("Show / Hide")
    act_show.triggered.connect(lambda: w.setVisible(not w.isVisible()))
    act_tour = QAction("Tour mode (auto-cycle states)")
    act_tour.triggered.connect(lambda: (setattr(w, "_tour", not w._tour),
                                        setattr(w, "_forced", None),
                                        setattr(w, "_tour_next", 0.0)))
    act_live = QAction("Live mode")
    act_live.triggered.connect(lambda: (setattr(w, "_forced", None),
                                        setattr(w, "_tour", False)))
    act_quit = QAction("Quit MAX-Q")
    act_quit.triggered.connect(app.quit)
    force_menu = QMenu("Force state")
    force_actions = []
    def _mk_force(st):
        def f():
            w._forced = st
            w._tour = False
        return f
    for st in STATES:
        fa = QAction(st.capitalize())
        fa.triggered.connect(_mk_force(st))
        force_menu.addAction(fa)
        force_actions.append(fa)
    for a in (act_show, act_tour, act_live):
        menu.addAction(a)
    menu.addMenu(force_menu)
    menu.addSeparator()
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.setToolTip("MAX-Q — your AI agent as a launch stream")
    tray._refs = (act_show, act_tour, act_live, act_quit,
                  force_menu, force_actions)
    tray.show()

    app.exec()


if __name__ == "__main__":
    main()
