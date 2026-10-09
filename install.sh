#!/bin/bash
# Ставит приём правок со страницы журнала на этот Мак.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/local.work-journal.plist"
PYTHON="/opt/homebrew/bin/python3"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3)"
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.cursor/work-log"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.work-journal</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/scripts/journal_server.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}/scripts</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${HOME}/.cursor/work-log/server.log</string>
  <key>StandardErrorPath</key>
  <string>${HOME}/.cursor/work-log/server.log</string>
</dict>
</plist>
EOF

UID_NUM="$(id -u)"
LABEL="gui/${UID_NUM}/local.work-journal"
if launchctl print "$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$LABEL" || true
fi
launchctl bootstrap "gui/${UID_NUM}" "$PLIST"

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ]; then
  "$LSREGISTER" -f "$ROOT/bin/WorkJournal.app" >/dev/null 2>&1 || true
fi

echo "журнал слушает 127.0.0.1:47655"
