# -*- coding: utf-8 -*-

import unittest

import mock

from sync_settings_reborn import thread_progress


class ThreadProgressTest(unittest.TestCase):
    def setUp(self):
        self.messages = []
        thread_progress._live.clear()
        self._patch('status_message', self.messages.append)
        # Keep the animation chain from recursing: tests drive `run()` by hand.
        self._patch('set_timeout', lambda fn, *a, **k: None)

    def _patch(self, name, new):
        patcher = mock.patch.object(thread_progress.sublime, name, new)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _progress(self, message, alive=True, success_message='', success_when=None):
        with mock.patch.object(thread_progress.threading, 'Thread') as thread:
            thread.return_value.is_alive.return_value = alive
            return thread_progress.ThreadProgress(
                lambda: None, message, success_message, success_when)

    def test_paints_the_animation_frame(self):
        progress = self._progress('uploading files')

        progress.run(0)

        self.assertEqual(self.messages, ['SyncSettingsReborn: uploading files [=       ]'])

    def test_only_the_oldest_progress_paints_the_status(self):
        # A command queued behind a running one must not repaint over it.
        older = self._progress('uploading files')
        newer = self._progress('downloading files')

        older.run(0)
        newer.run(0)

        self.assertEqual(len(self.messages), 1)
        self.assertIn('uploading files', self.messages[0])

    def test_next_progress_takes_over_when_the_oldest_finishes(self):
        older = self._progress('uploading files')
        newer = self._progress('downloading files')
        older.thread.is_alive.return_value = False

        older.run(0)
        newer.run(0)

        self.assertEqual(len(self.messages), 1)
        self.assertIn('downloading files', self.messages[0])

    def test_last_progress_shows_its_success_message(self):
        progress = self._progress('uploading files', alive=False, success_message='files uploaded')

        progress.run(0)

        self.assertEqual(self.messages, ['SyncSettingsReborn: files uploaded'])
        self.assertEqual(thread_progress._live, [])

    def test_failed_operation_shows_no_success_message(self):
        progress = self._progress('uploading files', alive=False,
                                  success_message='files uploaded', success_when=lambda: False)

        progress.run(0)

        self.assertEqual(self.messages, [])
        self.assertEqual(thread_progress._live, [])


if __name__ == '__main__':
    unittest.main()
