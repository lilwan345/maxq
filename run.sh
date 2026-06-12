#!/bin/zsh
# MAX-Q launcher — single instance.
if pgrep -f "[m]axq.py" > /dev/null; then
  echo "MAX-Q is already running."
  exit 0
fi
PY="${PYTHON:-python3}"
[ -x /opt/anaconda3/bin/python3 ] && PY=/opt/anaconda3/bin/python3
exec "$PY" "$(dirname "$0")/maxq.py"
