#!/bin/sh
# Daily rotation for logs in every directory named by $LOG_DIRS (a
# colon-separated list; $LOG_DIR is accepted as the single-directory form the
# stock docker-compose.yml.example still passes). Each VM can mount its own
# host directory over /shared/logs, so there is more than one to walk and
# rotating only the first silently leaves the rest to grow forever.
#
# Idempotent: keyed on whether yesterday's archive already exists, so re-runs
# are no-ops. Runs as a loop inside an alpine sidecar; no cron daemon needed.
#
# Truncate-in-place (archive + : >) instead of mv: full.log is held open by
# the Python API's FileHandler, so renaming the inode would leave the
# writer pointed at the renamed file forever. Truncating preserves the
# inode — Python keeps writing, the file just appears empty on next
# append. cmd.exe `>>` and PowerShell `Add-Content` reopen per write so
# either approach works for them.
#
# Archives are GZIPPED, which is not merely about size. Truncating the host
# file to 0 does not reset the Windows guest's cached write offset, so the
# guest resumes writing where it left off and the file comes back as a hole
# with a few KB of real text at the end: full.log measured 1.7 GB apparent
# against 6.6 MB allocated. Busybox `cp` does not preserve holes, so it
# faithfully wrote every one of those zeros out -- 2.18 GB of archives from
# 13 MB of actual log text in a single pass on 2026-08-21, and growing daily
# because the offset never resets. gzip turns that run of zeros back into
# nothing, needs no coreutils in the alpine image, and compresses the real
# text 10-20x as well.

set -eu

LOG_DIRS="${LOG_DIRS:-${LOG_DIR:-/logs}}"
TERMINALS_DIR="${TERMINALS_DIR:-/terminals}"
RETAIN_DAYS="${RETAIN_DAYS:-7}"
INTERVAL="${INTERVAL:-3600}"
# Terminal journals need a size bound as well as an age one — see
# cap_journal_size. IDLE_MINUTES is the "a backtest is still writing this"
# guard; nothing touched more recently is truncated.
MAX_LOG_BYTES="${MAX_LOG_BYTES:-2147483648}"
IDLE_MINUTES="${IDLE_MINUTES:-30}"

log() {
    printf '[%s] [rotator] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# The date an archive is named for, with any .gz stripped first, so the same
# two helpers read both the current and the pre-gzip naming.
_archive_date() {
    stem=${1%.gz}
    echo "${stem##*.}"
}

# True only for a name ending in a fixed-width YYYYMMDD (optionally .gz), so a
# live *.log and anything else sharing the directory are never touched.
_is_archive() {
    case "$(_archive_date "$1")" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;;
    *) return 1 ;;
    esac
}

is_positive_integer() {
    case "$1" in
    '' | *[!0-9]*) return 1 ;;
    esac

    [ "$1" -gt 0 ]
}

# Age alone cannot bound these. A high-frequency strategy logs every order
# placement, modification and cancellation, so a single backtest can write tens
# of gigabytes into TODAY's journal — which the retention window deliberately
# will not touch for RETAIN_DAYS, long after the disk has filled.
#
# This reclaims that space once the run goes quiet; it does NOT bound a journal
# while its own backtest is still writing (see IDLE_MINUTES below), because
# truncating a running job's log destroys the diagnostics for the very run
# producing them.
#
# Truncate in place rather than delete: the terminal holds the journal open, so
# unlinking the inode would leave the writer pointed at a deleted file and the
# space would not come back until the terminal exited.
cap_journal_size() {
    journal=$1

    # stat, not `wc -c`: busybox wc READS the whole file to count bytes, which
    # costs ~18s on a 3 GB journal (measured in alpine:3.20) and would run for
    # every in-window journal every INTERVAL — on exactly the multi-gigabyte
    # files this function exists for. stat is one fstat on busybox and GNU
    # alike. The test image has GNU coreutils, where `wc -c` is already O(1),
    # so this cost is invisible to the suite and only appears in production.
    size=$(stat -c %s "$journal" 2>/dev/null || echo 0)
    [ "$size" -gt "$MAX_LOG_BYTES" ] || return 0

    # Anything written inside the idle window belongs to a running backtest;
    # truncating it would destroy the diagnostics for the very run producing
    # them. Let it exceed the cap until it goes quiet.
    if [ -n "$(find "$journal" -mmin "-${IDLE_MINUTES}" 2>/dev/null)" ]; then
        log "over cap but still active, left alone (${size}B) $journal"
        return 0
    fi

    # Restore the mtime afterwards. _tail_dir_log picks the newest .log in a
    # directory by mtime, so bumping this one to now would make a just-emptied
    # journal outrank the journal a running job is actually writing, and
    # GET /backtest/<id>/tail would answer with nothing until that job's next
    # write. Truncating is this script's housekeeping, not the terminal
    # logging, so it should not look like the most recent activity.
    mtime=$(stat -c %Y "$journal" 2>/dev/null || echo "")
    if : >"$journal"; then
        if [ -n "$mtime" ]; then
            touch -d "@$mtime" "$journal" 2>/dev/null || true
        fi
        log "truncated oversized terminal journal (${size}B) $journal"
    fi
}

