#!/bin/bash
# Lints every script this stack runs. Executed inside the throwaway image built
# by `make lint` (Dockerfile.lint); the repo is bind-mounted read-only at /work.
#
# Checks, ordered by how badly each has bitten us:
#
#   1. NON-ASCII in .ps1 -- Windows PowerShell 5.1 reads .ps1 as ANSI unless the
#      file carries a UTF-8 BOM. A multi-byte character in a STRING LITERAL gets
#      mojibaked, terminates the string early, and cascades into
#      "Unexpected token" / "The hash literal was incomplete" parse errors.
#      An em-dash in acquire_lock.ps1 did exactly that; start.bat read
#      PowerShell's parse-error exit as "lock held", so every boot bailed and
#      the whole stack deadlocked. This check is the reason this file exists.
#   2. .ps1 PARSE -- catches syntax errors without booting the VM.
#   3. PSScriptAnalyzer -- style/correctness findings on .ps1.
#   4. shellcheck + shfmt -- the .sh files.
#
# DELIBERATELY NO `set -e` and no ERR trap: a linter must run EVERY check and
# report the full picture. `set -e` would abort at the first finding, and an ERR
# trap would fire on every expected non-zero from a lint tool. Failures are
# accumulated in `failures` and the verdict is the exit code instead.
set -uo pipefail

readonly REPO=/work
readonly PSSA_VERSION=1.22.0

# Gate on warning and above. shellcheck's `info`/`style` tiers flag deliberate
# idioms -- e.g. SC2015 on the `[ x ] && pass ... || fail ...` shape used
# consistently throughout test.sh -- and blocking on those would make the gate
# something people route around instead of fix. Run `shellcheck` with no
# --severity locally when you want the full style report.
readonly SHELLCHECK_SEVERITY=warning

# Vendored third-party we do not own and will not restyle. defender-remover is
# an upstream tool vendored wholesale; its PSScriptAnalyzer findings (aliases
# like `dir`/`where`/`ni`) are the upstream author's business, not ours.
readonly VENDORED_GLOB='*/scripts/defender-remover/*'

failures=0

# Diagnostics go to stderr so stdout stays clean for anything that wants to
# pipe this. Plain-text rather than the project-standard JSON: the only consumer
# is a human running `make lint`, and the embedded tool output (shellcheck
# findings, PSScriptAnalyzer tables, shfmt diffs) is multi-line human-formatted
# text that JSON-encapsulating would make strictly harder to read.
log() {
    local level="$1"
    shift
    printf '[%s] %s\n' "$level" "$*" >&2
}

section() {
    printf '\n=== %s ===\n' "$*" >&2
}

# Only TRACKED files get linted. Gitignored local scratch (commit.sh,
# git-update.sh, fix.sh) would otherwise produce findings nobody can act on,
# and `git ls-files` keeps this list correct without a hand-maintained
# exclude list that drifts out of sync with .gitignore.
#
# Falls back to `find` when git is unavailable or the mount is not a work tree,
# so the linter still runs (louder than it should, but it runs).
tracked_files() {
    local pattern="$1"
    local path

    if git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        # --cached AND --others --exclude-standard: tracked files PLUS new
        # untracked ones, minus anything gitignored. Tracked-only would skip a
        # brand-new script that has not been `git add`ed yet -- precisely the
        # file most likely to carry a fresh mistake. The existence check drops
        # tracked deletions, which remain in the index until the release script
        # stages them but have no working-tree file for a linter to open.
        while IFS= read -r -d '' path; do
            [[ -f "$REPO/$path" ]] || continue
            [[ "$path" == scripts/defender-remover/* ]] && continue
            printf '%s/%s\n' "$REPO" "$path"
        done < <(
            git -C "$REPO" ls-files -z --cached --others --exclude-standard -- "$pattern"
        ) | sort
        return
    fi

    log WARN "not a git work tree; falling back to find (may include untracked scratch)"
    find "$REPO" -name "${pattern##*/}" -type f -not -path "$VENDORED_GLOB" | sort
}

find_shell_scripts() {
    tracked_files '*.sh'
}

find_ps_scripts() {
    tracked_files '*.ps1'
}

# grep -P (PCRE) because \xHH is DOCUMENTED there. In a POSIX bracket
# expression `[^\x00-\x7F]` is NOT an escape -- it is the literal set
# {\,x,0..7,F}, which matches nearly every line on some greps and nothing on
# others. Both wrong behaviours were observed while building this file.
readonly NON_ASCII_PCRE='[^\x00-\x7F]'

# Verifies the detector itself against known-good and known-bad fixtures.
#
# A checker that silently stops detecting is worse than no checker: this repo
# already shipped a healthcheck whose "port down" branch was unreachable, so an
# outage sat behind a green check for hours. The first ASCII pattern written for
# this file had exactly that failure mode (false negatives on real em-dashes).
# If this self-test fails, the whole lint run fails loudly.
selftest_non_ascii_detector() {
    section "self-test: non-ASCII detector"
    local tmp_ok tmp_bad rc=0
    tmp_ok=$(mktemp)
    tmp_bad=$(mktemp)
    # shellcheck disable=SC2064  # expand paths now, not at trap time
    trap "rm -f '$tmp_ok' '$tmp_bad'" RETURN

    printf 'pure ascii line\nanother one\n' >"$tmp_ok"
    # U+2014 EM DASH as raw UTF-8 bytes -- the exact character that broke
    # acquire_lock.ps1 in production.
    printf 'ascii\nem dash \xe2\x80\x94 here\n' >"$tmp_bad"

    if LC_ALL=C grep -qP "$NON_ASCII_PCRE" "$tmp_ok"; then
        log ERROR "detector false POSITIVE: flagged a pure-ASCII fixture"
        rc=1
    fi
    if ! LC_ALL=C grep -qP "$NON_ASCII_PCRE" "$tmp_bad"; then
        log ERROR "detector false NEGATIVE: missed a real em-dash fixture"
        rc=1
    fi

    if [[ $rc -eq 0 ]]; then
        log OK "detector correctly flags non-ASCII and passes ASCII"
        return 0
    fi
    log ERROR "the non-ASCII gate cannot be trusted -- fix the pattern"
    return 1
}

check_ps_ascii() {
    section "non-ASCII scan (.ps1 must be pure ASCII)"
    local bad=0 f hits

    while IFS= read -r f; do
        if hits=$(LC_ALL=C grep -nP "$NON_ASCII_PCRE" "$f"); then
            bad=1
            log ERROR "non-ASCII in ${f#"$REPO"/} -- mojibakes under Windows PowerShell 5.1:"
            printf '%s\n' "$hits" >&2
        fi
    done < <(find_ps_scripts)

    if [[ $bad -eq 0 ]]; then
        log OK "all .ps1 files are pure ASCII"
        return 0
    fi
    return 1
}

check_ps_parse() {
    section "PowerShell parse check"
    local bad=0 f

    while IFS= read -r f; do
        if ! pwsh -NoProfile -File /dev/stdin "$f" <<'PWSH'; then
param([string]$Path)
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Output $_.ToString() }
    exit 1
}
exit 0
PWSH
            bad=1
            log ERROR "parse errors in ${f#"$REPO"/}"
        fi
    done < <(find_ps_scripts)

    if [[ $bad -eq 0 ]]; then
        log OK "all .ps1 files parse"
        return 0
    fi
    return 1
}

