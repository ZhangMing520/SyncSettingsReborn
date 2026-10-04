# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import unittest
import mock

from .mocks import gist_race

from sync_settings_reborn.libs import http as http_lib

from sync_settings_reborn import auto_sync
from sync_settings_reborn.libs.gist import NotFoundError


def _h(content):
    return auto_sync._sha(content)


def _gist(rev='r1'):
    return {'id': 'g1', 'history': [{'version': rev, 'committed_at': 't'}]}


def _files(d):
    # Shape returned by manager.get_files(): {encoded: {'content':, 'path':}}
    return {k: {'content': v, 'path': os.path.join('/tmp/User', k)}
            for k, v in d.items()}


def _resp(status_code=200, text=''):
    r = mock.MagicMock(status_code=status_code)
    r.text = text
    return r


class TestShouldAutoSync(unittest.TestCase):
    @mock.patch('sync_settings_reborn.auto_sync.settings.get')
    def test_enabled_with_token(self, get):
        def fake_get(key, default=None):
            return {'auto_upgrade': True, 'access_token': 'tok'}.get(key, default)
        get.side_effect = fake_get
        self.assertTrue(auto_sync.should_auto_sync())

    @mock.patch('sync_settings_reborn.auto_sync.settings.get')
    def test_disabled_when_option_off(self, get):
        def fake_get(key, default=None):
            return {'auto_upgrade': False, 'access_token': 'tok'}.get(key, default)
        get.side_effect = fake_get
        self.assertFalse(auto_sync.should_auto_sync())

    @mock.patch('sync_settings_reborn.auto_sync.settings.get')
    def test_disabled_without_token(self, get):
        def fake_get(key, default=None):
            return {'auto_upgrade': True, 'access_token': ''}.get(key, default)
        get.side_effect = fake_get
        self.assertFalse(auto_sync.should_auto_sync())


class TestInterval(unittest.TestCase):
    def _svc(self, val):
        with mock.patch('sync_settings_reborn.auto_sync.settings.get',
                        side_effect=lambda k, d=None: val if k == 'auto_sync_interval' else d):
            svc = auto_sync.AutoSync()
            svc._configure_interval()
            return svc

    def test_minutes_to_seconds(self):
        self.assertEqual(self._svc(2)._interval, 120)

    def test_negative_ignored(self):
        self.assertEqual(self._svc(-1)._interval, auto_sync.DEFAULT_INTERVAL_SECONDS)

    def test_zero_ignored(self):
        self.assertEqual(self._svc(0)._interval, auto_sync.DEFAULT_INTERVAL_SECONDS)

    def test_subminute_float_ignored(self):
        # int(0.5) == 0 would otherwise create a zero-timeout busy loop.
        self.assertEqual(self._svc(0.5)._interval, auto_sync.DEFAULT_INTERVAL_SECONDS)

    def test_garbage_ignored(self):
        self.assertEqual(self._svc('soon')._interval, auto_sync.DEFAULT_INTERVAL_SECONDS)


class TestCurrentHashes(unittest.TestCase):
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    def test_deterministic(self, get_files, _snap):
        get_files.return_value = {
            'A.sublime-settings': {'content': 'x', 'path': '/a'},
            'B.sublime-settings': {'content': 'y', 'path': '/b'},
        }
        first = auto_sync._current_hashes()
        get_files.return_value = {
            'B.sublime-settings': {'content': 'y', 'path': '/b'},
            'A.sublime-settings': {'content': 'x', 'path': '/a'},
        }
        self.assertEqual(first, auto_sync._current_hashes())

    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    def test_changes_with_content(self, get_files, _snap):
        get_files.return_value = {'A.sublime-settings': {'content': 'x', 'path': '/a'}}
        h1 = auto_sync._current_hashes()
        get_files.return_value = {'A.sublime-settings': {'content': 'z', 'path': '/a'}}
        self.assertNotEqual(h1, auto_sync._current_hashes())


def _patch_settings(gist_id='g1'):
    patcher = mock.patch('sync_settings_reborn.auto_sync.settings.get',
                         side_effect=lambda k, d=None: {'gist_id': gist_id}.get(k, d))
    patcher.start()
    return patcher


