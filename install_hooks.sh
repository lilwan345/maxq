#!/bin/bash
# Wires MAX-Q into Claude Code's hooks (backs up settings.json first).
# The hooks write per-session state files that the card mirrors in real time.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.ai-desktop-toy/sessions"
chmod +x "$DIR/hooks/write_state.sh"

python3 - "$DIR" <<'PY'
import json
import os
import shutil
import sys
import time

repo = sys.argv[1]
script = os.path.join(repo, "hooks", "write_state.sh")
sp = os.path.expanduser("~/.claude/settings.json")
s = {}
if os.path.exists(sp):
    shutil.copy(sp, sp + ".bak." + time.strftime("%Y%m%d-%H%M%S"))
    with open(sp) as f:
        s = json.load(f)

hooks = s.setdefault("hooks", {})
EVENTS = {
    "UserPromptSubmit": "working",
    "PreToolUse": "working",
    "PostToolUse": "working",
    "Notification": "asking",
    "Stop": "idle",
}
for ev, arg in EVENTS.items():
    arr = hooks.setdefault(ev, [])
    if any("write_state.sh" in h.get("command", "")
           for m in arr for h in m.get("hooks", [])):
        continue                       # already wired (e.g. by the Wisp)
    arr.append({"matcher": "", "hooks": [{
        "type": "command",
        "command": f'"{script}" {arg}',
        "timeout": 2,
    }]})

with open(sp, "w") as f:
    json.dump(s, f, indent=2)
print("hooks installed →", sp)
print("(a timestamped backup of your settings.json sits next to it)")
PY

echo "Done. Restart your Claude Code sessions, then: ./run.sh"
