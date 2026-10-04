# -*- coding: utf-8 -*-

import unittest
import mock
import os
import json
import tempfile
import shutil
from sync_settings_reborn import sync_manager as manager


def create_file(file, mode='w', content=None):
    delete_file(file)
    with open(file, mode) as fi:
        if content:
            fi.write(content)


def delete_file(file):
    if os.path.isfile(file):
        os.unlink(file)


class TestSyncManager(unittest.TestCase):
    @mock.patch('sync_settings_reborn.libs.settings.get', mock.MagicMock(return_value=[
        '*Package Control.sublime-settings',
        '*.txt',
        'foo/**/*.py',
        'bar/*.py',
        'bar/*.md',
    ]))
    def test_should_exclude(self):
        tests = [
            {'file': '/usr/bin/conf/default.conf', 'expected': False},
            {'file': '/foo/bar/script.py', 'expected': False},
            {'file': 'file.js', 'expected': False},
            {'file': 'Theme.tmTheme', 'expected': False},
            {'file': 'foo/bar/file.py', 'expected': True},
            {'file': 'bar/README.md', 'expected': True},
            {'file': '/a/long/path/file.txt', 'expected': True},
            {'file': '/User/Settings/Package Control.sublime-settings', 'expected': True},
            # No file name is special-cased any more; a config file is judged
            # only by the exclude/include patterns (and, for tokens, by content).
            {'file': '/User/Settings/SyncSettingsReborn.sublime-settings', 'expected': False},
        ]

        for test in tests:
            self.assertEqual(
                test['expected'],
                manager.should_exclude(test['file']),
                'comparing: {}'.format(test['file'])
            )

    @mock.patch('sync_settings_reborn.libs.settings.get', mock.MagicMock(return_value='*.txt'))
    def test_should_exclude_with_string_setting(self):
        # regression for #200: a string setting must not crash and is
        # treated as a single pattern
        self.assertTrue(manager.should_exclude('foo.txt'))
        self.assertFalse(manager.should_exclude('foo.py'))

    @mock.patch('sync_settings_reborn.libs.settings.get', mock.MagicMock(return_value='*.txt'))
    def test_should_include_with_string_setting(self):
        # regression for #200: a string setting must not crash and is
        # treated as a single pattern
        self.assertTrue(manager.should_include('foo.txt'))
        self.assertFalse(manager.should_include('foo.py'))

    @mock.patch('sync_settings_reborn.libs.settings.get', mock.MagicMock(return_value=[
        '*.sublime-settings',
        '*.txt',
    ]))
    def test_should_include(self):
        tests = [
            {'file': '/usr/bin/conf/default.conf', 'expected': False},
            {'file': '', 'expected': False},
            {'file': '/foo/bar/script.py', 'expected': False},
            {'file': 'file.js', 'expected': False},
            {'file': 'Theme.tmTheme', 'expected': False},
            {'file': 'foo/bar/file.py', 'expected': False},
            {'file': 'bar/README.md', 'expected': False},
            {'file': '/a/long/path/file.txt', 'expected': True},
            {'file': '/User/Settings/Package Control.sublime-settings', 'expected': True},
            {'file': '/User/Settings/SyncSettingsReborn.sublime-settings', 'expected': True},
        ]

        for test in tests:
            self.assertEqual(
                test['expected'],
                manager.should_include(test['file']),
                'comparing: {}'.format(test['file'])
            )

    def test_get_content(self):
        create_file('empty.txt')
        create_file('plain.txt', content='content')

        tests = [
            {'file': 'empty.txt', 'expected': ''},
            {'file': 'not-found.txt', 'expected': ''},
            {'file': 'plain.txt', 'expected': 'content'},
        ]

        for test in tests:
            self.assertEqual(manager.get_content(test['file']), test['expected'])

        delete_file('empty.txt')
        delete_file('plain.txt')

    @mock.patch('sync_settings_reborn.sync_manager.path.exists', mock.MagicMock(return_value=True))
    def test_get_content_with_exception(self):
        self.assertEqual(manager.get_content('file.error'), '')

    def test_get_content_skips_binary_file(self):
        # A non-UTF-8 file must be skipped (return ''), not uploaded corrupted.
        create_file('binary.bin', content=b'\x86\x00\x87', mode='wb')
        self.assertEqual(manager.get_content('binary.bin'), '')
        delete_file('binary.bin')