class TestSyncOnce(unittest.TestCase):
    def setUp(self):
        self.svc = auto_sync.AutoSync()
        self.svc._last_seen_remote = 'r1'
        self.svc._last_synced = {}
        self._persist = mock.patch(
            'sync_settings_reborn.auto_sync.version.update_config_file').start()

    def tearDown(self):
        mock.patch.stopall()

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._push', return_value=_gist('r1-p'))
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r1', 't', {}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_remote_unchanged_pushes_local_delta(self, _snap, get_files,
                                                 _fetch, _push, _exists):
        _patch_settings(gist_id='g1')
        get_files.return_value = _files({'A.sublime-settings': 'x',
                                         'B.sublime-settings': 'y'})
        self.svc._last_synced = {'A.sublime-settings': _h('x')}  # B is new locally
        self.svc._sync_once()
        # The single gist listing reports the same revision; its body is ignored
        # and only the local delta is pushed. The last-seen revision is passed
        # in so the fetch skips per-file raw downloads entirely.
        _fetch.assert_called_once_with('r1', only_keys=None)
        _push.assert_called_once()
        pushed = set(_push.call_args.args[0])
        self.assertEqual(pushed, {'B.sublime-settings'})
        # The PATCH response revision is recorded, so the next cycle doesn't
        # redundantly merge a revision we ourselves just created.
        self.assertEqual(self.svc._last_seen_remote, 'r1-p')

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'A.sublime-settings': 'x',
                                          'C.sublime-settings': 'z'}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_remote_changed_no_conflict(self, _snap, get_files, _fetch,
                                        write, _push, _exists):
        _patch_settings(gist_id='g1')
        # Before the pull the disk has A and local-new B; after the remote pull
        # writes C, a re-scan of the disk sees A, B and C.
        get_files.side_effect = [
            _files({'A.sublime-settings': 'x', 'B.sublime-settings': 'y'}),
            _files({'A.sublime-settings': 'x', 'B.sublime-settings': 'y',
                    'C.sublime-settings': 'z'}),
        ]
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        # Remote added C -> pulled; local added B -> pushed. No overlap.
        write.assert_called_once_with({'C.sublime-settings': 'z'},
                                      preserve_packages=True)
        _push.assert_called_once()
        self.assertEqual(set(_push.call_args.args[0]), {'B.sublime-settings'})
        # Baseline follows the new revision and remembers the pulled file.
        self.assertEqual(self.svc._last_seen_remote, 'r2')
        self.assertEqual(self.svc._last_synced['C.sublime-settings'], _h('z'))

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._backup_conflicts')
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'A.sublime-settings': 'x3'}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_conflict_takes_remote_and_backs_up(self, _snap, get_files, _fetch,
                                                write, _push, backup, _exists):
        _patch_settings(gist_id='g1')
        # First scan (before the remote write) sees the local edit; re-scan
        # after pulling sees the remote version now on disk.
        get_files.side_effect = [
            _files({'A.sublime-settings': 'x2'}),
            _files({'A.sublime-settings': 'x3'}),
        ]
        self.svc._last_synced = {'A.sublime-settings': _h('x')}  # baseline x
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        # Conflict: remote x3 wins (pulled), local x2 backed up, NOT pushed.
        write.assert_called_once_with({'A.sublime-settings': 'x3'},
                                      preserve_packages=True)
        _push.assert_not_called()
        backup.assert_called_once()
        self.assertEqual(backup.call_args.args[0], ['A.sublime-settings'])
        self.assertEqual(self.svc._last_synced['A.sublime-settings'], _h('x3'))

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.delete_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_remote_deletion_propagates_locally(self, _snap, get_files, _fetch,
                                                delete, write, _push, _exists):
        _patch_settings(gist_id='g1')
        get_files.side_effect = [
            _files({'A.sublime-settings': 'x'}),
            _files({}),
        ]
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        delete.assert_called_once_with(['A.sublime-settings'])
        _push.assert_not_called()
        self.assertNotIn('A.sublime-settings', self.svc._last_synced)

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'A.sublime-settings': None}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_unavailable_remote_content_arms_pending_and_adopts_revision(
            self, _snap, get_files, _fetch, write, _push, _exists):
        _patch_settings(gist_id='g1')
        get_files.return_value = _files({'A.sublime-settings': 'x'})
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        write.assert_not_called()
        _push.assert_not_called()
        # Baseline kept so the file is never treated as a remote deletion.
        self.assertIn('A.sublime-settings', self.svc._last_synced)
        # The revision IS adopted now: idle polls must not re-download the
        # whole gist. Instead the failed key gets a bounded retry budget and
        # is re-requested on its own until it recovers (or the gist changes).
        self.assertEqual(self.svc._last_seen_remote, 'r2')
        self.assertEqual(self.svc._pending,
                         {'A.sublime-settings':
                          auto_sync.PENDING_MAX_ATTEMPTS - 1})
        _fetch.assert_called_once_with('r1', only_keys=None)

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists')
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'A.sublime-settings': 'x',
                                          'R.sublime-settings': 'z'}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_local_deletion_during_merge_does_not_raise(self, _snap, get_files,
                                                        _fetch, write, _push,
                                                        exists):
        _patch_settings(gist_id='g1')
        # A is gone from disk; a *different* file R changed remotely, forcing
        # the merge path. Old code raised KeyError on current['A'].
        exists.return_value = False
        get_files.return_value = _files({})
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()  # must not raise
        _push.assert_called_once()
        self.assertEqual(_push.call_args.args[0], {'A.sublime-settings': None})
        self.assertNotIn('A.sublime-settings', self.svc._last_synced)

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists')
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r1', 't', {}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_filtered_out_file_is_not_pushed_as_deletion(self, _snap, get_files,
                                                         _fetch, _push, exists):
        # The file is absent from the collected set (e.g. token filter flipped)
        # but STILL ON DISK: it must never be deleted from the gist.
        _patch_settings(gist_id='g1')
        exists.return_value = True
        get_files.return_value = _files({})
        self.svc._last_synced = {'A.sublime-settings': _h('secret')}
        self.svc._sync_once()
        _push.assert_not_called()
        self.assertIn('A.sublime-settings', self.svc._last_synced)

    @mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=True)
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'PC.sublime-settings': 'remote'}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    def test_merged_pull_result_is_pushed_back(self, _snap, get_files, _fetch,
                                               write, _push, _exists):
        # write_user_files merges installed_packages on disk; the re-scan shows
        # content different from the raw remote, and that union must be pushed.
        _patch_settings(gist_id='g1')
        get_files.side_effect = [
            _files({'PC.sublime-settings': 'local0'}),
            _files({'PC.sublime-settings': 'merged-union'}),
        ]
        self.svc._last_synced = {'PC.sublime-settings': _h('local0')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        _push.assert_called_once()
        self.assertEqual(
            _push.call_args.args[0]['PC.sublime-settings'],
            {'content': 'merged-union'})


class TestPendingRetries(unittest.TestCase):
    KEY = 'A.sublime-settings'
    MAP = {KEY: [KEY]}

    def setUp(self):
        self.svc = auto_sync.AutoSync()
        _patch_settings(gist_id='g1')

        def patch(target, **kw):
            return mock.patch(target, **kw).start()

        self.fetch = patch('sync_settings_reborn.auto_sync._fetch_remote')
        self.get_files = patch(
            'sync_settings_reborn.auto_sync.manager.get_files')
        self.write = patch(
            'sync_settings_reborn.auto_sync.manager.write_user_files')
        patch('sync_settings_reborn.auto_sync._push')
        patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
              return_value=True)
        patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
              return_value=None)

    def tearDown(self):
        mock.patch.stopall()

    def test_unchanged_poll_retries_only_pending_key_and_applies_it(self):
        # Cycle 1: a new revision whose raw content we cannot fetch.
        # Cycle 2: same revision, only the pending key's raw_url is retried
        # and now succeeds.
        self.fetch.side_effect = [
            ('r2', 't', {self.KEY: None}, self.MAP),
            ('r2', 't', {self.KEY: 'z'}, self.MAP),
        ]
        self.get_files.side_effect = [
            _files({self.KEY: 'x'}),   # merge scan
            _files({self.KEY: 'x'}),   # retry pre-write scan
            _files({self.KEY: 'z'}),   # retry post-write re-scan
        ]
        self.svc._last_synced = {self.KEY: _h('x')}
        self.svc._last_seen_remote = 'r1'

        self.svc._sync_once()
        self.assertEqual(self.svc._pending, {self.KEY: 4})
        self.svc._sync_once()

        # Only the pending key was asked for on the unchanged poll, and the
        # recovered content was applied with no whole-gist re-download.
        self.assertEqual(self.fetch.call_args_list[0].args, ('r1',))
        self.assertEqual(self.fetch.call_args_list[0].kwargs,
                         {'only_keys': None})
        self.assertEqual(self.fetch.call_args_list[1].args, ('r2',))
        self.assertEqual(self.fetch.call_args_list[1].kwargs,
                         {'only_keys': {self.KEY}})
        self.write.assert_called_once_with({self.KEY: 'z'},
                                           preserve_packages=True)
        self.assertEqual(self.svc._pending, {})
        self.assertEqual(self.svc._last_synced[self.KEY], _h('z'))
        self.assertEqual(self.svc._last_seen_remote, 'r2')

    def test_permanent_failure_stops_after_budget_and_never_redownloads_rest(
            self):
        # A key that never recovers must spend its budget on targeted retries
        # only; afterwards idle polls ask for nothing and no merge repeats.
        self.fetch.return_value = ('r2', 't', {self.KEY: None}, self.MAP)
        self.get_files.return_value = _files({self.KEY: 'x'})
        self.svc._last_synced = {self.KEY: _h('x')}
        self.svc._last_seen_remote = 'r1'

        # 1 full merge + PENDING_MAX_ATTEMPTS-1 targeted retries exhaust it.
        for _ in range(auto_sync.PENDING_MAX_ATTEMPTS):
            self.svc._sync_once()
        self.assertEqual(self.svc._pending, {})
        self.write.assert_not_called()
        # One more idle poll: pending is empty, nothing is requested.
        self.svc._sync_once()
        self.assertEqual(self.fetch.call_args_list[-1].args, ('r2',))
        self.assertEqual(self.fetch.call_args_list[-1].kwargs,
                         {'only_keys': None})
        self.assertEqual(self.svc._last_seen_remote, 'r2')
        # The last-known baseline copy is retained (never a deletion).
        self.assertEqual(self.svc._last_synced[self.KEY], _h('x'))

    def test_pending_recovery_conflicting_with_local_edit_gist_wins(self):
        backup = mock.patch(
            'sync_settings_reborn.auto_sync._backup_conflicts').start()
        self.fetch.side_effect = [
            ('r2', 't', {self.KEY: None}, self.MAP),
            ('r2', 't', {self.KEY: 'z'}, self.MAP),
        ]
        self.get_files.side_effect = [
            _files({self.KEY: 'x'}),      # merge scan
            _files({self.KEY: 'local'}),  # user edited while content pending
            _files({self.KEY: 'z'}),      # post-pull re-scan
        ]
        self.svc._last_synced = {self.KEY: _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        self.svc._sync_once()
        backup.assert_called_once()
        self.assertEqual(backup.call_args.args[0], [self.KEY])
        self.write.assert_called_once_with({self.KEY: 'z'},
                                           preserve_packages=True)
        self.assertEqual(self.svc._pending, {})


class TestBuildPayload(unittest.TestCase):
    KEY = 'sub%2FC.sublime-settings'
    FOREIGN = 'sub/C.sublime-settings'

    def _call(self, keys, current, name_map, exists=False):
        with mock.patch(
                'sync_settings_reborn.auto_sync.manager.user_file_exists',
                return_value=exists):
            return auto_sync._build_payload(
                keys, current, auto_sync._content_hashes(current),
                {}, name_map)

    def test_foreign_only_file_is_renamed_to_canonical(self):
        current = _files({self.KEY: 'x'})
        payload, baseline = self._call(
            {self.KEY}, current, {self.KEY: [self.FOREIGN]})
        self.assertEqual(payload, {
            self.FOREIGN: {'filename': self.KEY, 'content': 'x'}})
        self.assertEqual(baseline, {self.KEY: _h('x')})

    def test_canonical_and_foreign_twins_update_canonical_drop_foreign(self):
        current = _files({self.KEY: 'x'})
        payload, _ = self._call(
            {self.KEY}, current, {self.KEY: [self.KEY, self.FOREIGN]})
        self.assertEqual(payload, {
            self.KEY: {'content': 'x'}, self.FOREIGN: None})

    def test_deleted_foreign_file_nulls_real_remote_name(self):
        payload, baseline = self._call(
            {self.KEY}, {}, {self.KEY: [self.FOREIGN]})
        self.assertEqual(payload, {self.FOREIGN: None})
        self.assertEqual(baseline, {})

    def test_deleted_file_with_twins_removes_both_names(self):
        payload, _ = self._call(
            {self.KEY}, {}, {self.KEY: [self.KEY, self.FOREIGN]})
        self.assertEqual(payload, {self.KEY: None, self.FOREIGN: None})

    def test_plain_key_keeps_original_form_without_name_map(self):
        current = _files({'A.sublime-settings': 'x'})
        payload, _ = self._call({'A.sublime-settings'}, current, {})
        self.assertEqual(payload, {'A.sublime-settings': {'content': 'x'}})


class TestMissingGist(unittest.TestCase):
    def setUp(self):
        self.svc = auto_sync.AutoSync()
        _patch_settings(gist_id='g1')
        self.dialog = mock.patch.object(auto_sync.sublime,
                                        'message_dialog').start()

    def tearDown(self):
        mock.patch.stopall()

    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                side_effect=NotFoundError('gone'))
    def test_404_dialog_shown_once_and_paused(self, fetch):
        self.svc._sync_once()
        self.svc._sync_once()
        self.assertEqual(self.dialog.call_count, 1)
        # Sync really paused: the dead gist is not polled on the second cycle.
        fetch.assert_called_once_with(None, only_keys=None)
        self.assertEqual(self.svc._missing_gist, 'g1')

    @mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                return_value=None)
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files', return_value={})
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                side_effect=[NotFoundError('gone'), ('r9', 't', {}, {})])
    def test_resumes_after_gist_id_changes(self, fetch, _files_mock, _snap):
        self.svc._sync_once()  # g1 404 -> paused
        _patch_settings(gist_id='g2')
        self.svc._sync_once()  # new id must be tried, pause lifted
        self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(self.svc._missing_gist)


