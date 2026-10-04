# -*- coding: utf-8 -*-

"""Background auto-sync driven by the `auto_upgrade` option.

When `auto_upgrade` is true and the user has configured an `access_token`
(and, for pulls, a `gist_id`), this module keeps the local `Packages/User`
mirror in sync with the backing gist, mimicking VS Code's Settings Sync:

  * on startup it pulls the latest gist (the original auto_upgrade behaviour);
  * on a background timer (default every few minutes) it both pulls remote
    changes and pushes local changes, so edits made on any machine propagate
    without a manual upload/download.

The loop is a three-way merge against the per-file content snapshot we last
synced. That snapshot is persisted in ``sync.json`` (alongside the gist
revision), so an edit made while Sublime was closed is still recognised as a
local change after restart: it is pushed, and if the same file also changed
remotely it is detected as a conflict — logged, backed up, and resolved in
favour of the remote (the gist stays the shared source of truth) instead of
one side being silently lost. File deletions propagate both ways. Everything
runs on a daemon thread, so it never blocks Sublime.
"""

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import sublime

from .libs import settings, path, http
from .libs.gist import Gist, NotFoundError
from .libs.logger import logger
from . import sync_version as version, sync_manager as manager


DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes
MIN_INTERVAL_MINUTES = 1

# State persisted per sync (sync.json keys):
#   hash       — gist revision (cheap "did the remote move?" pre-check)
#   created_at — revision timestamp
#   files      — {encoded name: sha256} common ancestor for the three-way merge


def should_auto_sync():
    """True when auto-sync is allowed to run at all.

    Requires the option on plus an access token. Pulls also need a gist id,
    but pushing can create one, so the token is the only hard requirement.
    """
    if not settings.get('auto_upgrade'):
        return False
    if not settings.get('access_token'):
        logger.info('auto_upgrade is on but no access_token is configured; '
                    'auto-sync disabled')
        return False
    return True


