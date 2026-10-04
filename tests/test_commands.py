# -*- coding: utf-8 -*-

import os
import shutil
import tempfile
import mock
import unittest

from .mocks import gist_race

from sync_settings_reborn import backup
from sync_settings_reborn import sync_manager as manager
from sync_settings_reborn.commands import sync_online
from sync_settings_reborn.commands import download
from sync_settings_reborn.commands import upload
from sync_settings_reborn import sync_version as version
from sync_settings_reborn.libs import gist
from sync_settings_reborn.libs import settings


class BackupHelpersTest(unittest.TestCase):
    def test_default_backup_path_falls_back(self):
        with mock.patch.object(backup.settings, 'get', return_value=None):
            self.assertEqual(
                backup.default_backup_path(),
                os.path.join(os.path.expanduser('~'), 'SyncSettingsReborn.zip'),
            )

    def test_default_backup_path_uses_setting(self):
        with mock.patch.object(backup.settings, 'get', return_value='/tmp/x.zip'):
            self.assertEqual(backup.default_backup_path(), '/tmp/x.zip')

    def test_packages_backup_does_not_collide_with_full(self):
        # Regression: both commands used to resolve to ~/SyncSettingsReborn.zip,
        # so `Backup Package List` silently overwrote a full backup.
        with mock.patch.object(backup.settings, 'get', return_value=None):
            full = backup.default_backup_path()
            packages = backup.default_backup_path(packages_only=True)
        self.assertNotEqual(full, packages)
        self.assertEqual(packages, os.path.join(os.path.expanduser('~'), 'SyncSettingsReborn-packages.zip'))

    def test_packages_backup_derives_from_configured_path(self):
        with mock.patch.object(backup.settings, 'get', return_value='/tmp/x.zip'):
            self.assertEqual(
                backup.default_backup_path(packages_only=True),
                '/tmp/x-packages.zip',
            )

    def test_packages_backup_adds_extension_when_missing(self):
        with mock.patch.object(backup.settings, 'get', return_value='/tmp/backup'):
            self.assertEqual(
                backup.default_backup_path(packages_only=True),
                '/tmp/backup-packages.zip',
            )

    def test_preserve_packages_default_true(self):
        with mock.patch.object(backup.settings, 'get', return_value=None):
            self.assertTrue(backup.preserve_packages())

    def test_preserve_packages_explicit(self):
        with mock.patch.object(backup.settings, 'get', return_value=False):
            self.assertFalse(backup.preserve_packages())


class SyncOnlinePullTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pull_missing_file_reports_error(self):
        cmd = sync_online.SyncSettingsRebornSyncOnlinePullCommand()
        missing = os.path.join(self.tmp, 'does-not-exist.zip')
        # report_error uses the stubbed sublime.message_dialog, so calling it is safe.
        cmd._pull(missing)
        self.assertTrue(cmd._failed)


class MoveFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_move_files_writes_all(self):
        origin = tempfile.mkdtemp()
        try:
            with open(os.path.join(origin, 'A.sublime-settings'), 'w') as f:
                f.write('{"a": 1}')
            with open(os.path.join(origin, 'B.sublime-settings'), 'w') as f:
                f.write('{"b": 2}')
            manager.move_files(origin)
            self.assertTrue(os.path.exists(os.path.join(self.user, 'A.sublime-settings')))
            self.assertTrue(os.path.exists(os.path.join(self.user, 'B.sublime-settings')))
        finally:
            shutil.rmtree(origin, ignore_errors=True)


class IgnoreDirsMatchesDirectoriesOnlyTest(unittest.TestCase):
    """Regression: a file named like an ignored dir must not be excluded."""

    @mock.patch.object(
        manager.settings, 'get',
        mock.MagicMock(side_effect=lambda k: ['node_modules'] if k == 'ignore_dirs' else None),
    )
    def test_file_named_like_ignored_dir_kept(self):
        self.assertFalse(manager.should_exclude('/Packages/User/node_modules'))

    @mock.patch.object(
        manager.settings, 'get',
        mock.MagicMock(side_effect=lambda k: ['node_modules'] if k == 'ignore_dirs' else None),
    )
    def test_dir_named_like_ignored_dir_excluded(self):
        self.assertTrue(manager.should_exclude('/Packages/User/node_modules/foo.py'))