class TestAcquireGistId(unittest.TestCase):
    """The single-flight primitive both `_push` and the Upload command share."""

    def _patch(self, race):
        return (mock.patch('sync_settings_reborn.auto_sync.settings.get',
                           side_effect=race.settings_get),
                mock.patch('sync_settings_reborn.auto_sync.settings.update',
                           side_effect=race.settings_set))

    def _create(self, race):
        # The primitive supplies no payload: the caller's closure owns it.
        return lambda: race.api.create({'files': {'A.sublime-settings': None}})

    def test_returns_configured_id_without_creating(self):
        race = gist_race.SingleFlightRace()
        race.store['gist_id'] = 'g-existing'
        get, update = self._patch(race)
        with get, update:
            gid, created = auto_sync.acquire_gist_id(self._create(race))
        self.assertEqual((gid, created), ('g-existing', None))
        race.api.create.assert_not_called()

    def test_racing_callers_create_exactly_one_gist(self):
        # Both racers read an empty gist_id before either may create, so this is
        # the shape that used to fork two gists and leave an orphan.
        race = gist_race.SingleFlightRace()
        results = []
        get, update = self._patch(race)
        with get, update:
            def one():
                results.append(auto_sync.acquire_gist_id(self._create(race)))
            race.race(one)
        self.assertEqual(len(race.created), 1, 'only one gist should be created')
        self.assertEqual(sorted(gid for gid, _ in results), ['g-new', 'g-new'])
        self.assertEqual(sorted(bool(c is not None) for _, c in results),
                         [False, True],
                         'exactly one caller may report the gist it created')

    def test_lock_is_released_when_it_returns(self):
        # Callers update the gist after this function returns; the create lock
        # must not still be held, and must never be held across adopt().
        race = gist_race.SingleFlightRace()
        get, update = self._patch(race)
        for configured in ('', 'g-existing'):
            race.store['gist_id'] = configured
            with get, update:
                auto_sync.acquire_gist_id(self._create(race))
            self.assertTrue(auto_sync._gist_create_lock.acquire(blocking=False),
                            'acquire_gist_id returned still holding the lock')
            auto_sync._gist_create_lock.release()