def _sha(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _on_main(fn, timeout=None):
    """Run fn on Sublime's main thread.

    Dialogs/status must run there, and ``sublime.list_packages()`` only works
    there. With ``timeout`` the worker waits up to that many seconds and gets
    fn's return value; without it the call is fire-and-forget. When nothing
    pumps the event loop (headless tests) fn runs inline.
    """
    box = {}
    done = threading.Event()

    def run():
        # The inline fallback can race the callback already queued on the main
        # loop; run the payload at most once.
        if box.get('ran'):
            return
        box['ran'] = True
        try:
            box['value'] = fn()
        except Exception as e:
            logger.debug('main-thread callback failed: {}'.format(e))
        done.set()

    try:
        sublime.set_timeout(run, 0)
    except Exception:
        run()
        return box.get('value')
    if timeout is not None and not done.wait(timeout):
        run()
    return box.get('value')


def _snapshot_installed():
    """Take the installed-package snapshot on the MAIN thread.

    ``sublime.list_packages()`` only works there (it raises from a worker
    thread). A failed snapshot returns None, which the collector treats as
    "do not filter" (fail-open).
    """
    return _on_main(manager.installed_packages_snapshot, timeout=0.2)


def _current_files(installed='unset'):
    if installed == 'unset':
        installed = _snapshot_installed()
    return manager.get_files(installed=installed)


def _content_hashes(files):
    return {k: _sha(v['content']) for k, v in files.items()}


def _remote_hashes(remote_files):
    """Hashes of successfully fetched remote files (content present)."""
    return {k: _sha(v) for k, v in remote_files.items() if v}


def _current_hashes():
    """Per-file content hashes of what we would upload right now.

    Uses the same collection path as the real upload (filters, uninstalled
    skip, token exclusion), so a change only to a token / empty / excluded
    file never triggers a push.
    """
    return _content_hashes(_current_files())


# Maximum unchanged-revision retries for a file whose raw content keeps
# failing before auto-sync stops asking for it. The key stays in the
# baseline (so it is never mistaken for a remote deletion); a later gist
# revision rearms the retry. Bounds background traffic and log noise when a
# raw_url is permanently gone (403/404).
PENDING_MAX_ATTEMPTS = 5


def _load_state():
    """Return ``(gist_revision, {name: hash}, {name: attempts_left})`` from
    sync.json."""
    info = version.get_local_version() or {}
    files = info.get('files')
    pending = info.get('pending')
    if not isinstance(pending, dict):
        pending = {}
    return (info.get('hash'),
            dict(files) if isinstance(files, dict) else {},
            {k: v for k, v in pending.items() if isinstance(v, int) and v > 0})


def _head_commit(g):
    """The gist's current commit as ``(version, committed_at)``."""
    commit = (g.get('history') or [{}])[0] if isinstance(g, dict) else {}
    return commit.get('version'), commit.get('committed_at')


def _name_map(g):
    """canonical internal key -> list of filenames the gist stores for it.

    Gists created by other tools (or pushed via git) can carry literal path
    separators (``sub/C.sublime-settings``) while this plugin's internal key
    space is percent-encoded (``sub%2FC.sublime-settings``); older builds even
    left BOTH names as twins. Writes use this map to rename/delete the REAL
    remote filename(s) instead of forking or resurrecting duplicates.
    """
    grouped = {}
    for name in (g.get('files') or {}):
        grouped.setdefault(path.canonical(name), []).append(name)
    return grouped


def _normalise_gist_files(g, proxies=None, only_keys=None):
    """Map a gist payload to ``({encoded_name: content}, name_map)`` via each
    file's ``raw_url``; ``name_map`` is built in the same pass (see
    ``_name_map``) and always covers every file, even with ``only_keys``.

    We deliberately never read the API's inline ``content`` field: GitHub
    truncates it for files larger than ~1MB (``truncated: true``) and omits it
    entirely for files pushed via git, so relying on it would make auto-sync
    silently skip both kinds of file. ``raw_url`` always returns the complete,
    authoritative bytes (verified for a 6.28 MB file). ``proxies`` comes from
    the same validated ``Gist`` client used for the listing, so manual
    Download and auto-sync share one proxy configuration.

    ``only_keys`` restricts the raw downloads to those canonical keys, used on
    unchanged-revision polls to retry only keys a previous merge could not
    fetch. A mapped content value of None means the file exists but its
    content could not be fetched this cycle (network error or non-200);
    callers skip it rather than treating it as a deletion, so the pull is
    retried later.
    """
    name_map = _name_map(g)
    files = {}
    for name, meta in (g.get('files') or {}).items():
        key = path.canonical(name)
        if only_keys is not None and key not in only_keys:
            continue
        content = None
        raw_url = meta.get('raw_url') if isinstance(meta, dict) else None
        if raw_url:
            try:
                r = http.request('GET', raw_url, proxies=proxies)
            except Exception as e:
                logger.warning('auto-sync could not fetch raw file: {}'.format(raw_url))
                logger.exception(e)
            else:
                if r.status_code == 200:
                    content = r.text
                else:
                    logger.warning(
                        'auto-sync raw fetch returned status {}: {}'.format(
                            r.status_code, raw_url))
        files[key] = content
    return files, name_map


def _build_payload(keys, current, hashes, baseline, name_map):
    """Build a gist PATCH payload and the matching new baseline for ``keys``.

    A present file uploads its content (``{name: {'content': ...}}``); a key
    missing from disk is deleted remotely (``{name: None}``) only when the
    file is genuinely gone, not when it was merely filtered out.

    ``name_map`` maps canonical internal keys to the filename(s) the gist
    actually carries (foreign tools keep literal path separators, and old
    builds may have left twins). A write to a foreign-only file uses the gist
    rename form (``{old_name: {'filename': canonical, ...}}``); a canonical
    twin present is updated while the foreign twin is nulled. Deletions null
    every real remote name, so foreign names converge instead of resurrecting
    deleted files.
    """
    name_map = name_map or {}
    payload, new_baseline = {}, dict(baseline)
    for k in keys:
        actuals = name_map.get(k) or []
        if k in current:
            entry = {'content': current[k]['content']}
            if actuals and k not in actuals:
                # Foreign name(s) only: rename the first to canonical, drop
                # any extra twins, all in the same PATCH.
                entry['filename'] = k
                payload[actuals[0]] = entry
                for twin in actuals[1:]:
                    payload[twin] = None
            else:
                # Plain key, or the canonical name already exists: update it
                # and null any foreign twins.
                payload[k] = entry
                for twin in actuals:
                    if twin != k:
                        payload[twin] = None
            new_baseline[k] = hashes[k]
        elif not manager.user_file_exists(k):
            # A genuinely gone file removes every remote name carrying it;
            # with no remote name the plain key is nulled (local-only key).
            for target in actuals or [k]:
                payload[target] = None
            new_baseline.pop(k, None)
    return payload, new_baseline


def _fetch_remote(last_rev=None, only_keys=None):
    """Fetch the gist listing and return
    ``(revision_id, committed_at, {encoded_name: content}, name_map)``.

    Per-file ``raw_url`` downloads happen only when content is needed:

    * a revision different from ``last_rev``: every file is fetched (a full
      three-way merge);
    * an unchanged revision with ``only_keys``: only those keys, used to
      retry content an earlier merge could not fetch;
    * an unchanged revision with nothing required: no raw downloads at all.

    Idle polls therefore cost a single lightweight listing instead of
    re-downloading the whole gist, and one permanently failing file can only
    ever cost its own single retry per cycle.

    Returns ``(None, None, {}, {})`` on a transient failure (retry next
    cycle); raises NotFoundError on a deleted gist so the caller surfaces it.
    """
    gid = settings.get('gist_id')
    if not gid:
        return None, None, {}, {}
    try:
        api = Gist.from_settings()
        g = api.get(gid)
        rev, committed_at = _head_commit(g)
        if last_rev is None or rev != last_rev:
            files, name_map = _normalise_gist_files(g, api.proxies)
        elif only_keys:
            files, name_map = _normalise_gist_files(
                g, api.proxies, only_keys=only_keys)
        else:
            # Unchanged and nothing pending: skip every raw download; the
            # listing alone still yields the filename map for local pushes.
            files, name_map = {}, _name_map(g)
        return rev, committed_at, files, name_map
    except NotFoundError:
        raise
    except Exception as e:
        logger.exception(e)
        return None, None, {}, {}


def _apply_remote(remote_files, to_pull):
    """Write only the requested gist files into Packages/User (a delta pull)."""
    files = {k: remote_files[k] for k in to_pull if k in remote_files}
    if not files:
        return
    try:
        manager.write_user_files(files, preserve_packages=True)
    except Exception as e:
        logger.exception(e)


def _push(payload):
    """Upload a gist ``files`` payload: ``{name: {'content': ...}}`` to update
    a file, ``{name: None}`` to delete one (Gist PATCH semantics).

    Creates the gist when no gist_id is configured (deletion entries are not
    valid there). Returns the gist response, or None on failure.
    """
    if not payload:
        logger.info('auto-sync push skipped: no files to upload')
        return None
    gid = settings.get('gist_id')
    if not gid and any(v is None for v in payload.values()):
        logger.warning('auto-sync cannot delete files while creating the gist; '
                       'retrying next cycle')
        return None
    try:
        gist_api = Gist.from_settings()
        data = {'files': payload}
        if gid:
            g = gist_api.update(gid, data=data)
        else:
            data['description'] = 'SyncSettingsReborn backup'
            g = gist_api.create(data)
            settings.update('gist_id', g['id'])
            logger.info('auto-sync created gist {}'.format(g['id']))
        return g
    except Exception as e:
        logger.exception(e)
        return None


def _backup_conflicts(conflicts, current):
    """Save the local version of each conflicted file so nothing is lost.

    Resolving a conflict by taking the remote version overwrites (or deletes)
    the local one; stashing it under
    ``~/.sync_settings_reborn/conflicts/<timestamp>/`` lets the user recover
    their edit by hand.
    """
    if not conflicts:
        return
    stamp = path.join(os.path.dirname(version.file_path),
                      'conflicts', str(int(time.time())))
    for k in conflicts:
        content = current.get(k, {}).get('content')
        if content is None:
            continue
        dest = path.join(stamp, k)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, 'w') as f:
                f.write(content)
        except Exception as e:
            logger.warning('could not back up conflicted file {}: {}'.format(k, e))