def _settings_side_effect(ignore_dirs, excluded_files=None):
    table = {'ignore_dirs': ignore_dirs, 'excluded_files': excluded_files}
    return mock.MagicMock(side_effect=lambda key: table.get(key))


class ShouldExcludeDirsTest(unittest.TestCase):

    @mock.patch('sync_settings_reborn.sync_manager.settings.get',
                _settings_side_effect(['IgnoredDir'], None))
    def test_ignore_dirs_excludes_nested_file(self):
        self.assertTrue(manager.should_exclude('/Packages/User/IgnoredDir/foo.py'))

    @mock.patch('sync_settings_reborn.sync_manager.settings.get',
                _settings_side_effect(['IgnoredDir'], None))
    def test_ignore_dirs_keeps_other_dirs(self):
        self.assertFalse(manager.should_exclude('/Packages/User/Kept/foo.py'))

    @mock.patch('sync_settings_reborn.sync_manager.settings.get',
                _settings_side_effect(['*Cache*'], None))
    def test_ignore_dirs_with_wildcard(self):
        self.assertTrue(manager.should_exclude('/Packages/User/Foo/Cache/bar.py'))


class WriteUserFilesTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _read_pc(self):
        with open(os.path.join(self.user, 'Package Control.sublime-settings')) as f:
            return json.load(f)

    def test_write_user_files_preserves_local_packages(self):
        # incoming only has [A, B]; local already has [B, C]
        local_settings = mock.MagicMock()
        local_settings.get.return_value = ['B', 'C']
        with mock.patch.object(manager.sublime, 'load_settings', return_value=local_settings):
            manager.write_user_files(
                {'Package%20Control.sublime-settings': json.dumps(
                    {'installed_packages': ['A', 'B']}).encode()},
                preserve_packages=True,
            )
        self.assertEqual(self._read_pc()['installed_packages'], ['A', 'B', 'C'])

    def test_write_user_files_no_preserve_overwrites(self):
        local_settings = mock.MagicMock()
        local_settings.get.return_value = ['B', 'C']
        with mock.patch.object(manager.sublime, 'load_settings', return_value=local_settings):
            manager.write_user_files(
                {'Package%20Control.sublime-settings': json.dumps(
                    {'installed_packages': ['A', 'B']}).encode()},
                preserve_packages=False,
            )
        # overwrite: only the incoming list remains
        self.assertEqual(self._read_pc()['installed_packages'], ['A', 'B'])

    def test_write_user_files_writes_plain_file(self):
        manager.write_user_files({'Preferences.sublime-settings': b'{"x": 1}'}, preserve_packages=True)
        with open(os.path.join(self.user, 'Preferences.sublime-settings')) as f:
            self.assertEqual(json.load(f), {'x': 1})

    def test_write_user_files_local_packages_none(self):
        local_settings = mock.MagicMock()
        local_settings.get.return_value = None
        with mock.patch.object(manager.sublime, 'load_settings', return_value=local_settings):
            manager.write_user_files(
                {'Package%20Control.sublime-settings': json.dumps(
                    {'installed_packages': ['A', 'B']}).encode()},
                preserve_packages=True,
            )
        self.assertEqual(self._read_pc()['installed_packages'], ['A', 'B'])

    def test_write_user_files_local_packages_non_list(self):
        local_settings = mock.MagicMock()
        local_settings.get.return_value = 'not a list'
        with mock.patch.object(manager.sublime, 'load_settings', return_value=local_settings):
            manager.write_user_files(
                {'Package%20Control.sublime-settings': json.dumps(
                    {'installed_packages': ['A', 'B']}).encode()},
                preserve_packages=True,
            )
        self.assertEqual(self._read_pc()['installed_packages'], ['A', 'B'])