class TestPush(unittest.TestCase):
    @mock.patch('sync_settings_reborn.auto_sync.version.update_config_file')
    @mock.patch('sync_settings_reborn.auto_sync.settings.update')
    @mock.patch('sync_settings_reborn.auto_sync.settings.get', return_value='')
    def test_creates_gist_when_no_id(self, _get, update, upd_cfg):
        gist_mock = mock.MagicMock()
        gist_mock.create.return_value = _gist('v1')
        with mock.patch('sync_settings_reborn.auto_sync.Gist.from_settings',
                        return_value=gist_mock):
            g = auto_sync._push({'A.sublime-settings': {'content': 'x'}})
        self.assertEqual(g['history'][0]['version'], 'v1')
        update.assert_called_once_with('gist_id', 'g1')

    def test_no_push_without_files(self):
        self.assertIsNone(auto_sync._push({}))

    def test_deletions_not_sent_on_create(self):
        # A None entry means "delete"; that is invalid while creating a gist.
        with mock.patch('sync_settings_reborn.auto_sync.Gist.from_settings') as api:
            self.assertIsNone(
                auto_sync._push({'A.sublime-settings': None}))
            api.assert_not_called()

    def test_create_is_single_flight(self):
        # Two auto-sync pushes with an empty gist_id — the same race a manual
        # Upload joins — must create one gist; the loser updates the winner's.
        race = gist_race.SingleFlightRace()
        payload = {'A.sublime-settings': {'content': 'x'}}
        with mock.patch('sync_settings_reborn.auto_sync.Gist.from_settings',
                        return_value=race.api), \
                mock.patch('sync_settings_reborn.auto_sync.settings.get',
                           side_effect=race.settings_get), \
                mock.patch('sync_settings_reborn.auto_sync.settings.update',
                           side_effect=race.settings_set):
            race.race(lambda: auto_sync._push(payload))
        self.assertEqual(len(race.created), 1, 'only one gist should be created')
        self.assertEqual(race.store['gist_id'], 'g-new')
        self.assertEqual(race.updated, ['g-new'],
                         'the losing push must update, not create')


