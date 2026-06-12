#!/usr/bin/env bash
# Called by Claude Code hooks. Writes <state> + session_id to a per-session
# JSON file so detect.py can aggregate across concurrent Claude Code instances.
#
# Usage: write_state.sh <state>   (hook JSON arrives on stdin)
#
# Deliberately robust: hooks may run in a minimal environment (no HOME, sparse
# PATH), so we avoid `set -u`, fall back for HOME, and parse the session_id
# with sed instead of depending on python3.

set -e

state="${1:-idle}"
ts=$(date +%s)

# HOME can be unset when Claude Code (esp. the terminal CLI) spawns the hook.
home="${HOME:-}"
if [ -z "$home" ]; then
    home="/Users/$(whoami 2>/dev/null || echo "${USER:-}")"
fi

sessions_dir="$home/.ai-desktop-toy/sessions"
mkdir -p "$sessions_dir"

# Extract session_id and transcript_path from the hook's stdin JSON.
# The transcript file is Claude Code's running conversation log — its mtime
# is our heartbeat: while Claude is actually generating output, this file
# keeps getting written. detect.py uses it to know "still working" vs
# "finished/interrupted" without needing a Stop hook.
input="$(cat)"
session_id="$(printf '%s' "$input" \
    | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
[ -z "$session_id" ] && session_id="pid-$PPID"
transcript_path="$(printf '%s' "$input" \
    | sed -n 's/.*"transcript_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"

# --- Terminal identity, so the toy can focus THIS session's window on click ---
# All best-effort: captured from the env the hook inherits from the terminal CC
# runs in, plus cwd from the payload. Empty when a given terminal doesn't expose
# them; detect.py / focus.py degrade gracefully (click just pokes).
#   cwd       → workspace folder, used to raise the right VSCode/Cursor window
#   term      → $TERM_PROGRAM (vscode / iTerm.app / Apple_Terminal / ...)
#   app_id    → $__CFBundleIdentifier, the exact editor app (VSCode vs Cursor)
#   iterm_id  → iTerm2 session UUID (persistent; the wXtYpZ: prefix is stripped)
#   tty       → controlling TTY, used to pick the right Terminal.app tab
cwd="$(printf '%s' "$input" \
    | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
term_program="${TERM_PROGRAM:-}"
app_id="${__CFBundleIdentifier:-}"
iterm_id=""
if [ -n "${ITERM_SESSION_ID:-}" ]; then
    iterm_id="${ITERM_SESSION_ID##*:}"   # strip wXtYpZ: → persistent UUID
fi
tty_dev="$(ps -o tty= -p $$ 2>/dev/null | tr -d ' ')"
case "$tty_dev" in
    ""|"?"|"??") tty_dev="" ;;
    /dev/*) ;;
    *) tty_dev="/dev/$tty_dev" ;;
esac

# Ancestor PID chain (hook → claude → terminal-shell → editor-helper → ...).
# The Wisp VSCode extension matches one of these against a terminal's
# processId to focus the EXACT integrated terminal this session runs in — the
# only reliable way to pick a specific terminal pane inside one VSCode window.
ppids=""
_p="$PPID"
for _i in 1 2 3 4 5 6; do
    case "$_p" in ""|0|1) break ;; esac
    ppids="${ppids:+$ppids,}$_p"
    _p="$(ps -o ppid= -p "$_p" 2>/dev/null | tr -d ' ')"
done

# --- Real 'done' detection ---------------------------------------------
# UserPromptSubmit stamps work_started into the session JSON; every other
# hook carries it forward; Stop compares elapsed wall-clock. A run that
# lasted >= WISP_DONE_MIN_SEC (default 180s) ends as 'done' — the toys
# celebrate ~6s (EVENT_STALE_SEC) and decay back to idle on their own.
# 'confused' stays manual: a survey of 9k PostToolUse payloads in hook.log
# (2026-06-12) found no reliable tool-error marker, and routine agent
# failures would fire it constantly.
evt="$(printf '%s' "$input" \
    | sed -n 's/.*"hook_event_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
session_file="$sessions_dir/${session_id}.json"
prev_ws=""
if [ -f "$session_file" ]; then
    prev_ws="$(sed -n 's/.*"work_started"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
        "$session_file" 2>/dev/null)"
fi
[ "$prev_ws" = "0" ] && prev_ws=""
work_started="$prev_ws"
case "$evt" in
    UserPromptSubmit)
        work_started="$ts"                  # a new task begins
        ;;
    Stop)
        done_min="${WISP_DONE_MIN_SEC:-180}"
        if [ -n "$prev_ws" ] && [ $((ts - prev_ws)) -ge "$done_min" ]; then
            state="done"
        fi
        work_started=""                     # task over — don't re-fire
        ;;
    *)
        # First sighting mid-task (e.g. right after this script shipped).
        if [ -z "$work_started" ] && [ "$state" = "working" ]; then
            work_started="$ts"
        fi
        ;;
esac

# Notification fires with two distinct messages:
#   "Claude needs your permission"      → true asking (user must act NOW)
#   "Claude is waiting for your input"  → passive idle ping (CC's 60s terminal
#                                          bell after Stop, NOT a question)
# Only the former should advance the toy to 'asking'. The latter would
# falsely lock the toy in asking for ASKING_STALE_SEC (5 min) — the bug seen
# in Terminal.app sessions where the user pauses longer than 60s.
if [ "$state" = "asking" ]; then
    msg="$(printf '%s' "$input" \
        | sed -n 's/.*"message"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
    case "$msg" in
        *permission*) ;;
        *)
            { printf '%s  state=SKIP-ask    session=%s msg="%s"\n' \
                "$(date '+%H:%M:%S')" "${session_id:0:8}" "$msg"
            } >> "$home/.ai-desktop-toy/hook.log" 2>/dev/null || true
            exit 0
            ;;
    esac
fi

printf '{"state":"%s","ts":%d,"work_started":%s,"session":"%s","transcript":"%s","cwd":"%s","term":"%s","app_id":"%s","iterm_id":"%s","tty":"%s","ppids":"%s"}' \
    "$state" "$ts" "${work_started:-0}" "$session_id" "$transcript_path" \
    "$cwd" "$term_program" "$app_id" "$iterm_id" "$tty_dev" "$ppids" \
    > "$sessions_dir/${session_id}.json"

# --- Debug logging (temporary). Records every hook fire with stdin payload
# so we can tell when Notification fires unexpectedly. Remove once the
# state machine is well understood.
log="$home/.ai-desktop-toy/hook.log"
{
    printf '%s  state=%-9s  session=%s\n' \
        "$(date '+%H:%M:%S')" "$state" "${session_id:0:8}"
    printf '  stdin: %s\n' "$input"
} >> "$log" 2>/dev/null || true