class ExcludedFilesBasenameTest(unittest.TestCase):
    """Regression: a bare pattern (no `*`) should match the file basename."""

    @mock.patch.object(
        manager.settings, 'get',
        mock.MagicMock(side_effect=lambda k: ['secret.txt'] if k == 'excluded_files' else None),
    )
    def test_bare_pattern_matches_basename(self):
        self.assertTrue(manager.should_exclude('/Packages/User/sub/secret.txt'))


class InstalledPackagesHelpersTest(unittest.TestCase):
    """Guard the public helper API so a name mismatch (private vs public) can
    never silently break the Download package-install step again."""

    def test_from_content_parses(self):
        self.assertEqual(
            manager.installed_packages_from_content('{"installed_packages": ["A", "B"]}'),
            ['A', 'B'],
        )

    def test_from_content_empty(self):
        self.assertEqual(manager.installed_packages_from_content(''), [])

    def test_from_content_non_dict(self):
        self.assertEqual(manager.installed_packages_from_content('[1, 2]'), [])

    def test_from_content_non_list_value(self):
        self.assertEqual(manager.installed_packages_from_content('{"installed_packages": "x"}'), [])

    def test_local_returns_list(self):
        self.assertIsInstance(manager.local_installed_packages(), list)


class DownloadCommandTest(unittest.TestCase):
    """Exercises the Download package-install path end to end with the network
    and Gist stubbed. The install step delegates to the shared
    manager.install_missing_packages helper (covered directly in
    test_sync_manager)."""

    def setUp(self):
        self.cmd = download.SyncSettingsRebornDownloadCommand()
        self.cmd.window = mock.MagicMock()
        self.cmd._failed = False

    def test_download_restores_then_installs(self):
        fake_gist = {
            'id': 'g1',
            'files': {'x.sublime-settings': {'raw_url': 'http://u'}},
            'history': [{'version': 'v', 'committed_at': 't'}],
        }
        with mock.patch.object(download.Gist, 'get', return_value=fake_gist), \
                mock.patch.object(manager, 'fetch_files') as m_fetch, \
                mock.patch.object(manager, 'get_content', return_value='{"installed_packages": ["C"]}'), \
                mock.patch.object(manager, 'installed_packages_from_content', return_value=['C']), \
                mock.patch.object(manager, 'install_missing_packages') as m_install, \
                mock.patch.object(manager, 'move_files') as m_move:
            self.cmd.download()
        m_fetch.assert_called_once()
        m_move.assert_called_once()
        # Restore happens before the best-effort install request.
        m_install.assert_called_once_with(['C'])