class TestInitialPush(unittest.TestCase):
    def setUp(self):
        self.svc = auto_sync.AutoSync()
        _patch_settings(gist_id='')
        mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                   return_value=None).start()
        mock.patch('sync_settings_reborn.auto_sync.version.update_config_file').start()

    def tearDown(self):
        mock.patch.stopall()

    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    @mock.patch('sync_settings_reborn.auto_sync._push', return_value=_gist('v1'))
    def test_first_gist_gets_the_full_mirror(self, push, get_files):
        # Even though the baseline already records A and B (a restart before
        # any gist existed), the first-created gist must contain both files.
        get_files.return_value = {
            'A.sublime-settings': {'content': 'x', 'path': '/a'},
            'B.sublime-settings': {'content': 'y', 'path': '/b'},
        }
        self.svc._last_synced = {'A.sublime-settings': _h('x'),
                                 'B.sublime-settings': _h('y')}
        self.svc._sync_once()
        payload = push.call_args.args[0]
        self.assertEqual(set(payload),
                         {'A.sublime-settings', 'B.sublime-settings'})


class TestOfflineEdits(unittest.TestCase):
    """The common ancestor is persisted in sync.json, so edits made while ST
    was closed survive a restart instead of being seeded as "already synced"."""

    def setUp(self):
        self.svc = auto_sync.AutoSync()
        _patch_settings(gist_id='g1')
        mock.patch('sync_settings_reborn.auto_sync.manager.installed_packages_snapshot',
                   return_value=None).start()
        mock.patch('sync_settings_reborn.auto_sync.manager.user_file_exists',
                   return_value=True).start()
        mock.patch('sync_settings_reborn.auto_sync.version.update_config_file').start()

    def tearDown(self):
        mock.patch.stopall()

    @mock.patch('sync_settings_reborn.auto_sync._push', return_value=_gist('r1-p'))
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r1', 't', {}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    def test_offline_edit_is_pushed(self, get_files, _fetch, push):
        get_files.return_value = _files({'A.sublime-settings': 'x2'})
        # Baseline restored from sync.json after restart says x.
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        self.assertEqual(push.call_args.args[0]['A.sublime-settings'],
                         {'content': 'x2'})

    @mock.patch('sync_settings_reborn.auto_sync._backup_conflicts')
    @mock.patch('sync_settings_reborn.auto_sync._push')
    @mock.patch('sync_settings_reborn.auto_sync.manager.write_user_files')
    @mock.patch('sync_settings_reborn.auto_sync._fetch_remote',
                return_value=('r2', 't', {'A.sublime-settings': 'x3'}, {}))
    @mock.patch('sync_settings_reborn.auto_sync.manager.get_files')
    def test_offline_edit_conflicting_with_remote_is_backed_up(self, get_files,
                                                               _fetch, write,
                                                               push, backup):
        get_files.side_effect = [
            _files({'A.sublime-settings': 'x2'}),
            _files({'A.sublime-settings': 'x3'}),
        ]
        self.svc._last_synced = {'A.sublime-settings': _h('x')}
        self.svc._last_seen_remote = 'r1'
        self.svc._sync_once()
        backup.assert_called_once()
        push.assert_not_called()
        write.assert_called_once()