prune_journal_dir() {
    journal_dir=$1
    cutoff=$2

    [ -d "$journal_dir" ] || return 0

    for journal in "$journal_dir"/????????.log; do
        [ -f "$journal" ] || continue
        journal_date="${journal##*/}"
        journal_date="${journal_date%.log}"
        case "$journal_date" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
        *) continue ;;
        esac

        if [ "$journal_date" -lt "$cutoff" ]; then
            rm -f "$journal"
            log "pruned terminal journal $journal"
            continue
        fi

        # Still inside the retention window — bound it by size instead.
        cap_journal_size "$journal"
    done
}

prune_terminal_journals() {
    cutoff=$1

    [ -d "$TERMINALS_DIR" ] || return 0

    find "$TERMINALS_DIR" -type f -name terminal64.exe -print |
        while IFS= read -r terminal_binary; do
            terminal_dir="${terminal_binary%/terminal64.exe}"
            prune_journal_dir "$terminal_dir/logs" "$cutoff"
            prune_journal_dir "$terminal_dir/Tester/logs" "$cutoff"

            for agent_journal_dir in "$terminal_dir"/Tester/Agent-*/logs; do
                prune_journal_dir "$agent_journal_dir" "$cutoff"
            done
        done
}

rotate_dir() {
    dir=$1
    yesterday=$2
    cutoff=$3

    # A directory named but not mounted is a compose/vms.yaml mismatch, not a
    # reason to abandon the other directories -- say so and carry on.
    if [ ! -d "$dir" ]; then
        log "skipping $dir: not mounted"
        return 0
    fi

    # Compress archives left behind by the pre-gzip version. Bounded and
    # self-limiting: a compressed archive no longer matches this glob. gzip
    # only unlinks the source once it has written the .gz, so an interrupted
    # pass leaves the original intact rather than a truncated archive.
    for plain in "$dir"/*.log.[0-9]*; do
        [ -f "$plain" ] || continue
        case "$plain" in *.gz) continue ;; esac
        _is_archive "$plain" || continue
        # gzip refuses rather than overwrites when the target exists; skip so
        # one such pair cannot stall every later archive in this directory.
        [ -e "${plain}.gz" ] && continue
        if gzip "$plain"; then
            log "compressed $(basename "$plain")"
        else
            log "compression FAILED for $(basename "$plain")"
        fi
    done

    for f in "$dir"/*.log; do
        [ -f "$f" ] || continue
        archive="${f}.${yesterday}.gz"
        [ -e "$archive" ] && continue
        # An uncompressed archive for the same day means an older version
        # already rotated it; do not rotate the day twice on the changeover.
        [ -e "${f}.${yesterday}" ] && continue
        [ -s "$f" ] || continue
        # Atomic: write to .tmp then mv. If gzip fails (disk full etc.) the
        # partial sits as .tmp and gets retried/overwritten next cycle —
        # never leaves a half-written archive blocking rotation.
        if gzip -c "$f" >"${archive}.tmp" && mv "${archive}.tmp" "$archive"; then
            : >"$f"
            log "rotated $(basename "$f") -> $(basename "$archive")"
        else
            rm -f "${archive}.tmp"
            log "rotation FAILED for $(basename "$f")"
        fi
    done

    # Prune *.log.YYYYMMDD[.gz] older than cutoff. Lex sort == chrono sort
    # because the date is fixed-width YYYYMMDD.
    for old in "$dir"/*.log.[0-9]*; do
        [ -f "$old" ] || continue
        _is_archive "$old" || continue
        if [ "$(_archive_date "$old")" -lt "$cutoff" ]; then
            rm -f "$old"
            log "pruned $(basename "$old")"
        fi
    done
}

rotate_once() {
    now=$(date -u +%s)
    yesterday=$(date -u -d "@$((now - 86400))" +%Y%m%d)
    cutoff=$(date -u -d "@$((now - RETAIN_DAYS * 86400))" +%Y%m%d)

    # Split on ':' into the positional params rather than leaving IFS changed
    # for the rest of the run. The list is generated into the compose file from
    # vms.yaml, so it holds container paths only -- no spaces, no globs.
    old_ifs=$IFS
    IFS=:
    # shellcheck disable=SC2086  # deliberate split on the ':' separator
    set -- $LOG_DIRS
    IFS=$old_ifs

    for dir in "$@"; do
        rotate_dir "$dir" "$yesterday" "$cutoff"
    done

    prune_terminal_journals "$cutoff"
}

if ! is_positive_integer "$RETAIN_DAYS"; then
    log "RETAIN_DAYS must be a positive integer, got: $RETAIN_DAYS"
    exit 1
fi

if ! is_positive_integer "$MAX_LOG_BYTES"; then
    log "MAX_LOG_BYTES must be a positive integer, got: $MAX_LOG_BYTES"
    exit 1
fi

if ! is_positive_integer "$IDLE_MINUTES"; then
    log "IDLE_MINUTES must be a positive integer, got: $IDLE_MINUTES"
    exit 1
fi

log "starting (log_dirs=$LOG_DIRS terminals_dir=$TERMINALS_DIR retain_days=$RETAIN_DAYS max_log=${MAX_LOG_BYTES}B idle_min=$IDLE_MINUTES interval=${INTERVAL}s)"
while true; do
    if ! rotate_once; then
        log "rotate_once failed (continuing)"
    fi
    sleep "$INTERVAL"
done
