#!/bin/sh
# Daily rotation for logs in $LOG_DIR. Idempotent: keyed on whether
# yesterday's archive already exists, so re-runs are no-ops. Runs as a
# loop inside an alpine sidecar; no cron daemon needed.
#
# Truncate-in-place (cp + : >) instead of mv: full.log is held open by
# the Python API's FileHandler, so renaming the inode would leave the
# writer pointed at the renamed file forever. Truncating preserves the
# inode — Python keeps writing, the file just appears empty on next
# append. cmd.exe `>>` and PowerShell `Add-Content` reopen per write so
# either approach works for them.

set -eu

LOG_DIR="${LOG_DIR:-/logs}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
INTERVAL="${INTERVAL:-3600}"

log() {
    printf '[%s] [rotator] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

rotate_once() {
    now=$(date -u +%s)
    yesterday=$(date -u -d "@$((now - 86400))" +%Y%m%d)
    cutoff=$(date -u -d "@$((now - RETAIN_DAYS * 86400))" +%Y%m%d)

    for f in "$LOG_DIR"/*.log; do
        [ -f "$f" ] || continue
        archive="${f}.${yesterday}"
        [ -e "$archive" ] && continue
        [ -s "$f" ] || continue
        # Atomic: cp to .tmp then mv. If cp fails (disk full etc.) the
        # partial sits as .tmp and gets retried/overwritten next cycle —
        # never leaves a half-written archive blocking rotation.
        if cp "$f" "${archive}.tmp" && mv "${archive}.tmp" "$archive"; then
            : > "$f"
            log "rotated $(basename "$f") -> $(basename "$archive")"
        else
            rm -f "${archive}.tmp"
            log "rotation FAILED for $(basename "$f")"
        fi
    done

    # Prune *.log.YYYYMMDD older than cutoff. Lex sort == chrono sort
    # because the suffix is fixed-width YYYYMMDD.
    for old in "$LOG_DIR"/*.log.[0-9]*; do
        [ -f "$old" ] || continue
        suffix="${old##*.}"
        case "$suffix" in
            [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
            *) continue ;;
        esac
        if [ "$suffix" -lt "$cutoff" ]; then
            rm -f "$old"
            log "pruned $(basename "$old")"
        fi
    done
}

log "starting (log_dir=$LOG_DIR retain_days=$RETAIN_DAYS interval=${INTERVAL}s)"
while true; do
    if ! rotate_once; then
        log "rotate_once failed (continuing)"
    fi
    sleep "$INTERVAL"
done
