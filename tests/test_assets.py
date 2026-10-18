# -*- coding: utf-8 -*-
"""Guards for the static assets shipped to Package Control.

Sublime silently drops a menu file that fails to parse, and Package Control
silently skips a missing message file, so neither failure surfaces at runtime.
"""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SETTINGS_COMMAND = 'edit_settings'
BUILTIN_COMMANDS = {SETTINGS_COMMAND}


def _load_json(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return json.load(f)


def _iter_entries(entries):
    for entry in entries:
        yield entry
        yield from _iter_entries(entry.get('children', []))


def _package_command_names():
    """Command names defined by this package, derived from its class names."""
    names = set()
    for dirpath, _dirnames, filenames in os.walk(os.path.join(ROOT, 'sync_settings_reborn')):
        for filename in filenames:
            if not filename.endswith('.py'):
                continue
            with open(os.path.join(dirpath, filename), encoding='utf-8') as f:
                source = f.read()
            for cls in re.findall(r'^class ([A-Za-z0-9_]+)Command\b', source, re.M):
                names.add(re.sub(r'(?<!^)(?=[A-Z])', '_', cls).lower())
    return names


MENU = _load_json('Main.sublime-menu')
PALETTE = _load_json('Default.sublime-commands')
MESSAGES = _load_json('messages.json')
KNOWN_COMMANDS = _package_command_names() | BUILTIN_COMMANDS


class MenuAssetsTest(unittest.TestCase):
    def _assert_known_commands(self, entries, source):
        for entry in _iter_entries(entries):
            command = entry.get('command')
            if command is not None:
                self.assertIn(command, KNOWN_COMMANDS, '%s references unknown command %r' % (source, command))

    def _top_level(self, entry_id):
        entries = [e for e in MENU if e.get('id') == entry_id]
        self.assertEqual(len(entries), 1, 'expected exactly one %r entry' % entry_id)
        return entries[0]

    def test_main_menu_command_references(self):
        self._assert_known_commands(MENU, 'Main.sublime-menu')

    def test_command_palette_command_references(self):
        self._assert_known_commands(PALETTE, 'Default.sublime-commands')

    def test_menu_ids_are_unique(self):
        seen = set()
        for entry in _iter_entries(MENU):
            entry_id = entry.get('id')
            if entry_id is None:
                continue
            self.assertNotIn(entry_id, seen, 'duplicate menu id %r' % entry_id)
            seen.add(entry_id)

    def test_tools_entry_uses_merge_form(self):
        # A caption/mnemonic here makes Sublime append a second Tools menu
        # instead of merging into the built-in one.
        self.assertEqual(set(self._top_level('tools')), {'id', 'children'})

    def test_preferences_entry_keeps_caption(self):
        prefs = self._top_level('preferences')
        self.assertIn('caption', prefs)
        self.assertIn('mnemonic', prefs)

    def test_settings_entries_use_edit_settings(self):
        """The channel review requires the settings entry to use edit_settings."""
        entries = list(_iter_entries(MENU)) + PALETTE
        settings_entries = [e for e in entries if 'base_file' in (e.get('args') or {})]
        self.assertTrue(settings_entries, 'no settings entry found')
        for entry in settings_entries:
            self.assertEqual(entry.get('command'), SETTINGS_COMMAND)


class MessagesManifestTest(unittest.TestCase):
    def test_referenced_message_files_exist(self):
        for version, path in MESSAGES.items():
            self.assertTrue(
                os.path.isfile(os.path.join(ROOT, path)),
                'messages.json entry %s points at missing %s' % (version, path),
            )

    def test_no_orphan_message_files(self):
        referenced = {os.path.basename(p) for p in MESSAGES.values()}
        on_disk = set(os.listdir(os.path.join(ROOT, 'messages')))
        self.assertEqual(on_disk - referenced, set(), 'message files missing from messages.json')