@dataclass
class MergeInputs:
    """Inputs for one apply-and-push step, grouped so the two shared call sites
    (full merge and pending-key retry) stay readable instead of passing ten
    positional args.
    """
    present: List[str]
    deleted: List[str]
    conflicts: List[str]
    remote_files: Dict[str, Optional[str]]
    installed: object
    local_changed: Set[str]
    last: Dict[str, str]
    current: Dict[str, dict]
    remote_hashes: Dict[str, str]


class AutoSync:
    """A restartable background auto-sync loop using three-way merge."""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._interval = DEFAULT_INTERVAL_SECONDS
        # Serialises the daemon cycle against a manual Upload/Download, which
        # runs on a ThreadProgress worker and calls adopt(). Without it the two
        # can interleave their read-modify-write of the sync state and persist a
        # stale baseline over a fresh one. Only _run and adopt take it, so a
        # plain Lock suffices.
        self._lock = threading.Lock()
        # The snapshot we last synced (per-file content hashes). This is the
        # common ancestor used to compute local vs remote deltas, and it is
        # persisted to sync.json so offline edits survive a restart.
        self._last_synced = {}
        # The gist revision we last observed, for a cheap unchanged pre-check.
        self._last_seen_remote = None
        self._last_committed_at = None
        # Keys whose content a merge could not fetch: {canonical key: retries
        # left}. Unchanged-revision polls re-request only these raw_urls.
        self._pending = {}
        # canonical key -> actual gist filename from the latest listing/push,
        # so writes rename/delete the real remote name.
        self._remote_name_map = {}
        # When set to a gist id that gist 404'd: sync is actually paused (no
        # more polls of it) and the dialog was shown, until gist_id changes.
        self._missing_gist = None

    def _configure_interval(self):
        val = settings.get('auto_sync_interval')
        try:
            minutes = int(val)
        except (TypeError, ValueError):
            return
        if minutes >= MIN_INTERVAL_MINUTES:
            self._interval = minutes * 60
        else:
            logger.warning(
                'auto_sync_interval must be an integer number of minutes '
                '>= {}; keeping the default of {} minutes'.format(
                    MIN_INTERVAL_MINUTES, DEFAULT_INTERVAL_SECONDS // 60))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._configure_interval()
        self._stop.clear()
        self._missing_gist = None
        # Restore the persisted common ancestor. We deliberately do NOT seed it
        # from the current disk state: doing so would make edits performed
        # while Sublime was closed look "already synced" after a restart.
        rev, files, pending = _load_state()
        self._last_synced = files
        self._last_seen_remote = rev
        self._pending = dict(pending)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info('auto-sync started (interval {}s)'.format(self._interval))

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None
        logger.info('auto-sync stopped')

    def _run(self):
        # Brief grace period so Sublime has finished loading settings before
        # the first network call.
        if self._stop.wait(min(5, self._interval)):
            return
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._sync_once()
            except Exception as e:
                logger.exception(e)
            if self._stop.wait(self._interval):
                break

    # ---- state bookkeeping -------------------------------------------------

    def _persist_state(self):
        version.update_config_file({
            'hash': self._last_seen_remote,
            'created_at': self._last_committed_at or '',
            'files': self._last_synced,
            'pending': self._pending,
        })

    def adopt(self, rev, committed_at, baseline, remote_names=None):
        """Adopt an externally established sync point (a manual Upload or
        Download command) so its files don't look like fresh deltas here."""
        with self._lock:
            self._last_seen_remote = rev
            self._last_committed_at = committed_at
            self._last_synced = dict(baseline)
            # A manual command just established a complete, consistent state:
            # nothing is pending, and its gist response defines the real names.
            self._pending = {}
            self._remote_name_map = dict(remote_names or {})
            self._missing_gist = None
            try:
                self._persist_state()
            except Exception as e:
                logger.warning('auto-sync could not persist adopted state: {}'.format(e))

    def _record_push(self, g):
        self._last_seen_remote, self._last_committed_at = _head_commit(g)
        # The response reflects any renames our PATCH just performed.
        self._remote_name_map = _name_map(g)
        self._missing_gist = None
        logger.info('auto-sync push complete')

    def _warn_missing_gist(self, gid):
        # Caller only reaches here when the gist 404s; the id guard makes the
        # dialog one-shot and keeps the pause honest across settings changes.
        if self._missing_gist == gid:
            return
        self._missing_gist = gid
        logger.error('auto-sync gist `{}` is gone (404); pausing until the '
                     'gist_id setting changes'.format(gid))
        msg = (
            'SyncSettingsReborn:\n\n'
            'The configured gist no longer exists (or the token cannot access it).\n\n'
            'Automatic sync is paused. Clear or change `gist_id` in the settings '
            'to resume.'
        )
        _on_main(lambda: sublime.message_dialog(msg))

    # ---- sync cycles -------------------------------------------------------

    def _sync_once(self):
        gid = settings.get('gist_id')
        if self._missing_gist is not None and self._missing_gist != gid:
            # gist_id was repointed away from the dead gist: resume syncing it.
            self._missing_gist = None
        elif self._missing_gist is not None and gid:
            # Still paused on the same dead gist; a cleared gist_id falls
            # through to the initial push below.
            return
        if not gid:
            # No gist yet: publish the whole mirror so the first gist is
            # complete, no matter what the persisted baseline remembers.
            self._initial_push()
            return
        try:
            rev, committed_at, remote_files, name_map = _fetch_remote(
                self._last_seen_remote,
                only_keys=set(self._pending) or None)
        except NotFoundError:
            self._warn_missing_gist(gid)
            return
        if rev is None:
            # Transient failure; retry next cycle rather than guessing.
            return
        self._remote_name_map = name_map
        if rev == self._last_seen_remote:
            # Remote unchanged: retry keys a previous merge could not fetch;
            # when that resolves it also pushes this cycle's local deltas.
            if self._pending and self._retry_pending(remote_files):
                return
            self._push_local_delta()
            return
        self._merge(rev, committed_at, remote_files)

    def _initial_push(self):
        current = _current_files()
        if not current:
            logger.info('auto-sync push skipped: no files to upload')
            return
        current_hashes = _content_hashes(current)
        # A brand-new gist carries none of the foreign names we may have
        # recorded for a previously 404'd gist, so pass an empty name map:
        # plain canonical keys, never rename forms. (An empty baseline means
        # there are no deletions to consider either.)
        payload, new_baseline = _build_payload(set(current), current,
                                               current_hashes, {}, {})
        g = _push(payload)
        if g is None:
            return
        self._record_push(g)
        self._last_synced = new_baseline
        self._persist_state()

    def _push_local_delta(self):
        """Push only the files that changed (or were deleted) locally, no
        remote merge."""
        current = _current_files()
        current_hashes = _content_hashes(current)
        last = self._last_synced or {}
        changed = {k for k in set(current_hashes) | set(last)
                   if current_hashes.get(k) != last.get(k)}
        payload, new_baseline = _build_payload(changed, current,
                                               current_hashes, last,
                                               self._remote_name_map)
        if not payload:
            return
        g = _push(payload)
        if g is None:
            # Keep the baseline untouched; the whole delta is retried.
            return
        # The PATCH moved the gist: record its revision so the next cycle
        # doesn't fetch-and-merge a revision we already know.
        self._record_push(g)
        self._last_synced = new_baseline
        self._persist_state()

    def _warn_and_backup_conflicts(self, conflicts, current):
        """The gist wins a conflict: warn the user and back up recoverable
        local edits (files present on disk) before the remote overwrites."""
        backup_keys = [k for k in conflicts if k in current]
        if not backup_keys:
            return
        names = ', '.join(path.decode(k) for k in backup_keys)
        msg = 'SyncSettingsReborn: auto-sync conflict on: {}'.format(names)
        logger.warning(msg)
        _on_main(lambda m=msg: sublime.status_message(m))
        _backup_conflicts(backup_keys, current)

    def _consume_remote_wins(self, m):
        """Apply gist-wins writes/deletions and converge onto them.

        Shared by full merges and pending-key retries: back up local
        conflicts, write pulled content / delete remotely removed files,
        best-effort install missing packages, then re-scan (writes may merge
        Package Control content) and re-baseline. Returns
        ``(current, current_hashes, new_baseline, push_after_pull)``;
        push_after_pull are pulled files whose post-write content differs from
        the raw remote (a local merge result) and must be pushed back.
        ``m.remote_hashes`` is precomputed by the caller (no recompute here).
        """
        self._warn_and_backup_conflicts(m.conflicts, m.current)
        if m.present:
            _apply_remote(m.remote_files, set(m.present))
        if m.deleted:
            manager.delete_user_files(sorted(m.deleted))
        # Best-effort: bring this machine's plugin set in line with the remote
        # one, so a sync on a fresh machine converges without a manual Download.
        pc_key = path.encode('Package Control.sublime-settings')
        pc_content = m.remote_files.get(pc_key)
        if pc_key in m.present and pc_content:
            manager.install_missing_packages(
                manager.installed_packages_from_content(pc_content))
        # Re-collect AFTER writing: write_user_files may merge content
        # (Package Control installed_packages union), and the push must carry
        # that merged result, not a stale pre-pull copy.
        current = m.current
        if m.present or m.deleted:
            current = _current_files(m.installed)
        current_hashes = _content_hashes(current)
        # Pulled files join the baseline at post-write hashes; remotely
        # deleted files leave it.
        new_baseline = dict(m.last)
        for k in m.present:
            if k in current_hashes:
                new_baseline[k] = current_hashes[k]
        for k in m.deleted:
            new_baseline.pop(k, None)
        push_after_pull = {k for k in m.present
                           if k in current_hashes
                           and current_hashes[k] != m.remote_hashes.get(k)}
        return current, current_hashes, new_baseline, push_after_pull

    def _apply_and_push(self, m):
        """Apply remote-wins via ``_consume_remote_wins``, then push local and
        merged deltas. Returns the updated baseline, or ``None`` when the push
        failed so the caller keeps the prior baseline and retries next cycle.
        """
        current, current_hashes, new_baseline, push_after_pull = \
            self._consume_remote_wins(m)
        push_keys = (set(m.local_changed) - set(m.conflicts)) | push_after_pull
        payload, new_baseline = _build_payload(
            push_keys, current, current_hashes, new_baseline,
            self._remote_name_map)
        if not payload:
            return new_baseline
        g = _push(payload)
        if g is None:
            return None
        self._record_push(g)
        return new_baseline

    def _merge(self, rev, committed_at, remote_files):
        installed = _snapshot_installed()
        current = _current_files(installed)
        current_hashes = _content_hashes(current)
        # A present key with falsy content means the remote content could not
        # be fetched this cycle (network error / non-200); such keys must not
        # be treated as deletions.
        remote_hashes = _remote_hashes(remote_files)
        last = dict(self._last_synced or {})

        all_keys = set(current_hashes) | set(last) | set(remote_hashes)
        local_changed = {k for k in all_keys if current_hashes.get(k) != last.get(k)}
        remote_changed = {k for k in all_keys if remote_hashes.get(k) != last.get(k)}

        # A conflict: the same file moved both locally and remotely. The gist
        # wins; back up recoverable local edits first (covers both
        # remote-modified and remote-deleted cases).
        conflicts = sorted(local_changed & remote_changed)
        # Delta pull (conflicts resolve to the remote side, including a
        # remote deletion). Classify into write / delete / unavailable.
        pull_keys = (remote_changed - local_changed) | set(conflicts)
        pull_present, pull_deleted, pull_unavailable = [], [], []
        for k in pull_keys:
            if k not in remote_files:
                pull_deleted.append(k)
            elif remote_files[k]:
                pull_present.append(k)
            else:
                pull_unavailable.append(k)

        m = MergeInputs(pull_present, pull_deleted, conflicts, remote_files,
                        installed, local_changed, last, current, remote_hashes)
        new_baseline = self._apply_and_push(m)
        if new_baseline is None:
            # Network/API failure: do not move the baseline; the whole merge is
            # retried next cycle (pulls are idempotent).
            return

        # Adopt the observed revision normally; keys whose content could not
        # be fetched go into a per-key retry set, so unchanged-revision polls
        # re-request only those raw_urls instead of re-downloading the whole
        # gist. A new revision rearms every budget (the gist genuinely moved).
        self._pending = {k: PENDING_MAX_ATTEMPTS - 1 for k in pull_unavailable}
        self._last_seen_remote = rev
        self._last_committed_at = committed_at
        self._last_synced = new_baseline
        self._persist_state()

    def _spend_pending(self, failed_keys):
        """Decrement retry budgets for keys that failed on a retry poll.

        Every key is already in ``self._pending``. Keys that recover simply
        disappear (they are not in ``failed_keys``). Keys whose budget runs
        out are dropped with a loud error: they keep their old baseline entry,
        so the gist revision still advances and they are never treated as
        deletions; the next changed gist revision rearms them via ``_merge``.
        """
        pending = {}
        for k in failed_keys:
            attempts = self._pending[k] - 1
            if attempts > 0:
                pending[k] = attempts
            else:
                logger.error('auto-sync giving up fetching {} after {} '
                             'attempts; keeping the last synced copy. It will '
                             'be retried if the gist changes.'.format(
                                 path.decode(k), PENDING_MAX_ATTEMPTS))
        if failed_keys:
            logger.warning('auto-sync could not fetch remote content for: '
                           '{}'.format(', '.join(sorted(failed_keys))))
        return pending

    def _retry_pending(self, remote_files):
        """Apply content recovered for pending keys on an unchanged-revision
        poll (only those keys' raw_urls were requested), using the exact same
        gist-wins rules as ``_merge``. Also pushes ordinary local deltas seen
        on the same pre/post-write scans, saving a separate push cycle.

        Returns False when nothing recovered (budgets spent; the caller should
        still run its usual local-delta push), True once state moved.
        """
        pending = set(self._pending)
        fetched = {k: v for k, v in remote_files.items() if v}
        if not fetched:
            # Nothing recovered. A pending key missing from the listing at the
            # SAME revision cannot happen (revisions are immutable snapshots;
            # a changed gist goes through _merge), so it is just retried.
            self._pending = self._spend_pending(sorted(pending))
            self._persist_state()
            return False

        installed = _snapshot_installed()
        current = _current_files(installed)
        current_hashes = _content_hashes(current)
        last = dict(self._last_synced or {})

        # The persisted baseline is still the common ancestor: a local edit
        # to a key the gist also changed while pending is a conflict.
        local_changed = {k for k in set(current_hashes) | set(last)
                         if current_hashes.get(k) != last.get(k)}
        conflicts = sorted(set(fetched) & local_changed)
        remote_hashes = _remote_hashes(remote_files)

        m = MergeInputs(set(fetched), (), conflicts, remote_files, installed,
                        local_changed, last, current, remote_hashes)
        new_baseline = self._apply_and_push(m)
        if new_baseline is None:
            # Nothing moves; pulls are idempotent and the caller's local
            # delta path retries the push next.
            return False

        self._last_synced = new_baseline
        self._pending = self._spend_pending(
            sorted(pending - set(fetched)))
        self._persist_state()
        return True


# Module-level singleton so plugin_loaded / plugin_unloaded can manage it, and
# repeated reloads (the 1_reloader path) don't spawn duplicate loops.
_auto_sync = AutoSync()


def startup_sync():
    """Start the background auto-sync loop (called from the package root's
    plugin_loaded, since Sublime only invokes that hook on root modules)."""
    if not should_auto_sync():
        return
    _auto_sync.start()


def shutdown():
    _auto_sync.stop()


def _adopt_gist(g, baseline, kind):
    try:
        rev, committed_at = _head_commit(g)
        _auto_sync.adopt(rev, committed_at, baseline, _name_map(g))
    except Exception as e:
        logger.warning('auto-sync could not adopt manual {}: {}'.format(kind, e))


def adopt_manual_upload(files, g):
    """Re-baseline auto-sync after a successful manual Upload."""
    baseline = {k: _sha(v['content']) for k, v in files.items()
                if isinstance(v, dict) and v.get('content') is not None}
    _adopt_gist(g, baseline, 'upload')


def _restored_baseline(g):
    """Hash the files a manual Download just restored into Packages/User.

    Those bytes were already downloaded (``fetch_files``) and installed
    (``move_files``) by the Download command, so the baseline is taken from a
    local disk scan with zero extra HTTP traffic. The scan uses the same
    collection/hash pipeline as the post-pull baseline in ``_merge``
    (exclude/include, token and uninstalled-package filters included), so the
    next background cycle stays quiet. Keys the gist does not carry are
    irrelevant; keys filtered out locally are simply absent.
    """
    remote_keys = set(_name_map(g))
    return {k: h for k, h in _current_hashes().items() if k in remote_keys}


def adopt_manual_download(g):
    """Re-baseline auto-sync after a successful manual Download.

    The baseline is hashed from the files just restored on disk (never
    re-fetched over HTTP), covering files of any size the raw endpoint serves.
    """
    _adopt_gist(g, _restored_baseline(g), 'download')
