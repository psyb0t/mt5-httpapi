"""Compile MQL5 source to .ex5 via MetaEditor.

Why this exists: building an EA otherwise requires a human on a Windows machine
with MetaEditor installed. This lets an automated caller compile source it has
generated or assembled, on the host that already has the toolchain.

Threat model, because this endpoint takes arbitrary text from a caller and hands
it to a compiler:

  * SOURCE TEXT ONLY. There is no caller-supplied path anywhere. `filename` is
    reduced to a bare stem and re-suffixed, so "../../terminal64" or
    "C:\\Windows\\x" cannot escape the temp directory.
  * The caller controls neither /log: nor /inc:. Both are computed here.
  * The handler reads nothing from disk on the caller's behalf. The only files
    it touches are in a per-request temp directory, removed on every exit path.
  * MetaEditor itself does read files the source names: #include, #resource
    and #property icon. Those paths are checked before it runs, see
    _check_source_paths().
  * The handler cannot trade, cannot restart a terminal, and never touches the
    MT5 SDK. It shells out to MetaEditor64.exe and reads back two files.

MetaEditor specifics worth knowing before editing this:

  * It exits NON-ZERO on warnings as well as errors, so the exit code cannot
    decide success. The log is the authority, and the produced .ex5 is the
    tiebreaker.
  * It writes its log as UTF-16LE with a BOM. Decoding it as UTF-8 yields
    mojibake and every regex below silently stops matching.
  * It emits the .ex5 beside the source file, not into a configurable output
    path — which is exactly why compiling inside the temp directory is enough
    to keep concurrent requests from colliding over output names.

MQL4 rides the same route. A `filename` ending in `.mq4` selects the MT4
MetaEditor (COMPILE_MT4_TERMINAL_DIR) and answers with `ex4_base64`; anything
else is MQL5 exactly as before. MT4 is a compiler only here — there is no MT4
terminal, account or tester behind it.
"""

import base64
import errno
import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import psutil
from flask import jsonify, request

from mt5api.config import (
    COMPILE_INCLUDE_DIGESTS,
    COMPILE_INCLUDE_DIR,
    COMPILE_LOCAL_CACHE,
    COMPILE_MAX_EX5_BYTES,
    COMPILE_MAX_SOURCE_BYTES,
    COMPILE_METAEDITOR,
    COMPILE_MT4_TERMINAL_DIR,
    COMPILE_TIMEOUT_SECONDS,
    COMPILE_WORK_DIR,
)
from mt5api.logger import log

#: The client treats a non-JSON body as a broken host, and `log` as always a
#: string. Both are enforced at every return in this module.
MAX_LOG_BYTES = 8192

#: MetaEditor is single-instance per installation directory and compiles are
#: short. One lock, no job queue — a queue would add failure modes (lost jobs,
#: status polling, restart recovery) to buy nothing at this duration.
_COMPILE_LOCK = threading.Lock()

#: How long a caller may wait for the lock on top of its own compile budget.
#: Without a bound, a stuck compile turns every later request into a hung
#: connection, which is the one failure the client cannot distinguish from a
#: dead host.
_LOCK_WAIT_MARGIN_SECONDS = 30

#: How many compile requests may wait behind the one that is running.
#:
#: waitress serves every route from one fixed pool of threads, and a compile
#: waiting on the lock holds one of them for up to the budget above. Unbounded,
#: a caller with nothing but the compile-only token could park the whole pool
#: behind the lock and stall orders, positions and /ping — the routes that
#: token is supposed to be unable to affect. Past this many waiters a request
#: is answered 429 at once instead of queueing.
MAX_WAITING_COMPILES = 2
_COMPILE_SLOTS = threading.BoundedSemaphore(1 + MAX_WAITING_COMPILES)

#: Seconds a 429 asks the caller to wait before retrying. About one warm
#: compile.
_BUSY_RETRY_AFTER_SECONDS = 5

#: _COMPILE_LOCK above only serializes calls inside ONE process. Every mt5api
#: process on a VM exposes /compile and, by default, every one of them
#: resolves COMPILE_METAEDITOR (or the local mirror) to the SAME installation
#: directory — so in production the real contention for the "single-instance
#: per installation directory" MetaEditor is across N processes, not within
#: one. Verified live: two separate OS processes each holding their own
#: (empty) _COMPILE_LOCK ran MetaEditor fully concurrently against a shared
#: install with no serialization at all.
#:
#: The lock is an operating-system byte-range lock on this file (msvcrt on
#: Windows, flock elsewhere), not a file whose existence means "held". The OS
#: drops it the moment its holder's handle closes, including when the process
#: is killed, so there is no stale lock to detect, no heartbeat to keep it
#: fresh and no reaper that could remove a live holder's lock. The file itself
#: is never deleted.
_CROSS_PROCESS_LOCK_BASENAME = ".compile-inflight.lock"

#: Polled rather than blocking, so a waiter still honours its deadline. Cheap
#: relative to a compile.
_CROSS_PROCESS_LOCK_POLL_SECONDS = 0.2

if os.name == "nt":
    import msvcrt

    def _os_try_lock(fd):
        # msvcrt locks from the current position; pin it to byte 0 so every
        # process contends for the same byte.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _os_unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _os_try_lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _os_unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


#: errno values that mean "someone else holds it". msvcrt reports contention
#: as EACCES (or EDEADLOCK), flock as EWOULDBLOCK/EAGAIN.
_CONTENTION_ERRNOS = {
    getattr(errno, name)
    for name in ("EACCES", "EAGAIN", "EWOULDBLOCK", "EDEADLK", "EDEADLOCK")
    if hasattr(errno, name)
}


def _cross_process_lock_path():
    """The lock file for the toolchain every process on this VM shares.

    The local mirror's directory when one is configured, because that is the
    tree the lock has to cover: the mirror is refreshed (copied into and
    pruned) under this lock as well as compiled from. Otherwise the configured
    installation directory. Chosen from configuration, not from whichever copy
    _local_toolchain() picked, because the lock has to be held before the
    mirror is touched.
    """
    if COMPILE_LOCAL_CACHE:
        try:
            os.makedirs(COMPILE_LOCAL_CACHE, exist_ok=True)
            return os.path.join(COMPILE_LOCAL_CACHE, _CROSS_PROCESS_LOCK_BASENAME)
        except OSError as exc:
            log.warning("compile: local cache %s unusable for the lock (%s)", COMPILE_LOCAL_CACHE, exc)
    return os.path.join(os.path.dirname(COMPILE_METAEDITOR), _CROSS_PROCESS_LOCK_BASENAME)


