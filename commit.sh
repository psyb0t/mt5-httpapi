#!/bin/bash
set -eo pipefail

TAG="v1.2.0"
MSG="$(cat <<'EOF'
multi-terminal fixes, boot stability, login verification

- fix reboot loop: separate oem-install.bat stub so only one install.bat path runs
- fix concurrent instances: atomic mkdir lock instead of file-based lock (race condition)
- fix MT5 auto-updater reboots: kill liveupdate.exe/mtupdate.exe on startup
- add start-mt5.log: separate log for boot sequence
- ensure_initialized: verify account_info() login after terminal connects, call mt5.login() if not logged in
- wrap account_info() in 15s timeout to prevent hangs
- remove legacy single-terminal fallback (terminals.json required)
- remove default MetaQuotes installer fallback (big warning if no installers found)
- disable UAC on VM (headless, no need for it)
EOF
)"

git add -A
git commit -m "$MSG"
git tag "$TAG"
echo "Tagged $TAG"