check_psscriptanalyzer() {
    section "PSScriptAnalyzer ${PSSA_VERSION}"
    local bad=0 f

    # Per-file rather than -Recurse on the dir, so the vendored
    # defender-remover tree stays excluded by tracked_files().
    while IFS= read -r f; do
        if ! pwsh -NoProfile -File /dev/stdin "$f" <<'PWSH'; then
param([string]$Path)
$results = Invoke-ScriptAnalyzer -Path $Path -Severity Error,Warning
if ($results) {
    $results | Format-Table -AutoSize | Out-String -Width 200 | Write-Output
    exit 1
}
exit 0
PWSH
            bad=1
            log ERROR "PSScriptAnalyzer findings in ${f#"$REPO"/}"
        fi
    done < <(find_ps_scripts)

    if [[ $bad -eq 0 ]]; then
        log OK "PSScriptAnalyzer clean"
        return 0
    fi
    return 1
}

check_shellcheck() {
    section "shellcheck (severity>=${SHELLCHECK_SEVERITY})"
    local bad=0 f

    while IFS= read -r f; do
        if ! shellcheck --severity="$SHELLCHECK_SEVERITY" "$f"; then
            bad=1
            log ERROR "shellcheck findings in ${f#"$REPO"/}"
        fi
    done < <(find_shell_scripts)

    if [[ $bad -eq 0 ]]; then
        log OK "shellcheck clean"
        return 0
    fi
    return 1
}

check_shfmt() {
    section "shfmt"
    local bad=0 f

    # -d prints a diff and exits non-zero when a file is not formatted. Every
    # file is checked rather than stopping at the first, so one run shows all.
    while IFS= read -r f; do
        if ! shfmt -d -i 4 "$f"; then
            bad=1
        fi
    done < <(find_shell_scripts)

    if [[ $bad -eq 0 ]]; then
        log OK "shfmt clean"
        return 0
    fi
    log ERROR "shfmt found unformatted files (diff above)"
    return 1
}

apply_shfmt() {
    section "shfmt -w (rewriting in place)"
    local f count=0

    while IFS= read -r f; do
        if shfmt -w -i 4 "$f"; then
            count=$((count + 1))
        else
            log ERROR "shfmt failed on ${f#"$REPO"/}"
            return 1
        fi
    done < <(find_shell_scripts)

    log OK "formatted $count shell script(s)"
}

usage() {
    cat <<'EOF'
Usage: lint.sh [--format]

  (no args)   Run every check and exit non-zero on any failure.
  --format    Rewrite .sh files in place with shfmt instead of checking.
              Requires a WRITABLE mount (make lint mounts :ro, make format does not).
EOF
}

main() {
    [[ -d $REPO ]] || {
        log ERROR "$REPO not mounted"
        exit 1
    }

    # --format shares find_shell_scripts() with the checks on purpose. When the
    # Makefile had its own `git ls-files` selection, `make format` skipped
    # untracked files that `make lint` still flagged, so the two could never
    # agree and lint stayed red after a successful format.
    case "${1:-}" in
    --format)
        apply_shfmt
        return $?
        ;;
    -h | --help)
        usage
        return 0
        ;;
    "") ;;
    *)
        log ERROR "unknown argument: $1"
        usage >&2
        return 1
        ;;
    esac

    selftest_non_ascii_detector || failures=$((failures + 1))
    check_ps_ascii || failures=$((failures + 1))
    check_ps_parse || failures=$((failures + 1))
    check_psscriptanalyzer || failures=$((failures + 1))
    check_shellcheck || failures=$((failures + 1))
    check_shfmt || failures=$((failures + 1))

    section "verdict"
    if [[ $failures -ne 0 ]]; then
        log ERROR "$failures lint category/categories failed"
        exit 1
    fi

    log OK "all lint categories passed"
}

main "$@"