class _CrossProcessLock:
    """A held cross-process compile lock: an open handle with byte 0 locked."""

    def __init__(self, path, fd):
        self.path = path
        self.fd = fd

    def release(self):
        try:
            _os_unlock(self.fd)
        except OSError as exc:
            # Closing the handle below releases it regardless.
            log.warning("compile: unlocking %s failed (%s)", self.path, exc)
        finally:
            os.close(self.fd)


def _acquire_cross_process_lock(deadline):
    """Block (polling) until this process is the only one using the shared
    toolchain, or `deadline` (a time.monotonic() value) passes.

    Returns a _CrossProcessLock to pass to _release_cross_process_lock, or
    None on timeout.
    """
    path = _cross_process_lock_path()
    while True:
        fd = None
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            _os_try_lock(fd)
            return _CrossProcessLock(path, fd)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            if exc.errno not in _CONTENTION_ERRNOS or fd is None:
                # Anything but contention (permission denied, a bad path, a
                # share that refuses locks) would otherwise poll silently
                # until `deadline` and come back as a plain "compile busy",
                # indistinguishable from real load.
                log.warning("compile: cross-process lock at %s unusable (%s)", path, exc)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_CROSS_PROCESS_LOCK_POLL_SECONDS)


def _release_cross_process_lock(lock):
    if lock is None:
        return
    lock.release()

#: "Result: 0 errors, 2 warnings, 143 msec elapsed" — the summary line. Builds
#: differ on singular/plural and on the "Result:" prefix, so match the counts
#: rather than the whole line.
_ERRORS_RE = re.compile(r"(\d+)\s+error", re.IGNORECASE)
_WARNINGS_RE = re.compile(r"(\d+)\s+warning", re.IGNORECASE)

#: Fallback when no summary line is present: per-diagnostic lines look like
#: "ea.mq5(12,5) : error 123: ';' - unexpected token".
_ERROR_LINE_RE = re.compile(r":\s*error\s+\d+", re.IGNORECASE)
_WARNING_LINE_RE = re.compile(r":\s*warning\s+\d+", re.IGNORECASE)


#: Resolved once per process by _local_toolchain(): (metaeditor, include_dir),
#: or None when no mirror is configured or the mirror could not be built.
_LOCAL_TOOLCHAIN = None
_LOCAL_TOOLCHAIN_RESOLVED = False

#: When the mirrored include tree was last re-checked against the source.
_INCLUDES_CHECKED_AT = 0.0

#: Cached include-tree digest, and the (root, tree signature) it was computed
#: for. See _tree_signature().
_INCLUDE_HASH = None
_INCLUDE_HASH_KEY = None

#: Cached per-header digests, keyed the same way.
_INCLUDE_FILES = None
_INCLUDE_FILES_KEY = None

#: Globs (relative to the include root) whose per-file digests are reported.
_INCLUDE_DIGEST_PATTERNS = tuple(
    pattern.strip() for pattern in (COMPILE_INCLUDE_DIGESTS or "").split(",") if pattern.strip()
)

#: How stale the mirrored include tree may get before it is re-validated.
#:
#: MetaEditor and its Config are effectively immutable between deployments, but
#: the include tree is NOT: a shared library gets edited and every build after
#: that is supposed to pick it up. Resolving the mirror once per process meant
#: an edited .mqh was invisible until the next restart, and the compile that
#: used the old copy still returned ok:true - a silently stale binary, which is
#: the worst failure shape this endpoint has. Bound that window instead.
INCLUDE_REFRESH_SECONDS = 60

#: What MetaEditor actually needs to compile. Deliberately NOT the whole
#: terminal directory - that also holds terminal64.exe, metatester64.exe and
#: Bases, roughly 350MB of things a compile never reads.
_TOOLCHAIN_ITEMS = ("MetaEditor64.exe", "Config", "MQL5")

#: Compile a throwaway EA in the background shortly after start, so the first
#: REAL caller does not pay MetaEditor's cold load - measured at 30-55s on a
#: busy host against ~2-3s warm. Only meaningful with a local mirror, which is
#: also the only configuration where the cold cost is worth eliminating.
WARMUP_ENABLED = bool(COMPILE_LOCAL_CACHE)

#: Wait this long before warming. The VM launches every terminal at boot, and a
#: MetaEditor run added to that contention makes the guest slower precisely when
#: its health probe is most marginal. Warm once things have settled instead.
WARMUP_DELAY_SECONDS = 180

#: Every API process on a VM exposes /compile and they share COMPILE_LOCAL_CACHE,
#: so an ungated warm-up means one MetaEditor per terminal - 20 of them on this
#: host, all at once. A claim file in the shared cache keeps it to one per VM
#: per boot: it records the boot it was made in, and a claim from an earlier
#: boot is replaced. The cache directory survives reboots, so an undated claim
#: (or one that merely expired after an hour) would skip the warm-up on any
#: boot that came sooner than that.
_WARMUP_CLAIM = ".warmup-claim"

#: Boot times read in two processes of the same boot can differ by a second or
#: two on Windows, where they are derived from the uptime counter.
_BOOT_TIME_TOLERANCE_SECONDS = 60


def _is_current(src, dst):
    """True when dst already matches src closely enough to skip re-copying.

    Size plus whole-second mtime. Whole seconds because the mirror and the
    source can sit on filesystems with different timestamp resolution, and a
    sub-second difference there would make every file look stale forever.
    """
    try:
        s_stat = os.stat(src)
        d_stat = os.stat(dst)
    except OSError:
        return False
    return s_stat.st_size == d_stat.st_size and int(s_stat.st_mtime) == int(d_stat.st_mtime)