class TestFetchRemote(unittest.TestCase):
    def _run_fetch(self, urls, http=None, last_rev=None, proxies=None,
                   require=None):
        """Common scaffolding: a listing at revision r2 carrying ``urls``
        ({encoded_name: raw_url}), one Gist client, and an http.request
        stand-in (``http`` may be a callable(url, **kw) or a fixed response).
        Returns ``((rev, committed_at, files, name_map), get_mock)``.
        """
        client = mock.MagicMock()
        client.get.return_value = {
            'history': [{'version': 'r2', 'committed_at': 't'}],
            'files': {name: {'raw_url': url} for name, url in urls.items()},
        }
        if proxies is not None:
            client.proxies = proxies
        settings_p = mock.patch(
            'sync_settings_reborn.auto_sync.settings.get', return_value='g1')
        settings_p.start()
        self.addCleanup(settings_p.stop)

        def _adapt(method, url, **kw):
            if callable(http):
                return http(url, **kw)
            return http

        with mock.patch('sync_settings_reborn.auto_sync.Gist.from_settings',
                        return_value=client), \
                mock.patch('sync_settings_reborn.auto_sync.http.request',
                           side_effect=_adapt) as get_req:
            return auto_sync._fetch_remote(last_rev, only_keys=require), get_req

    def test_fetches_remote_content_via_raw_url(self):
        responses = {'http://a': _resp(200, 'x'),
                     'http://b': _resp(200, 'big-content')}
        # The listing's inline content field is ignored; bytes come from
        # raw_url (including files the inline field would truncate/omit).
        (rev, _, files, names), _ = self._run_fetch(
            {'A.sublime-settings': 'http://a',
             'Big.sublime-settings': 'http://b'},
            http=lambda url, **kw: responses[url])
        self.assertEqual(rev, 'r2')
        self.assertEqual(files, {'A.sublime-settings': 'x',
                                 'Big.sublime-settings': 'big-content'})
        # The listing also yields the canonical -> actual filename map writes
        # need to rename/delete the real remote file.
        self.assertEqual(names, {'A.sublime-settings': ['A.sublime-settings'],
                                 'Big.sublime-settings': ['Big.sublime-settings']})

    def test_raw_fetch_failure_is_skipped_not_deleted(self):
        def _boom(url, **kw):
            raise http_lib.NetworkError('boom')

        (rev, _, files, _), _ = self._run_fetch(
            {'A.sublime-settings': 'http://a'}, http=_boom)
        self.assertEqual(rev, 'r2')
        # Fetch failed: key kept (file exists) but content unavailable.
        self.assertIsNone(files['A.sublime-settings'])

    def test_non_200_raw_fetch_is_none_and_logged(self):
        (_, _, files, _), _ = self._run_fetch(
            {'A.sublime-settings': 'http://a'}, http=_resp(429))
        self.assertIsNone(files['A.sublime-settings'])

    def test_unchanged_revision_skips_all_raw_fetches(self):
        # Regression: an idle poll whose gist revision matches the last one
        # observed must not re-download any file bytes via raw_url.
        (rev, _, files, _), get_req = self._run_fetch(
            {'A.sublime-settings': 'http://a',
             'Big.sublime-settings': 'http://b'}, last_rev='r2')
        self.assertEqual(rev, 'r2')
        self.assertEqual(files, {})
        # Only the one listing call happened; zero per-file raw downloads.
        get_req.assert_not_called()

    def test_unchanged_revision_only_fetches_required_keys(self):
        # The pending-key retry path: on an unchanged revision only the named
        # keys' raw_urls are requested, never the whole gist.
        responses = {'http://a': _resp(200, 'x'),
                     'http://b': _resp(200, 'big')}
        (rev, _, files, _), get_req = self._run_fetch(
            {'A.sublime-settings': 'http://a',
             'Big.sublime-settings': 'http://b'},
            http=lambda url, **kw: responses[url], last_rev='r2',
            require={'A.sublime-settings'})
        self.assertEqual(rev, 'r2')
        self.assertEqual(files, {'A.sublime-settings': 'x'})
        self.assertEqual([c.args[1] for c in get_req.call_args_list],
                         ['http://a'])

    def test_name_map_canonicalises_foreign_filenames(self):
        (_, _, _, names), _ = self._run_fetch(
            {'sub/C.sublime-settings': 'http://c'},
            http=lambda url, **kw: _resp(200, 'x'), last_rev='r1')
        self.assertEqual(names, {'sub%2FC.sublime-settings':
                                 ['sub/C.sublime-settings']})

    def test_changed_revision_uses_gist_client_proxies(self):
        _, get_req = self._run_fetch(
            {'A.sublime-settings': 'http://a'},
            http=lambda url, **kw: _resp(200, 'x'), last_rev='r1',
            proxies={'https': 'http://proxy:3128'})
        self.assertEqual(get_req.call_args.kwargs['proxies'],
                         {'https': 'http://proxy:3128'})

    def test_normalise_canonicalises_external_literal_slash_keys(self):
        # A gist created by another tool stores the file under a literal
        # 'sub/C.sublime-settings'; the internal key must be the encoded form
        # so it matches the local scan (path.encode('sub/C.sublime-settings'))
        # instead of re-syncing every cycle.
        (rev, _, files, _), _ = self._run_fetch(
            {'sub/C.sublime-settings': 'http://c'},
            http=lambda url, **kw: _resp(200, 'x'), last_rev='r1')
        self.assertEqual(files, {'sub%2FC.sublime-settings': 'x'})