class BackupSkipUninstalledTest(unittest.TestCase):
    """The zip backup path must honour skip_uninstalled_packages just like the
    Gist path; it used to ignore the option entirely."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(backup.manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel):
        with open(os.path.join(self.user, rel), 'w') as f:
            f.write('{}')

    def test_leftovers_excluded(self):
        for rel in ('PackageSync.sublime-settings', 'LSP.sublime-settings', 'Preferences.sublime-settings'):
            self._write(rel)
        result = backup.collect_files(installed={'LSP'})
        self.assertNotIn(os.path.join('PackageSync.sublime-settings'), result)
        self.assertIn(os.path.join('LSP.sublime-settings'), result)
        self.assertIn(os.path.join('Preferences.sublime-settings'), result)

    def test_kept_when_no_snapshot(self):
        self._write('PackageSync.sublime-settings')
        result = backup.collect_files(installed=None)
        self.assertIn(os.path.join('PackageSync.sublime-settings'), result)


class UploadCommandTest(unittest.TestCase):
    """Guard the redesign: Upload must create when there is no gist_id and
    update when there is one, and must backfill gist_id after creating."""

    def setUp(self):
        self.cmd = upload.SyncSettingsRebornUploadCommand()
        self.cmd.window = mock.MagicMock()
        self.cmd._failed = False

    def test_upload_creates_when_no_gist_id(self):
        fake_gist = {
            'id': 'new-gist',
            'history': [{'version': 'v', 'committed_at': 't'}],
        }
        with mock.patch.object(manager, 'get_files', return_value={'a.sublime-settings': {'content': '{}'}}), \
                mock.patch.object(gist.Gist, 'create', return_value=fake_gist) as m_create, \
                mock.patch.object(gist.Gist, 'update') as m_update, \
                mock.patch.object(settings, 'get', side_effect=lambda k: '' if k == 'gist_id' else 'tok'), \
                mock.patch.object(settings, 'update') as m_set, \
                mock.patch.object(version, 'update_config_file') as m_ver:
            self.cmd.upload()
        m_create.assert_called_once()
        m_update.assert_not_called()
        m_set.assert_called_with('gist_id', 'new-gist')
        # Once for gist metadata, once to persist auto-sync's adopted baseline.
        self.assertEqual(m_ver.call_count, 2)
        self.assertEqual(m_ver.call_args_list[0][0][0],
                         {'hash': 'v', 'created_at': 't'})
        self.assertEqual(m_ver.call_args_list[1][0][0]['files'],
                         {'a.sublime-settings': mock.ANY})

    def test_upload_updates_when_gist_id_present(self):
        fake_gist = {
            'id': 'g1',
            'history': [{'version': 'v', 'committed_at': 't'}],
        }
        with mock.patch.object(manager, 'get_files', return_value={'a.sublime-settings': {'content': '{}'}}), \
                mock.patch.object(gist.Gist, 'update', return_value=fake_gist) as m_update, \
                mock.patch.object(gist.Gist, 'create') as m_create, \
                mock.patch.object(settings, 'get', side_effect=lambda k: 'g1' if k == 'gist_id' else 'tok'):
            self.cmd.upload()
        m_update.assert_called_once_with('g1', data=mock.ANY)
        m_create.assert_not_called()

    def test_upload_single_flight_against_concurrent_create(self):
        # Two Uploads with an empty gist_id (the same race an auto-sync cycle
        # joins) must create one gist; the loser must update the winner's.
        race = gist_race.SingleFlightRace()
        with mock.patch.object(manager, 'get_files',
                               return_value={'a.sublime-settings': {'content': '{}'}}), \
                mock.patch.object(gist.Gist, 'from_settings', return_value=race.api), \
                mock.patch.object(settings, 'get', side_effect=race.settings_get), \
                mock.patch.object(settings, 'update', side_effect=race.settings_set), \
                mock.patch.object(version, 'update_config_file'), \
                mock.patch.object(upload.auto_sync, 'adopt_manual_upload'):
            race.race(self.cmd.upload)
        self.assertEqual(len(race.created), 1, 'only one gist should be created')
        self.assertEqual(race.store['gist_id'], 'g-new')
        self.assertEqual(race.updated, ['g-new'],
                         'the second upload must update, not create')

    def test_upload_forwards_main_thread_snapshot(self):
        # run() takes the list_packages() snapshot on the main thread; the worker
        # must receive it instead of calling list_packages() itself.
        with mock.patch.object(manager, 'get_files', return_value={}) as m_get, \
                mock.patch.object(settings, 'get', return_value='tok'):
            self.cmd.upload(installed={'LSP'})
        m_get.assert_called_once_with(installed={'LSP'})

    def _upload_with_baseline(self, remote_names, baseline, file_exists,
                              local_files=None):
        """Run Upload against a stubbed gist and return the files map actually
        sent on PATCH. Local upload set defaults to a.sublime-settings."""
        if local_files is None:
            local_files = {'a.sublime-settings': {'content': '{}'}}
        api = mock.MagicMock()
        api.get.return_value = {
            'files': {name: {'content': '{}'} for name in remote_names},
        }
        api.update.return_value = {
            'id': 'g1', 'history': [{'version': 'v', 'committed_at': 't'}]}
        with mock.patch.object(
                manager, 'get_files',
                return_value=local_files), \
                mock.patch.object(manager, 'user_file_exists',
                                  return_value=file_exists), \
                mock.patch.object(gist.Gist, 'from_settings', return_value=api), \
                mock.patch.object(
                    settings, 'get',
                    side_effect=lambda k: 'g1' if k == 'gist_id' else 'tok'), \
                mock.patch.object(version, 'get_local_version',
                                  return_value={'hash': 'r', 'files': baseline}), \
                mock.patch.object(version, 'update_config_file'):
            self.cmd.upload()
        return api.update.call_args.kwargs['data']['files']

    def test_upload_deletes_remote_absent_files(self):
        # A file this machine previously synced (present in the persisted
        # baseline) but has since deleted from disk must be deleted (null).
        sent = self._upload_with_baseline(
            ['a.sublime-settings', 'b.sublime-settings'],
            {'a.sublime-settings': 'ha', 'b.sublime-settings': 'hb'},
            file_exists=False)
        self.assertEqual(sent['a.sublime-settings'], {'content': '{}'})
        self.assertIsNone(sent['b.sublime-settings'])

    def test_upload_keeps_unknown_remote_files(self):
        # Regression (cross-machine data loss): a remote file this machine has
        # never synced (absent from its baseline — a fresh install or an Upload
        # before the first Download) must NOT be deleted, even though it is
        # missing on this machine's disk.
        sent = self._upload_with_baseline(
            ['a.sublime-settings', 'other-machine.sublime-settings'],
            {'a.sublime-settings': 'ha'},
            file_exists=False)
        self.assertNotIn('other-machine.sublime-settings', sent)

    def test_upload_without_baseline_deletes_nothing(self):
        # No persisted baseline (manual-only user, or a freshly configured
        # gist_id): deletions cannot be attributed to this machine, so the
        # gist must only be appended to — nothing is nulled.
        sent = self._upload_with_baseline(
            ['a.sublime-settings', 'b.sublime-settings'], {}, file_exists=False)
        self.assertNotIn('b.sublime-settings', sent)

    def test_upload_keeps_filtered_remote_files(self):
        # A remote file that exists locally but is excluded from the upload set
        # (e.g. filtered out) must NOT be deleted from the gist.
        sent = self._upload_with_baseline(
            ['a.sublime-settings', 'c.sublime-settings'],
            {'a.sublime-settings': 'ha', 'c.sublime-settings': 'hc'},
            file_exists=True)
        self.assertNotIn('c.sublime-settings', sent)

    def test_upload_renames_foreign_literal_name_to_canonical(self):
        # Regression: another tool stored 'sub/C.sublime-settings' (literal
        # separator); an upload of the encoded local key must RENAME the gist
        # file in place, not create a second '%2F' twin.
        key = 'sub%2FC.sublime-settings'
        sent = self._upload_with_baseline(
            ['sub/C.sublime-settings'], {key: 'hc'}, file_exists=True,
            local_files={key: {'content': '{}'}})
        self.assertEqual(sent, {
            'sub/C.sublime-settings': {'filename': key, 'content': '{}'}})

    def test_upload_deleting_foreign_file_nulls_literal_remote_name(self):
        # The locally deleted foreign file must null the name the gist really
        # carries; nulling only the encoded key would miss it and the file
        # would be resurrected by the next background merge.
        key = 'sub%2FC.sublime-settings'
        sent = self._upload_with_baseline(
            ['a.sublime-settings', 'sub/C.sublime-settings'],
            {'a.sublime-settings': 'ha', key: 'hc'}, file_exists=False)
        self.assertEqual(sent['a.sublime-settings'], {'content': '{}'})
        self.assertIsNone(sent['sub/C.sublime-settings'])
        self.assertNotIn(key, sent)


if __name__ == '__main__':
    unittest.main()