def _mirror_tree(src, dst):
    """Copy a directory into the mirror, skipping files already current.

    Returns (files copied, files pruned).

    This is deliberately not shutil.copytree(dirs_exist_ok=True): that re-copies
    every file on every call, and this runs at the first compile after each
    process start. The stock MQL5 Include tree is ~260 files, so on a slow
    host-shared mount an unconditional re-copy puts tens of seconds in front of
    the first compile - which is exactly the window where a caller is already
    waiting on a reverse-proxy timeout.
    """
    copied = 0
    expected = set()
    for root, _dirs, files in os.walk(src):
        relative = os.path.relpath(root, src)
        target = dst if relative == "." else os.path.join(dst, relative)
        os.makedirs(target, exist_ok=True)
        for name in files:
            src_file = os.path.join(root, name)
            dst_file = os.path.join(target, name)
            expected.add(os.path.relpath(dst_file, dst))
            if _is_current(src_file, dst_file):
                continue
            # copy2 preserves mtime, which is what makes the next run a no-op.
            shutil.copy2(src_file, dst_file)
            copied += 1

    # Copying alone leaves a one-way mirror: a header deleted from the source
    # stays here and keeps resolving, so `#include <Gone.mqh>` still compiles
    # and the tree the compiler reads drifts from the tree anyone is
    # maintaining. Drop what the source no longer has.
    #
    # Guarded on a non-empty source: if the mount is unreachable the walk above
    # yields nothing, and pruning against that would delete the entire mirror
    # over a transient failure.
    removed = 0
    if expected:
        for root, _dirs, files in os.walk(dst):
            for name in files:
                stale = os.path.join(root, name)
                if os.path.relpath(stale, dst) not in expected:
                    try:
                        os.remove(stale)
                        removed += 1
                    except OSError:
                        pass  # in use or already gone; the next pass retries
    return copied, removed


def _include_root(include_root):
    """The directory the digests cover: <inc>/Include, or <inc> itself."""
    root = os.path.join(include_root, "Include")
    if not os.path.isdir(root):
        root = include_root
    return root if os.path.isdir(root) else None


def _tree_signature(root):
    """(relative path, size, mtime) for every file under `root`, sorted.

    What decides whether a cached digest still describes the tree. The tree
    changes under this process in ways it never sees: an operator edits a
    header in the terminal's live MQL5 folder, or another process refreshes a
    shared mirror. A few hundred stats per compile catch both, where re-reading
    the ~16MB of contents every time would not be worth it.
    """
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            stat = os.stat(path)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            entries.append((relative, stat.st_size, stat.st_mtime_ns))
    return tuple(entries)