class _TempUserDirCase(unittest.TestCase):
    """Temporary Packages/User tree with packages_path() pointed at it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(
            auto_sync.manager.sublime, 'packages_path',
            lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content='{}'):
        target = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w') as f:
            f.write(content)


class TestRestoredBaseline(_TempUserDirCase):
    """adopt_manual_download must hash the locally restored bytes, never
    re-download the gist (no per-file HTTP traffic on manual Download)."""

    def test_hashes_restored_files_without_http(self):
        self._write('A.sublime-settings', 'aaa')
        g = {'files': {
            'A.sublime-settings': {'raw_url': 'http://a'},
            'B.sublime-settings': {'raw_url': 'http://b'},
        }}
        with mock.patch('sync_settings_reborn.auto_sync.http.request') as get_req:
            baseline = auto_sync._restored_baseline(g)
        get_req.assert_not_called()
        self.assertEqual(baseline, {'A.sublime-settings': auto_sync._sha('aaa')})

    def test_subdirectory_and_empty_files(self):
        self._write(os.path.join('sub', 'C.sublime-settings'), 'ccc')
        self._write('Empty.sublime-settings', '')
        g = {'files': {
            # quote-encoded key, as the gist API uses it.
            'sub%2FC.sublime-settings': {'raw_url': 'http://c'},
            'Empty.sublime-settings': {'raw_url': 'http://e'},
        }}
        baseline = auto_sync._restored_baseline(g)
        self.assertEqual(baseline,
                         {'sub%2FC.sublime-settings': auto_sync._sha('ccc')})

    def test_canonicalises_external_literal_slash_keys(self):
        # External gist: literal separator, not percent-encoded. The baseline
        # key must still be the encoded form so the next cycle stays quiet.
        self._write(os.path.join('sub', 'C.sublime-settings'), 'ccc')
        g = {'files': {
            'sub/C.sublime-settings': {'raw_url': 'http://c'},
        }}
        baseline = auto_sync._restored_baseline(g)
        self.assertEqual(baseline, {'sub%2FC.sublime-settings': auto_sync._sha('ccc')})


class TestStatePersistence(unittest.TestCase):
    @mock.patch('sync_settings_reborn.auto_sync.version.update_config_file')
    @mock.patch('sync_settings_reborn.auto_sync.version.get_local_version',
                return_value={'hash': 'r9', 'created_at': 't',
                              'files': {'A.sublime-settings': 'ha'},
                              'pending': {'B.sublime-settings': 2}})
    def test_start_restores_persisted_baseline_and_pending(self, get_ver, update):
        svc = auto_sync.AutoSync()
        with mock.patch('sync_settings_reborn.auto_sync.settings.get',
                        return_value=None):
            svc.start()
        try:
            self.assertEqual(svc._last_seen_remote, 'r9')
            self.assertEqual(svc._last_synced, {'A.sublime-settings': 'ha'})
            self.assertEqual(svc._pending, {'B.sublime-settings': 2})
        finally:
            svc.stop()

    @mock.patch('sync_settings_reborn.auto_sync.version.get_local_version',
                return_value={'hash': 'r9', 'files': {'A': 'ha'},
                              'pending': {'ok': 3, 'zero': 0, 'bad': 'x'}})
    def test_load_state_validates_pending_entries(self, _get):
        rev, files, pending = auto_sync._load_state()
        self.assertEqual((rev, files), ('r9', {'A': 'ha'}))
        self.assertEqual(pending, {'ok': 3})

    @mock.patch('sync_settings_reborn.auto_sync.version.get_local_version',
                return_value=None)
    def test_load_state_without_state_file(self, _get):
        self.assertEqual(auto_sync._load_state(), (None, {}, {}))

    @mock.patch('sync_settings_reborn.auto_sync.version.update_config_file')
    def test_persist_includes_files_baseline_and_pending(self, update):
        svc = auto_sync.AutoSync()
        svc._last_seen_remote = 'r3'
        svc._last_committed_at = 'tt'
        svc._last_synced = {'A': 'h'}
        svc._pending = {'B': 3}
        svc._persist_state()
        update.assert_called_once_with({
            'hash': 'r3', 'created_at': 'tt',
            'files': {'A': 'h'}, 'pending': {'B': 3}})


class TestStartStop(unittest.TestCase):
    def test_start_is_idempotent(self):
        with mock.patch('sync_settings_reborn.auto_sync.settings.get',
                        return_value=None):
            svc = auto_sync.AutoSync()
            svc._interval = 0.01
            svc.start()
            first = svc._thread
            svc.start()
            self.assertIs(first, svc._thread)
            svc.stop()


class TestUserFileGuards(_TempUserDirCase):
    def test_exists_detects_real_file(self):
        name = 'A.sublime-settings'
        with open(os.path.join(self.user, name), 'w') as f:
            f.write('x')
        self.assertTrue(auto_sync.manager.user_file_exists(name))
        self.assertFalse(auto_sync.manager.user_file_exists('Missing.sublime-settings'))

    def test_delete_removes_file_and_rejects_traversal(self):
        name = 'A.sublime-settings'
        with open(os.path.join(self.user, name), 'w') as f:
            f.write('x')
        outside = os.path.join(self.tmp, 'outside.txt')
        with open(outside, 'w') as f:
            f.write('keep')
        auto_sync.manager.delete_user_files([name, '..%2Foutside.txt'])
        self.assertFalse(os.path.exists(os.path.join(self.user, name)))
        self.assertTrue(os.path.exists(outside))


if __name__ == '__main__':
    unittest.main()
