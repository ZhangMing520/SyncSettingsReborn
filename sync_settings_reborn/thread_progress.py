# -*- coding: utf-8 -*-
# Credits to @wbond package_control

import sublime
import threading

# Only the oldest live progress paints the status area. Overlapping commands
# (e.g. a second gist command queued behind the manual lock) would otherwise
# alternate their messages every 100 ms; later ones take over as earlier ones
# finish.
_live = []


class ThreadProgress:
    """
      Animates an indicator, [=   ], in the status area while a thread runs

      A command that finishes while an older progress is still running does not
      show its success message: the older animation would overwrite it.

      :param target:
        The thread to track for activity

      :param message:
        The message to display next to the activity indicator
    """

    def __init__(self, target, message, success_message='', success_when=None):
        self.message = message
        self.success_message = success_message
        # Optional callable that returns False when the operation failed, so we
        # don't show a misleading success message after an error.
        self.success_when = success_when
        self.addend = 1
        self.size = 8

        _live.append(self)
        self.thread = threading.Thread(target=target)
        self.thread.start()
        sublime.set_timeout(lambda: self.run(0), 100)

    def run(self, i):
        if not self.thread.is_alive():
            if self in _live:
                _live.remove(self)
            if _live:
                # The next progress repaints on its own next tick.
                return
            if self.success_when is not None and not self.success_when():
                return
            msg = '' if not self.success_message else 'SyncSettingsReborn: {}'.format(self.success_message)
            sublime.status_message(msg)
            return

        if _live and _live[0] is self:
            before = i % self.size
            after = (self.size - 1) - before

            sublime.status_message('SyncSettingsReborn: {} [{}={}]'.format(self.message, ' ' * before, ' ' * after))

            if not after:
                self.addend = -1
            if not before:
                self.addend = 1

            i += self.addend

        sublime.set_timeout(lambda: self.run(i), 100)