def _include_hash(include_root):
    """sha256 over the include tree the compiler was actually given.

    Returns "sha256:<hex>", or None if the tree cannot be read.

    Deliberately hashed from the tree passed as /inc:, NOT from the source it
    was mirrored from. The point of this value is to answer "which library is
    this binary built against" after the fact, so it has to describe what the
    compiler read - if the mirror were stale, a hash of the source would assert
    the opposite and be worse than no hash at all.

    Covers relative paths as well as contents so that adding, renaming or
    removing a header changes the digest, not just editing one.

    Cached against the tree's signature, so an edit by anyone - not only by
    this module's mirror refresh - produces a new digest on the next compile.
    """
    global _INCLUDE_HASH, _INCLUDE_HASH_KEY

    root = _include_root(include_root)
    if root is None:
        return None
    try:
        key = (root, _tree_signature(root))
        if _INCLUDE_HASH_KEY == key and _INCLUDE_HASH:
            return _INCLUDE_HASH
        digest = hashlib.sha256()
        for relative, _size, _mtime in key[1]:
            digest.update(relative.encode("utf-8", "replace") + b"\0")
            with open(os.path.join(root, *relative.split("/")), "rb") as handle:
                for chunk in iter(lambda h=handle: h.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    except OSError as exc:
        log.warning("compile: could not hash include tree (%s)", exc)
        return None

    _INCLUDE_HASH_KEY = key
    _INCLUDE_HASH = "sha256:" + digest.hexdigest()
    return _INCLUDE_HASH


def _include_file_digests(include_root):
    """Per-header digests for the globs in COMPILE_INCLUDE_DIGESTS.

    Returns {relative path: "sha256:<hex>"}, or {} when nothing is configured
    or nothing matches.

    Exists because the tree hash cannot say WHAT moved. An upgrade of the stock
    MQL5 library and an edit of a caller's own shared header both change it,
    and the correct responses are opposites: the first needs no rebuild, the
    second needs every dependent artifact rebuilt. Without this a caller has to
    assume the expensive one.

    A configured header that is absent from the tree is simply absent here, not
    null - "not in the tree the compiler read" is the thing worth knowing
    before a rebuild, and a null would blur it with "present but unreadable".

    Same root and cache key as _include_hash.
    """
    global _INCLUDE_FILES, _INCLUDE_FILES_KEY

    if not _INCLUDE_DIGEST_PATTERNS:
        return {}
    root = _include_root(include_root)
    if root is None:
        return {}

    digests = {}
    try:
        key = (root, _tree_signature(root))
        if _INCLUDE_FILES_KEY == key and _INCLUDE_FILES is not None:
            return _INCLUDE_FILES
        for relative, _size, _mtime in key[1]:
            if not any(
                fnmatch.fnmatch(relative, pattern) for pattern in _INCLUDE_DIGEST_PATTERNS
            ):
                continue
            digest = hashlib.sha256()
            with open(os.path.join(root, *relative.split("/")), "rb") as handle:
                for chunk in iter(lambda h=handle: h.read(1024 * 1024), b""):
                    digest.update(chunk)
            digests[relative] = "sha256:" + digest.hexdigest()
    except OSError as exc:
        log.warning("compile: could not hash individual includes (%s)", exc)
        return {}

    _INCLUDE_FILES_KEY = key
    _INCLUDE_FILES = digests
    return digests


def _refresh_mirrored_includes():
    """Re-validate the mirrored include tree against the source.

    Runs at most once every INCLUDE_REFRESH_SECONDS, and only walks MQL5 - not
    MetaEditor64.exe, which is 105MB and does not change under a running
    process.

    This exists because an edited shared header must reach subsequent builds.
    Without it the mirror is resolved once per process and an updated .mqh is
    invisible until the next restart, while the compile that used the old copy
    still returns ok:true. A silently stale binary is worse than a failed
    compile: nothing downstream can tell it apart from a correct one.

    Caller must hold _COMPILE_LOCK and the cross-process lock: this writes
    into the mirror other processes compile from.
    """
    global _INCLUDES_CHECKED_AT

    if not _LOCAL_TOOLCHAIN or not COMPILE_LOCAL_CACHE:
        return
    if time.monotonic() - _INCLUDES_CHECKED_AT < INCLUDE_REFRESH_SECONDS:
        return

    _INCLUDES_CHECKED_AT = time.monotonic()
    src = os.path.join(os.path.dirname(COMPILE_METAEDITOR), "MQL5")
    dst = os.path.join(COMPILE_LOCAL_CACHE, "MQL5")
    if not os.path.isdir(src):
        return
    try:
        copied, removed = _mirror_tree(src, dst)
        if copied or removed:
            log.info(
                "compile: include tree refreshed (%d changed, %d pruned)", copied, removed
            )
    except Exception as exc:  # noqa: BLE001 - keep compiling with what we have
        log.warning("compile: could not refresh includes (%s), using mirrored copy", exc)


def _local_toolchain():
    """Mirror the compile toolchain onto local disk, once per process.

    Returns (metaeditor_path, include_dir) to compile with, or None to use the
    configured paths as-is.

    Failure here is never fatal: if the mirror cannot be built we log it and
    fall back to the shared copy, which is slower but correct. A compile that
    works slowly beats a compile that stops working because a cache directory
    was not writable.

    Callers must hold _COMPILE_LOCK and the cross-process lock - this writes
    ~100MB into a mirror other processes compile from, and must not run twice
    concurrently or under a running MetaEditor.
    """
    global _LOCAL_TOOLCHAIN, _LOCAL_TOOLCHAIN_RESOLVED, _INCLUDES_CHECKED_AT

    if _LOCAL_TOOLCHAIN_RESOLVED:
        _refresh_mirrored_includes()
        return _LOCAL_TOOLCHAIN

    _LOCAL_TOOLCHAIN_RESOLVED = True
    _INCLUDES_CHECKED_AT = time.monotonic()
    if not COMPILE_LOCAL_CACHE:
        return None

    source_dir = os.path.dirname(COMPILE_METAEDITOR)
    try:
        os.makedirs(COMPILE_LOCAL_CACHE, exist_ok=True)
        for item in _TOOLCHAIN_ITEMS:
            src = os.path.join(source_dir, item)
            dst = os.path.join(COMPILE_LOCAL_CACHE, item)
            if not os.path.exists(src):
                continue
            if os.path.isdir(src):
                copied, removed = _mirror_tree(src, dst)
                if copied or removed:
                    log.info(
                        "compile: mirrored %s (%d refreshed, %d pruned)", item, copied, removed
                    )
            elif not _is_current(src, dst):
                shutil.copy2(src, dst)

        editor = os.path.join(COMPILE_LOCAL_CACHE, "MetaEditor64.exe")
        if not os.path.exists(editor):
            log.warning("compile: local cache built without MetaEditor, using shared copy")
            return None

        # Includes come from the mirrored tree so a compile never reaches back
        # across the slow mount for a .mqh either.
        include_dir = os.path.join(COMPILE_LOCAL_CACHE, "MQL5")
        if not os.path.isdir(include_dir):
            include_dir = COMPILE_INCLUDE_DIR

        _LOCAL_TOOLCHAIN = (editor, include_dir)
        log.info("compile: using local toolchain at %s", COMPILE_LOCAL_CACHE)
    except Exception as exc:  # noqa: BLE001 - fall back, never fail the request
        log.warning("compile: could not build local toolchain (%s), using shared copy", exc)
        _LOCAL_TOOLCHAIN = None

    return _LOCAL_TOOLCHAIN


#: MT4 toolchain, resolved once per process like the MT5 one above: the
#: (metaeditor, include_dir) to compile with, or None when MT4 is not installed.
_MT4_TOOLCHAIN = None
_MT4_TOOLCHAIN_RESOLVED = False
_MT4_INCLUDES_CHECKED_AT = 0.0

#: MetaTrader 4 ships the editor as metaeditor.exe (32-bit) and, on newer
#: builds, metaeditor64.exe. Prefer the 64-bit one when both are present.
_MT4_EDITOR_NAMES = ("metaeditor64.exe", "metaeditor.exe")


def _platform_for(filename):
    """"mt4" for a .mq4 filename, otherwise "mt5" - the endpoint's original
    behaviour, so a caller that never sends .mq4 sees no difference."""
    if isinstance(filename, str) and filename.strip().lower().endswith(".mq4"):
        return "mt4"
    return "mt5"


def _mt4_editor(directory):
    """Path of the MT4 MetaEditor in `directory`, or None if there isn't one."""
    for name in _MT4_EDITOR_NAMES:
        candidate = os.path.join(directory, name)
        if os.path.exists(candidate):
            return candidate
    return None


def _mt4_toolchain():
    """(metaeditor_path, include_dir) for MQL4, or None if MT4 is not installed.

    Mirrors the MT5 logic: with COMPILE_LOCAL_CACHE set the editor, its Config
    and the MQL4 tree are copied to local disk (under <cache>/mt4, so it cannot
    collide with the MT5 mirror) and compiled from there; the include tree is
    re-validated every INCLUDE_REFRESH_SECONDS. Failure to mirror falls back to
    the shared copy rather than failing the request.

    Callers must hold _COMPILE_LOCK.
    """
    global _MT4_TOOLCHAIN, _MT4_TOOLCHAIN_RESOLVED, _MT4_INCLUDES_CHECKED_AT

    source_editor = _mt4_editor(COMPILE_MT4_TERMINAL_DIR)
    if not source_editor:
        return None
    shared = (source_editor, os.path.join(COMPILE_MT4_TERMINAL_DIR, "MQL4"))
    if not COMPILE_LOCAL_CACHE:
        return shared

    cache = os.path.join(COMPILE_LOCAL_CACHE, "mt4")
    src_inc = shared[1]
    dst_inc = os.path.join(cache, "MQL4")
    if _MT4_TOOLCHAIN_RESOLVED:
        if (
            _MT4_TOOLCHAIN
            and os.path.isdir(src_inc)
            and time.monotonic() - _MT4_INCLUDES_CHECKED_AT >= INCLUDE_REFRESH_SECONDS
        ):
            _MT4_INCLUDES_CHECKED_AT = time.monotonic()
            try:
                _mirror_tree(src_inc, dst_inc)
            except Exception as exc:  # noqa: BLE001 - keep the mirrored copy
                log.warning("compile: could not refresh MT4 includes (%s)", exc)
        return _MT4_TOOLCHAIN or shared

    _MT4_TOOLCHAIN_RESOLVED = True
    _MT4_INCLUDES_CHECKED_AT = time.monotonic()
    try:
        os.makedirs(cache, exist_ok=True)
        for item in (os.path.basename(source_editor), "Config", "MQL4"):
            src = os.path.join(COMPILE_MT4_TERMINAL_DIR, item)
            dst = os.path.join(cache, item)
            if not os.path.exists(src):
                continue
            if os.path.isdir(src):
                _mirror_tree(src, dst)
            elif not _is_current(src, dst):
                shutil.copy2(src, dst)
        editor = os.path.join(cache, os.path.basename(source_editor))
        if os.path.exists(editor):
            _MT4_TOOLCHAIN = (editor, dst_inc if os.path.isdir(dst_inc) else src_inc)
            log.info("compile: using local MT4 toolchain at %s", cache)
    except Exception as exc:  # noqa: BLE001 - fall back, never fail the request
        log.warning("compile: could not build local MT4 toolchain (%s), using shared copy", exc)
        _MT4_TOOLCHAIN = None
    return _MT4_TOOLCHAIN or shared


def _tail(text, limit=MAX_LOG_BYTES):
    """Last `limit` bytes of a log, as a string. Never None."""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return text
    # Cut on a character boundary so the tail is still valid UTF-8.
    return encoded[-limit:].decode("utf-8", errors="replace")


def _read_metaeditor_log(path):
    """Decode MetaEditor's log file. Missing or unreadable reads as empty."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    return _read_metaeditor_log_bytes(raw)


def _read_metaeditor_log_bytes(raw):
    """Decode MetaEditor's UTF-16LE log bytes.

    Split out from the file read so the decoding — the part that actually goes
    wrong — is testable against real bytes without a filesystem.

    Falls back through UTF-8 and latin-1 rather than raising: a log we cannot
    decode must not turn a real compile result into a 500.
    """
    if not raw:
        return ""

    for encoding in ("utf-16", "utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # A UTF-16 log decoded as UTF-8 comes back riddled with NULs; treat that
        # as a failed decode rather than returning shredded text.
        if "\x00" in text:
            continue
        return text.replace("\r\n", "\n")

    return raw.decode("utf-8", errors="replace").replace("\x00", "")


def _parse_counts(log_text):
    """(errors, warnings) from a MetaEditor log.

    Prefers the trailing summary line; falls back to counting diagnostics. When
    neither is present the counts are 0 and the caller decides on the .ex5.
    """
    errors = warnings = None

    # Walk backwards: the summary is the last line that carries both counts.
    for line in reversed(log_text.splitlines()):
        if _ERRORS_RE.search(line) and _WARNINGS_RE.search(line):
            errors = int(_ERRORS_RE.search(line).group(1))
            warnings = int(_WARNINGS_RE.search(line).group(1))
            break

    if errors is None:
        errors = len(_ERROR_LINE_RE.findall(log_text))
    if warnings is None:
        warnings = len(_WARNING_LINE_RE.findall(log_text))

    return errors, warnings


def _safe_stem(filename):
    """Reduce a caller-supplied filename to a harmless stem.

    Everything structural is discarded: directories, drive letters, traversal.
    What survives is a conservative character class, because this string becomes
    a filename inside the temp directory AND the .ex5 name we read back.
    """
    if not isinstance(filename, str):
        filename = ""
    # ntpath-style and posix separators, plus drive colons.
    stem = re.split(r"[\\/]", filename)[-1]
    stem = stem.split(":")[-1]
    if stem.lower().endswith((".mq5", ".mq4")):
        stem = stem[:-4]
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._-")
    return stem or "ea"


def _json(payload, status, headers=None):
    """Every exit from this handler goes through here, so the client never sees
    a non-JSON body — which it is documented to treat as a broken host."""
    payload.setdefault("log", "")
    if payload.get("log") is None:
        payload["log"] = ""
    if headers:
        return jsonify(payload), status, headers
    return jsonify(payload), status


#: ea_version is only ever logged. It is kept to a short token so a caller
#: cannot write a forged log line (a newline) or megabytes into the log.
_EA_VERSION_RE = re.compile(r"[A-Za-z0-9._+-]{1,64}")


#: A comment, or a string or character literal (kept whole so a `//` inside a
#: path does not start a comment). An unterminated one runs to the end of the
#: line, or for a block comment to the end of the source.
_COMMENT_OR_LITERAL_RE = re.compile(
    r'//[^\n]*|/\*.*?(?:\*/|\Z)|"(?:\\.|[^"\\\n])*"?' + r"|'(?:\\.|[^'\\\n])*'?",
    re.DOTALL,
)


def _strip_comments(source):
    """`source` with every comment replaced by spaces, newlines kept.

    MetaEditor drops comments before it reads directives, so `/*x*/#include`
    is still an include.
    """
    def blank(match):
        text = match.group(0)
        if text[0] != "/":
            return text
        return re.sub(r"[^\n]", " ", text)

    return _COMMENT_OR_LITERAL_RE.sub(blank, source)


#: The directives that make MetaEditor read a file named in the source:
#: #include (compiled in), #resource (embedded in the .ex5) and #property icon
#: (embedded in the .ex5). Matched more loosely than MetaEditor parses them -
#: any case, spaces after the '#' - because over-matching only means a path
#: gets checked that MetaEditor would have ignored anyway.
_FILE_DIRECTIVE_RE = re.compile(
    r"^[ \t\f\v]*#[ \t\f\v]*(include|resource|property[ \t\f\v]+icon)\b[ \t\f\v]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)

#: A '#' with nothing after it on its line once comments are blanked. C calls
#: it a null directive; no MQL5 code needs one, and a comment spanning a line
#: break between '#' and the directive name would otherwise hide the directive
#: from _FILE_DIRECTIVE_RE while the preprocessor still joins it.
_SPLIT_DIRECTIVE_RE = re.compile(r"^[ \t\f\v]*#[ \t\f\v]*$", re.MULTILINE)

#: Characters some lexers treat as line breaks besides \n. The scan breaks lines
#: on them too: over-splitting only makes the check stricter.
_EXTRA_LINE_BREAKS_RE = re.compile("[\u0085  ]")


def _normalize_line_endings(source):
    """`source` with every \\r\\n and lone \\r turned into \\n.

    The handler scans and writes this same text, so MetaEditor reads exactly
    the lines the scan saw. Python's re only breaks lines on \\n, while a lone
    \\r survives the newline="\\r\\n" write and can start a line for MetaEditor.
    """
    return source.replace("\r\n", "\n").replace("\r", "\n")


def _scan_view(source):
    """`source` as the preprocessor reads directives: extra line breaks split,
    backslash-newline continuations joined, comments blanked."""
    text = _EXTRA_LINE_BREAKS_RE.sub("\n", _normalize_line_endings(source))
    text = text.replace("\\\n", "")
    return _strip_comments(text)
_PATH_ARGUMENT_RE = re.compile(r'"([^"\r\n]*)"|<([^>\r\n]*)>')

#: Windows device names. Opening one blocks or reads a device, not a file.
_DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in range(1, 10)
}


def _outside_path_reason(directive, path):
    """Why `path` could name a file outside the allowed trees, or None.

    Measured on MetaEditor 5.00 build 5836 (tests/real_compile/): #include
    reads an absolute path and a `..` walk out of the temp directory or the
    include tree, with either slash; #property icon reads a `..` walk;
    #resource refuses both itself. The rule here does not lean on which ones
    MetaEditor happens to stop: a path must stay relative to where MetaEditor
    resolves it (the source's temp directory, or the configured MQL5 tree for
    `<...>` and a resource's or icon's leading backslash).
    """
    normalized = path.replace("/", "\\")
    if ":" in normalized:
        return "a drive letter or stream name"
    if normalized.startswith("\\\\"):
        return "a UNC or device path"
    if normalized.startswith("\\") and directive == "include":
        # For #resource and #property icon a leading backslash means the MQL5
        # root. Build 5836 reads it as the source directory for #include, but
        # it is the drive root to Windows itself, and no include needs it.
        return "an absolute path"
    for segment in normalized.split("\\"):
        if segment not in ("", ".") and not segment.strip(" ."):
            # "..", and any other all-dots-and-spaces name, which Windows
            # trims when it resolves a path.
            return "a parent-directory segment"
        if segment.split(".")[0].strip().upper() in _DEVICE_NAMES:
            return "a device name"
    return None


def _check_source_paths(source):
    """Refuse source whose directives could read files outside the sandbox.

    Returns an error message, or None when the source may be compiled.

    MetaEditor resolves these directives itself, with the API process's file
    access, so the handler's own care over paths does not cover them. An
    #include of config.yaml, say, would not compile, but the diagnostics
    quote its tokens back in `log`.
    """
    scanned = _scan_view(source)
    if _SPLIT_DIRECTIVE_RE.search(scanned):
        return "a '#' must be followed by its directive name on the same line"
    for match in _FILE_DIRECTIVE_RE.finditer(scanned):
        directive = match.group(1).split()[0].lower()
        if directive == "property":
            directive = "property icon"
        argument = _PATH_ARGUMENT_RE.match(match.group(2))
        if not argument:
            return f"#{directive} must name its file as a literal \"path\" or <path>"
        path = argument.group(1) if argument.group(1) is not None else argument.group(2)
        reason = _outside_path_reason(directive, path)
        if reason:
            return (
                f"#{directive} {argument.group(0)} is refused: {reason}. Paths must stay "
                "inside the source directory or the server's MQL5 include tree"
            )
    return None


def _boot_time():
    try:
        return int(psutil.boot_time())
    except Exception:  # noqa: BLE001 - no boot time just means no dedupe
        return None


def _claim_warmup():
    """Claim this boot's warm-up. True only for the process that wins it.

    O_CREAT|O_EXCL against a file in the shared cache, because the processes
    racing for this are separate PIDs - a threading.Lock would gate one process
    while the other nineteen went ahead and launched MetaEditor anyway.

    A claim made in an earlier boot is removed first. Two processes can both
    judge the same old claim stale and both end up warming; that costs one
    extra warm-up, which the cross-process lock serializes.
    """
    if not COMPILE_LOCAL_CACHE:
        return False
    booted = _boot_time()
    path = os.path.join(COMPILE_LOCAL_CACHE, _WARMUP_CLAIM)
    try:
        os.makedirs(COMPILE_LOCAL_CACHE, exist_ok=True)
        try:
            with open(path, "r", encoding="ascii", errors="replace") as handle:
                text = handle.read(32).strip()
        except OSError:
            text = None  # no claim yet
        try:
            claimed = None if text is None else int(text)
        except ValueError:
            # Not a boot time, e.g. the timestamp an earlier version wrote.
            # It cannot be this boot's, so it must not block this boot.
            claimed = -1
        if claimed is not None and (
            booted is None or abs(claimed - booted) > _BOOT_TIME_TOLERANCE_SECONDS
        ):
            try:
                os.remove(path)
            except OSError:
                pass  # someone else just replaced it
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, str(booted or 0).encode("ascii"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False  # this boot's warm-up is already claimed


def _warmup():
    """Run one throwaway compile so the first real caller finds MetaEditor warm."""
    time.sleep(WARMUP_DELAY_SECONDS)
    if not _claim_warmup():
        return

    # Non-blocking: if a real compile is already running the toolchain is being
    # warmed by it, and making a caller queue behind a warm-up would defeat the
    # point of having one.
    if not _COMPILE_LOCK.acquire(blocking=False):
        log.info("compile warm-up: skipped, a compile is already running")
        return

    work_dir = None
    cross_lock = None
    try:
        started = time.monotonic()
        warmup_budget = COMPILE_TIMEOUT_SECONDS + 120
        # Winning the warm-up claim only means no OTHER PROCESS is also trying
        # to warm up — it says nothing about a real compile landing on a
        # different process at the same moment and sharing this same
        # installation directory. Taken before the mirror is refreshed, which
        # writes into the tree another process's MetaEditor may be reading.
        cross_lock = _acquire_cross_process_lock(time.monotonic() + warmup_budget)
        if cross_lock is None:
            log.warning("compile warm-up: could not get the cross-process compile lock in time")
            return

        local = _local_toolchain()
        metaeditor, include_dir = local if local else (COMPILE_METAEDITOR, COMPILE_INCLUDE_DIR)
        if not os.path.exists(metaeditor):
            log.warning("compile warm-up: MetaEditor missing at %s", metaeditor)
            return

        os.makedirs(COMPILE_WORK_DIR, exist_ok=True)
        work_dir = tempfile.mkdtemp(prefix="warmup-", dir=COMPILE_WORK_DIR)
        source_path = os.path.join(work_dir, "warmup.mq5")
        with open(source_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write("int OnInit(){return(INIT_SUCCEEDED);}\nvoid OnTick(){}\n")

        subprocess.run(
            [
                metaeditor,
                f"/compile:{source_path}",
                f"/log:{os.path.join(work_dir, 'warmup.log')}",
                f"/inc:{include_dir}",
            ],
            capture_output=True,
            # Generous: the whole point is absorbing the cold load, which is
            # slower than any warm compile this deadline normally covers.
            timeout=warmup_budget,
        )
        log.info("compile warm-up done in %.1fs", time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 - a failed warm-up must not matter
        log.warning(
            "compile warm-up failed (%s); the first real compile pays the cold load", exc
        )
    finally:
        _release_cross_process_lock(cross_lock)
        _COMPILE_LOCK.release()
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


def start_warmup():
    """Kick the warm-up off in the background. Never blocks or fails startup."""
    if not WARMUP_ENABLED:
        return
    log.info(
        "compile warm-up: one throwaway compile per VM in %ds (compile_local_cache is set)",
        WARMUP_DELAY_SECONDS,
    )
    threading.Thread(target=_warmup, daemon=True, name="compile-warmup").start()


def compile_source():
    """POST /compile — synchronous compile of one .mq5 to one .ex5."""
    started = time.monotonic()
    try:
        return _compile_source_inner(started)
    except Exception:  # noqa: BLE001 - the handler must never raise
        # The full traceback goes to the server log and ONLY there. Echoing
        # the exception class and message gave any caller who could provoke a
        # compiler or filesystem error a readout of internal paths and
        # implementation detail.
        log.exception("compile: unhandled error")
        return _json({"ok": False, "log": "internal error"}, 500)


def _body_byte_cap():
    """Upper bound on the raw request body, derived from the source cap.

    JSON escaping inflates the encoded string (worst case 6x for \\uXXXX
    escapes); 4x plus envelope slack admits every realistic encoding of a
    source that is itself within the cap. The authoritative check is on the
    DECODED source below - this one exists so an oversized body is refused
    from its declared Content-Length, before any of it is parsed.
    """
    return 4 * COMPILE_MAX_SOURCE_BYTES + 4096


def _compile_source_inner(started):
    # request.content_length is None for a chunked (Transfer-Encoding:
    # chunked, no Content-Length) request, which would silently skip the
    # size check below and let get_json() buffer an unbounded body into
    # memory before the decoded-source check downstream ever runs. A JSON
    # body this small has no legitimate reason to arrive chunked, so it is
    # refused before anything is read rather than merely falling through to
    # a slower path.
    declared = request.content_length
    if declared is None:
        log.warning("compile: rejected request with no declared Content-Length")
        return _json(
            {"ok": False, "log": "Content-Length header is required"},
            411,
        )
    if declared > _body_byte_cap():
        log.warning("compile: rejected %d-byte body (cap %d)", declared, _body_byte_cap())
        return _json(
            {"ok": False, "log": f"request body exceeds {_body_byte_cap()} bytes"},
            413,
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _json({"ok": False, "log": "body must be a JSON object"}, 400)

    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        return _json({"ok": False, "log": "'source' must be a non-empty string"}, 400)

    # Enforced BEFORE anything touches disk: past this line the source is
    # written into the work dir, so this check is what bounds that write.
    source_bytes = len(source.encode("utf-8"))
    if source_bytes > COMPILE_MAX_SOURCE_BYTES:
        log.warning(
            "compile: rejected %d-byte source (cap %d)",
            source_bytes, COMPILE_MAX_SOURCE_BYTES,
        )
        return _json(
            {
                "ok": False,
                "log": (
                    f"source is {source_bytes} bytes; this server accepts at "
                    f"most {COMPILE_MAX_SOURCE_BYTES} (COMPILE_MAX_SOURCE_BYTES)"
                ),
            },
            413,
        )

    ea_version = body.get("ea_version")
    if ea_version is not None and (
        not isinstance(ea_version, str) or not _EA_VERSION_RE.fullmatch(ea_version)
    ):
        return _json(
            {
                "ok": False,
                "log": "'ea_version' must be 1-64 characters of A-Z a-z 0-9 . _ + -",
            },
            400,
        )

    # Scanned and written as the same text: see _normalize_line_endings.
    source = _normalize_line_endings(source)
    refused = _check_source_paths(source)
    if refused:
        log.warning("compile: refused source: %s", refused)
        return _json({"ok": False, "log": refused}, 400)

    stem = _safe_stem(body.get("filename") or "ea.mq5")
    platform = _platform_for(body.get("filename"))

    if platform == "mt4":
        if not _mt4_editor(COMPILE_MT4_TERMINAL_DIR):
            log.error("compile: MT4 MetaEditor missing in %s", COMPILE_MT4_TERMINAL_DIR)
            return _json(
                {"ok": False, "log": "MT4 MetaEditor is not available on this host"},
                500,
            )
    elif not os.path.exists(COMPILE_METAEDITOR):
        log.error("compile: MetaEditor missing at %s", COMPILE_METAEDITOR)
        # The path is for the operator's log, not the caller's response.
        return _json(
            {"ok": False, "log": "MetaEditor is not available on this host"},
            500,
        )

    # A slot for the running compile plus MAX_WAITING_COMPILES waiters, taken
    # without blocking: past that, answer now rather than park a server thread
    # (see MAX_WAITING_COMPILES).
    if not _COMPILE_SLOTS.acquire(blocking=False):
        log.warning("compile: refused, %d already waiting", MAX_WAITING_COMPILES)
        return _json(
            {
                "ok": False,
                "log": (
                    f"compile busy: {MAX_WAITING_COMPILES} requests are already "
                    "waiting; retry shortly"
                ),
            },
            429,
            {"Retry-After": str(_BUSY_RETRY_AFTER_SECONDS)},
        )
    try:
        # Bound the wait so a stuck compile fails fast for everyone behind it
        # instead of holding connections open. One total budget for BOTH the
        # in-process lock below and the cross-process one inside _run_compile —
        # an absolute deadline off `started`, not two stacked relative timeouts.
        total_lock_budget = COMPILE_TIMEOUT_SECONDS + _LOCK_WAIT_MARGIN_SECONDS
        deadline = started + total_lock_budget
        if not _COMPILE_LOCK.acquire(timeout=max(0, deadline - time.monotonic())):
            log.warning("compile: lock wait exceeded %ss", total_lock_budget)
            return _json(
                {
                    "ok": False,
                    "log": f"compile busy: waited {total_lock_budget}s for the compiler lock",
                },
                504,
            )
        try:
            return _run_compile(stem, source, ea_version, started, deadline, platform)
        finally:
            _COMPILE_LOCK.release()
    finally:
        _COMPILE_SLOTS.release()


def _run_compile(stem, source, ea_version, started, deadline, platform="mt5"):
    # Serialize against every OTHER PROCESS sharing this toolchain (see
    # _CROSS_PROCESS_LOCK_BASENAME) — _COMPILE_LOCK only serializes calls
    # inside this one process. Taken BEFORE _local_toolchain(): refreshing the
    # mirror copies into and prunes the include tree, and another process's
    # MetaEditor must not be reading headers while they are rewritten.
    cross_lock = _acquire_cross_process_lock(deadline)
    if cross_lock is None:
        total_budget = COMPILE_TIMEOUT_SECONDS + _LOCK_WAIT_MARGIN_SECONDS
        log.warning(
            "compile: cross-process lock wait exceeded %ss stem=%s", total_budget, stem
        )
        return _json(
            {
                "ok": False,
                "log": f"compile busy: waited {total_budget}s for the compiler lock",
            },
            504,
        )
    try:
        return _compile_holding_lock(stem, source, ea_version, started, platform)
    finally:
        _release_cross_process_lock(cross_lock)


def _compile_holding_lock(stem, source, ea_version, started, platform="mt5"):
    if platform == "mt4":
        local = _mt4_toolchain()
        if not local:  # removed between the availability check and here
            return _json(
                {"ok": False, "log": "MT4 MetaEditor is not available on this host"},
                500,
            )
        src_ext, bin_ext, bin_key = ".mq4", ".ex4", "ex4_base64"
    else:
        local = _local_toolchain()
        src_ext, bin_ext, bin_key = ".mq5", ".ex5", "ex5_base64"
    metaeditor, include_dir = local if local else (COMPILE_METAEDITOR, COMPILE_INCLUDE_DIR)

    os.makedirs(COMPILE_WORK_DIR, exist_ok=True)
    work_dir = tempfile.mkdtemp(prefix="compile-", dir=COMPILE_WORK_DIR)
    src_path = os.path.join(work_dir, f"{stem}{src_ext}")
    ex5_path = os.path.join(work_dir, f"{stem}{bin_ext}")
    log_path = os.path.join(work_dir, "compile.log")

    try:
        # utf-8-sig: MetaEditor honours a BOM and will otherwise guess the
        # codepage, which mangles non-ASCII string literals in the source.
        with open(src_path, "w", encoding="utf-8-sig", newline="\r\n") as handle:
            handle.write(source)

        cmd = [
            metaeditor,
            f"/compile:{src_path}",
            f"/log:{log_path}",
            f"/inc:{include_dir}",
        ]

        log.info(
            "compile start platform=%s stem=%s ea_version=%s bytes=%d dir=%s",
            platform, stem, ea_version or "-", len(source), work_dir,
        )

        timed_out = False
        returncode = None
        try:
            completed = subprocess.run(
                cmd,
                cwd=work_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=COMPILE_TIMEOUT_SECONDS,
                check=False,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as exc:
            log.error("compile: could not launch MetaEditor: %s", exc)
            # exc carries the executable's path; that stays in the server log.
            return _json({"ok": False, "log": "could not launch MetaEditor"}, 500)

        if timed_out:
            log.warning("compile timeout after %ss stem=%s", COMPILE_TIMEOUT_SECONDS, stem)
            return _json(
                {"ok": False, "log": f"compile timeout after {COMPILE_TIMEOUT_SECONDS}s"},
                504,
            )

        log_text = _read_metaeditor_log(log_path)
        errors, warnings = _parse_counts(log_text)

        ex5_bytes = b""
        if os.path.exists(ex5_path):
            # Size-checked on disk BEFORE the read: an artifact over the cap
            # must not transit memory or get base64-inflated into the
            # response at all. This is a server policy refusal, not a compile
            # failure - the caller's source built fine - so it does not take
            # the 422 path, and the log names the knob to raise.
            try:
                ex5_size = os.path.getsize(ex5_path)
            except OSError:
                ex5_size = 0
            if ex5_size > COMPILE_MAX_EX5_BYTES:
                log.error(
                    "compile: refusing %d-byte .ex5 (cap %d) stem=%s",
                    ex5_size, COMPILE_MAX_EX5_BYTES, stem,
                )
                return _json(
                    {
                        "ok": False,
                        "log": (
                            f"compiled binary is {ex5_size} bytes; this server "
                            f"returns at most {COMPILE_MAX_EX5_BYTES} "
                            f"(COMPILE_MAX_EX5_BYTES)"
                        ),
                    },
                    500,
                )
            try:
                with open(ex5_path, "rb") as handle:
                    ex5_bytes = handle.read()
            except OSError as exc:
                log.error("compile: could not read .ex5: %s", exc)

        elapsed = round(time.monotonic() - started, 3)

        # Success needs BOTH a clean log and an actual binary. Reporting ok:true
        # without a binary is the one thing the client cannot recover from, and
        # MetaEditor's exit code is not usable here because warnings make it
        # non-zero too.
        if errors == 0 and ex5_bytes:
            log.info(
                "compile ok stem=%s bytes=%d warnings=%d rc=%s dur=%.3fs",
                stem, len(ex5_bytes), warnings, returncode, elapsed,
            )
            body = {
                "ok": True,
                bin_key: base64.b64encode(ex5_bytes).decode("ascii"),
                "log": _tail(log_text),
                "warnings": warnings,
            }
            # Identifies the library this binary was built against, so a caller
            # can answer "is this still current?" later without having to trust
            # that a sync had landed at the time. Omitted rather than guessed if
            # the tree cannot be read. Computed while the cross-process lock is
            # still held, so no other process's mirror refresh lands between
            # the compile and the hash.
            # MQL5 only: the digest cache is a single slot keyed on the MQL5
            # tree, and an MT4 build has no shared library to track.
            digest = _include_hash(include_dir) if platform == "mt5" else None
            if digest:
                body["include_hash"] = digest
            # Only alongside the tree hash: on its own it would say which of a
            # caller's headers changed without saying whether anything else did.
            per_file = _include_file_digests(include_dir) if digest else {}
            if per_file:
                body["include_files"] = per_file
            return _json(body, 200)

        # No binary but a clean log means MetaEditor failed in a way it did not
        # report as a diagnostic. That is still the caller's compile failing, so
        # it belongs on the 422 path with at least one error rather than a 500 —
        # but say so, because an empty log would otherwise look like success.
        if errors == 0 and not ex5_bytes:
            errors = 1
            log_text = (
                (log_text.rstrip() + "\n" if log_text.strip() else "")
                + f"compile produced no {bin_ext} (MetaEditor exit code {returncode})"
            )

        log.info(
            "compile failed stem=%s errors=%d warnings=%d rc=%s dur=%.3fs",
            stem, errors, warnings, returncode, elapsed,
        )
        return _json(
            {"ok": False, "log": _tail(log_text), "errors": errors},
            422,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
