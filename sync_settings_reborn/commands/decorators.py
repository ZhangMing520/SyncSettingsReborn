# -*- coding: utf-8 -*-

from functools import wraps
import sublime

from ..libs import settings
from ..libs.logger import logger
from .. import auto_sync


def with_manual_gist_lock(func):
    """Run ``func`` while holding the lock the background cycle also takes."""
    @wraps(func)
    def with_manual_gist_lock_inner(self, *args, **kwargs):
        with auto_sync.manual_gist_lock():
            return func(self, *args, **kwargs)
    return with_manual_gist_lock_inner


def check_settings(*props):
    def check_settings_wrapper(func):
        @wraps(func)
        def check_settings_inner(self, *args, **kwargs):
            for prop in props:
                if not settings.get(prop):
                    prop_text = 'properties' if len(args) > 1 else 'property'
                    msg = (
                        'SyncSettingsReborn:\n\n'
                        'The {} {}, must be defined. Edit your settings file.'
                    )
                    sublime.message_dialog(msg.format(prop_text, ' and '.join(props)))
                    sublime.active_window().run_command('open_file', {
                        'file': '${packages}/User/SyncSettingsReborn.sublime-settings'
                    })
                    return
            func(self, *args, **kwargs)
        return check_settings_inner
    return check_settings_wrapper


def report_error(cmd, e):
    """Log an unexpected error and surface it to the user.

    Also marks the command as failed so ThreadProgress does not show a
    misleading success message after an error.
    """
    logger.exception(e)
    cmd._failed = True
    sublime.message_dialog('SyncSettingsReborn:\n\n{}'.format(str(e)))


def report_gist_not_found(e):
    """Surface a gist 404 with the token-scope guidance shared by every
    command that talks to the Gist API."""
    msg = (
        'SyncSettingsReborn:\n\n'
        '{}\n\n'
        'Please check if the access token was created with the gist scope.\n\n'
        'If the access token is correct, please, delete the value of `gist_id` property manually.'
    )
    sublime.message_dialog(msg.format(str(e)))
