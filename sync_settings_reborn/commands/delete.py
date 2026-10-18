# -*- coding: utf-8 -*-

import sublime
import sublime_plugin

from . import decorators
from ..libs import gist
from ..libs import settings
from ..libs.logger import logger
from ..thread_progress import ThreadProgress
from .. import sync_version as version


class SyncSettingsRebornDeleteCommand(sublime_plugin.WindowCommand):
    @decorators.with_manual_gist_lock
    def delete(self, gid):
        try:
            gist.Gist.from_settings().delete(gid)
            # Forget the deleted gist and its version info so the next
            # Upload creates a fresh one.
            settings.update('gist_id', '')
            version.update_config_file({})
            logger.info('gist {} deleted'.format(gid))
        except gist.NotFoundError as e:
            decorators.report_gist_not_found(e)
        except Exception as e:
            decorators.report_error(self, e)

    @decorators.check_settings('gist_id', 'access_token')
    def run(self):
        dialog_message = (
            'SyncSettingsReborn:\n\n'
            'This action will delete your remote backup, do you want to proceed with this action?\n\n'
            'Note: this action is irreversible'
        )
        if sublime.yes_no_cancel_dialog(dialog_message) == sublime.DIALOG_YES:
            gid = settings.get('gist_id')
            self._failed = False
            ThreadProgress(
                target=lambda: self.delete(gid),
                message='deleting gist `{}`'.format(gid),
                success_message='gist deleted',
                success_when=lambda: not self._failed
            )
