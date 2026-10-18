# -*- coding: utf-8 -*-

import os
import shutil

import sublime
import sublime_plugin

from . import decorators
from .. import auto_sync, sync_version as version, sync_manager as manager
from ..libs import settings, path
from ..libs.gist import Gist
from ..libs.logger import logger

from ..thread_progress import ThreadProgress


class SyncSettingsRebornDownloadCommand(sublime_plugin.WindowCommand):
    temp_folder = path.join(os.path.expanduser('~'), '.sync_settings_reborn', 'temp')

    def on_done(self, g):
        manager.move_files(self.temp_folder)
        try:
            commit = g['history'][0]
            settings.update('gist_id', g['id'])
            version.update_config_file({
                'hash': commit['version'],
                'created_at': commit['committed_at'],
            })
            # Align background auto-sync with what was just downloaded so it
            # doesn't flag every remote file as a conflict on its next cycle.
            auto_sync.adopt_manual_download(g)
        except Exception as e:
            logger.warning('could not update gist metadata')
            logger.exception(e)
        shutil.rmtree(self.temp_folder, ignore_errors=True)

    @decorators.with_manual_gist_lock
    def download(self):
        try:
            g = Gist.from_settings().get(settings.get('gist_id'))
            files = g.get('files') or {}
            if not files:
                logger.warning('The gist `{}` contains no files.'.format(settings.get('gist_id')))
                sublime.status_message('SyncSettingsReborn: the gist is empty or not found')
                self._failed = True
                return

            manager.fetch_files(files, self.temp_folder)

            # Read the remote package list before on_done removes the temp dir.
            remote_packages = manager.installed_packages_from_content(
                manager.get_content(
                    path.join(self.temp_folder, path.encode('Package Control.sublime-settings'))
                )
            )

            # Restore the user files first. This is the primary goal and must
            # always happen, independent of the package-install step below.
            self.on_done(g)

            # Best-effort: install any packages present remotely but missing
            # locally (shared helper, same path auto-sync uses). Wrapped so a
            # failure here can never block the file restore above.
            manager.install_missing_packages(remote_packages)
        except Exception as e:
            decorators.report_error(self, e)

    @decorators.check_settings('gist_id')
    def run(self):
        self._failed = False
        ThreadProgress(
            target=self.download,
            message='downloading files',
            success_message='files downloaded',
            success_when=lambda: not self._failed
        )