class FetchMoveRegressionTest(unittest.TestCase):
    """Regression tests for the Download temp-folder crash (sync.log showed
    FileNotFoundError on the temp dir in move_files)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_files_recreates_leftover_dir(self):
        # Recreated (not merely removed) so move_files can read it afterwards.
        target = os.path.join(self.tmp, 'temp')
        os.makedirs(target)
        manager.fetch_files({}, to=target)
        self.assertTrue(os.path.isdir(target))

    def test_fetch_files_idempotent(self):
        target = os.path.join(self.tmp, 'temp')
        manager.fetch_files({}, to=target)
        manager.fetch_files({}, to=target)
        self.assertTrue(os.path.isdir(target))

    def test_move_files_missing_dir_is_noop(self):
        # A missing temp dir must not raise; it used to crash with
        # FileNotFoundError inside os.listdir.
        manager.move_files(os.path.join(self.tmp, 'does-not-exist'))


class SkipUninstalledPackagesTest(unittest.TestCase):
    """Verify skip_uninstalled_packages drops leftovers of removed plugins."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, content='{}'):
        full = os.path.join(self.user, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def _run(self, installed, skip=True):
        rels = [
            'SyncSettings.sublime-settings',
            'PackageSync.sublime-settings',
            'LSP.sublime-settings',
            'Preferences.sublime-settings',
            'MyNotes.txt',
            'LSP.sublime-keymap',
            'SyncSettings.sublime-keymap',
            os.path.join('SyncSettings', 'data.json'),
            os.path.join('Terminus', 'state.json'),
        ]
        for rel in rels:
            self._write(rel)
        full_paths = [os.path.join(self.user, rel) for rel in rels]

        def _get(key):
            return skip if key == 'skip_uninstalled_packages' else None

        with mock.patch.object(manager.path, 'list_files', return_value=full_paths), \
                mock.patch.object(manager, 'get_content', return_value='{"x":1}'), \
                mock.patch.object(manager.settings, 'get', _get), \
                mock.patch.object(manager.sublime, 'list_packages', return_value=installed):
            return manager.get_files()

    def test_skips_uninstalled_leftovers(self):
        enc = manager.path.encode
        # Only LSP and Terminus are installed; the rest are removed-plugin leftovers.
        files = self._run(installed=['LSP', 'Terminus'])
        keys = set(files.keys())
        # uninstalled package leftovers are skipped (settings, keymap, data dir)
        self.assertNotIn(enc('SyncSettings.sublime-settings'), keys)
        self.assertNotIn(enc('PackageSync.sublime-settings'), keys)
        self.assertNotIn(enc('SyncSettings.sublime-keymap'), keys)
        self.assertNotIn(enc(os.path.join('SyncSettings', 'data.json')), keys)
        # installed package (LSP) and global files are kept
        self.assertIn(enc('LSP.sublime-settings'), keys)
        self.assertIn(enc('LSP.sublime-keymap'), keys)
        self.assertIn(enc('Preferences.sublime-settings'), keys)
        self.assertIn(enc('MyNotes.txt'), keys)
        self.assertIn(enc(os.path.join('Terminus', 'state.json')), keys)

    def test_disabled_keeps_everything(self):
        enc = manager.path.encode
        files = self._run(installed=['LSP', 'Terminus'], skip=False)
        keys = set(files.keys())
        self.assertIn(enc('SyncSettings.sublime-settings'), keys)
        self.assertIn(enc('PackageSync.sublime-settings'), keys)
        self.assertIn(enc('SyncSettings.sublime-keymap'), keys)
        self.assertIn(enc(os.path.join('SyncSettings', 'data.json')), keys)


class InstalledSnapshotTest(unittest.TestCase):
    """skip_uninstalled_packages must survive being used from a worker thread:
    the snapshot is taken on the main thread and passed down, and a failure
    degrades to "do not filter" rather than to a bogus empty package list."""

    def _opt_in(self):
        return mock.patch.object(manager.settings, 'get', return_value=True)

    def _opt_out(self):
        return mock.patch.object(manager.settings, 'get', return_value=None)

    def test_returns_none_when_option_off(self):
        with self._opt_out(), \
                mock.patch.object(manager, '_packages_on_disk', return_value={'A'}):
            self.assertIsNone(manager.installed_packages_snapshot())

    def test_union_of_all_sources(self):
        # PC's list misses manual installs, the filesystem sees them, and
        # list_packages() is simply absent on some builds.
        with self._opt_in(), \
                mock.patch.object(manager, 'local_installed_packages', return_value=['FromPC']), \
                mock.patch.object(manager, '_packages_on_disk', return_value={'FromDisk'}), \
                mock.patch.object(manager.sublime, 'list_packages', return_value=['FromApi']):
            self.assertEqual(
                manager.installed_packages_snapshot(),
                {'FromPC', 'FromDisk', 'FromApi'},
            )

    def test_works_without_list_packages_api(self):
        # Sublime build 4215 exposes no sublime.list_packages; the other sources
        # must be enough on their own.
        with self._opt_in(), \
                mock.patch.object(manager, 'local_installed_packages', return_value=['PC']), \
                mock.patch.object(manager, '_packages_on_disk', return_value={'Disk'}):
            with mock.patch.object(manager.sublime, 'list_packages', create=False):
                del manager.sublime.list_packages
                try:
                    snapshot = manager.installed_packages_snapshot()
                finally:
                    manager.sublime.list_packages = lambda: []
        self.assertEqual(snapshot, {'PC', 'Disk'})

    def test_resolve_reuses_supplied_snapshot(self):
        with self._opt_in(), \
                mock.patch.object(manager, 'local_installed_packages', side_effect=AssertionError('no call')), \
                mock.patch.object(manager, '_packages_on_disk', side_effect=AssertionError('no call')):
            self.assertEqual(manager.resolve_installed({'A'}), {'A'})

    def test_returns_none_when_every_source_empty(self):
        # Never hand back an empty set: that would make every package-scoped
        # file look uninstalled.
        with self._opt_in(), \
                mock.patch.object(manager, 'local_installed_packages', return_value=[]), \
                mock.patch.object(manager, '_packages_on_disk', return_value=set()), \
                mock.patch.object(manager.sublime, 'list_packages', return_value=[]):
            self.assertIsNone(manager.installed_packages_snapshot())

    def test_empty_snapshot_keeps_everything(self):
        # An empty set must not make every package-scoped file look uninstalled.
        self.assertFalse(manager.should_skip_uninstalled('LSP.sublime-settings', set()))
        self.assertFalse(manager.should_skip_uninstalled('LSP.sublime-settings', None))

    def test_should_skip_uninstalled_basics(self):
        installed = {'LSP'}
        self.assertTrue(manager.should_skip_uninstalled('PackageSync.sublime-settings', installed))
        self.assertFalse(manager.should_skip_uninstalled('LSP.sublime-settings', installed))
        self.assertFalse(manager.should_skip_uninstalled('Preferences.sublime-settings', installed))


class PackagesOnDiskTest(unittest.TestCase):
    """The filesystem source must find both Package Control installs and
    manually placed ones (a symlinked checkout), and ignore `User`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.installed = os.path.join(self.tmp, 'Installed Packages')
        self.packages = os.path.join(self.tmp, 'Packages')
        os.makedirs(self.installed)
        os.makedirs(self.packages)
        os.makedirs(os.path.join(self.packages, 'User'))
        os.makedirs(os.path.join(self.packages, 'Symlinked'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _names(self):
        with mock.patch.object(manager.sublime, 'installed_packages_path', lambda: self.installed), \
                mock.patch.object(manager.sublime, 'packages_path', lambda: self.packages):
            return manager._packages_on_disk()

    def test_finds_both_kinds(self):
        with open(os.path.join(self.installed, 'FromControl.sublime-package'), 'w') as f:
            f.write('')
        names = self._names()
        self.assertIn('FromControl', names)   # Installed Packages/*.sublime-package
        self.assertIn('Symlinked', names)     # Packages/<Name>/ (manual/symlink)
        self.assertNotIn('User', names)       # not a package

    def test_unreadable_dirs_are_tolerated(self):
        with mock.patch.object(manager.sublime, 'installed_packages_path', side_effect=OSError), \
                mock.patch.object(manager.sublime, 'packages_path', lambda: self.packages):
            self.assertEqual(manager._packages_on_disk(), {'Symlinked'})


class ContainsGithubTokenTest(unittest.TestCase):
    """Only GitHub's own token prefixes are treated as secrets, and the match is
    narrow enough that ordinary settings are never flagged."""

    def test_detects_every_github_prefix(self):
        for prefix in ('ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_'):
            self.assertTrue(
                manager.contains_github_token(
                    '{{"access_token": "{}a1b2c3d4e5f6g7h8i9j0k1l2"}}'.format(prefix)
                ),
                prefix,
            )

    def test_detects_fine_grained_token(self):
        self.assertTrue(manager.contains_github_token('"github_pat_11ABCDEFG0a1b2c3d4e5f6g7h8i9j0k1l2m3n"'))

    def test_detects_token_without_json_wrapper(self):
        self.assertTrue(manager.contains_github_token('ghp_' + 'a' * 36))

    def test_ignores_ordinary_settings(self):
        for content in (
            '{"token_color_scheme": "Solarized"}',
            '{"access_token": ""}',
            '{"access_token": "<your token here>"}',
            '{"font_size": 12, "tab_size": 4}',
            'ghp is a prefix but too short to be a token',
            '',
            None,
        ):
            self.assertFalse(manager.contains_github_token(content), repr(content))


class GetFilesTokenScanTest(unittest.TestCase):
    """A leftover plugin config carrying a real token must never be uploaded."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, content):
        full = os.path.join(self.user, 'PackageSync.sublime-settings')
        with open(full, 'w') as f:
            f.write(content)
        with mock.patch.object(manager.path, 'list_files', return_value=[full]), \
                mock.patch.object(manager, 'get_content', return_value=content), \
                mock.patch.object(manager.settings, 'get', return_value=None):
            return manager.get_files()

    def test_token_file_is_skipped(self):
        content = '{"access_token": "ghp_' + 'a' * 36 + '"}'
        self.assertEqual(self._run(content), {})

    def test_clean_file_is_kept(self):
        content = '{"some_option": true}'
        self.assertIn(manager.path.encode('PackageSync.sublime-settings'), self._run(content))


class WriteUserFilesRestoreTest(unittest.TestCase):
    """Restore is the inverse of backup: files are written back as they arrive,
    with no token filtering. Only upload filters tokens."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.user = os.path.join(self.tmp, 'User')
        os.makedirs(self.user)
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_token_carrying_file_is_written(self):
        content = b'{"access_token": "ghp_' + b'a' * 36 + b'"}'
        manager.write_user_files({'PackageSync.sublime-settings': content}, preserve_packages=True)
        target = os.path.join(self.user, 'PackageSync.sublime-settings')
        self.assertTrue(os.path.exists(target))
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_clean_file_written(self):
        manager.write_user_files({'Preferences.sublime-settings': b'{"x": 1}'}, preserve_packages=True)
        self.assertTrue(os.path.exists(os.path.join(self.user, 'Preferences.sublime-settings')))


class TestInstallMissingPackages(unittest.TestCase):
    @mock.patch('sync_settings_reborn.sync_manager.sublime.active_window')
    def test_installs_only_missing(self, active_mock):
        window = mock.MagicMock()
        active_mock.return_value = window
        with mock.patch.object(manager, 'local_installed_packages',
                               return_value=['A', 'B']):
            manager.install_missing_packages(['A', 'B', 'C'])
        window.run_command.assert_called_once_with(
            'advanced_install_package', {'packages': ['C']})

    @mock.patch('sync_settings_reborn.sync_manager.sublime.active_window',
                return_value=None)
    def test_no_window_is_safe(self, active_mock):
        # Must not raise when there is no active window.
        manager.install_missing_packages(['X'])
        active_mock.assert_called_once()

    def test_no_missing_is_noop(self):
        with mock.patch.object(manager, 'local_installed_packages',
                               return_value=['A', 'B', 'C']):
            # active_window is never consulted when nothing is missing.
            manager.install_missing_packages(['A', 'B', 'C'])


class PathCanonicalTest(unittest.TestCase):
    """A gist key created by this plugin (encoded) and by an external tool
    (literal separators) must collapse to the same internal key."""

    def test_canonical_collapses_literal_and_encoded(self):
        self.assertEqual(manager.path.canonical('sub/C.sublime-settings'),
                         manager.path.canonical('sub%2FC.sublime-settings'))
        self.assertEqual(manager.path.canonical('sub/C.sublime-settings'),
                         manager.path.encode('sub/C.sublime-settings'))


class FetchFilesProxyAndCanonicalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patcher = mock.patch.object(manager.sublime, 'packages_path', lambda: self.tmp)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _fake_download(url, name, proxies=None, timeout=None):
        # http.download streams the bytes into name itself.
        with open(name, 'wb') as f:
            f.write(b'')
        return 200

    @mock.patch('sync_settings_reborn.sync_manager.http.download')
    @mock.patch('sync_settings_reborn.sync_manager.Gist')
    def test_fetch_files_passes_configured_proxy(self, Gist, download):
        # Manual Download must honour the same proxy config as the gist API.
        Gist.from_settings.return_value.proxies = {'https': 'http://proxy:3128'}
        download.side_effect = self._fake_download
        target = os.path.join(self.tmp, 'temp')
        manager.fetch_files({'A.sublime-settings': {'raw_url': 'http://a'}}, to=target)
        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs['proxies'],
                         {'https': 'http://proxy:3128'})

    @mock.patch('sync_settings_reborn.sync_manager.http.download')
    @mock.patch('sync_settings_reborn.sync_manager.Gist')
    def test_fetch_files_canonicalises_external_key(self, Gist, download):
        Gist.from_settings.return_value.proxies = {}
        download.side_effect = self._fake_download
        target = os.path.join(self.tmp, 'temp')
        # An external gist carries a literal path separator in the filename.
        manager.fetch_files({'sub/C.sublime-settings': {'raw_url': 'http://c'}}, to=target)
        # The temp file is written under the canonical (encoded) key so it
        # decodes back to the same on-disk path this plugin would produce.
        self.assertTrue(os.path.exists(
            os.path.join(target, 'sub%2FC.sublime-settings')))

    @mock.patch('sync_settings_reborn.sync_manager.http.download')
    @mock.patch('sync_settings_reborn.sync_manager.Gist')
    def test_fetch_files_skips_missing_raw_url(self, Gist, download):
        Gist.from_settings.return_value.proxies = {}
        download.side_effect = self._fake_download
        target = os.path.join(self.tmp, 'temp')
        # A gist entry missing raw_url (e.g. a truncated/deleted file) must be
        # skipped, not raise mid-loop; a sibling with a raw_url still downloads.
        manager.fetch_files({
            'A.sublime-settings': {'raw_url': 'http://a'},
            'B.sublime-settings': {},
        }, to=target)
        download.assert_called_once()
        self.assertEqual(download.call_args.args[0], 'http://a')
        self.assertFalse(os.path.exists(
            os.path.join(target, 'B.sublime-settings')))
