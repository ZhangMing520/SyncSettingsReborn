# -*- coding: utf-8 -*-

import sublime
import sublime_plugin

from . import decorators
from .. import auto_sync, sync_version as version, sync_manager as manager
from ..libs import settings
from ..libs import gist
from ..libs.logger import logger
from ..thread_progress import ThreadProgress


class SyncSettingsRebornUploadCommand(sublime_plugin.WindowCommand):
    def _remote_names_and_deletions(self, gist_api, gid, files):
        """Return ``(name_map, removable)`` for the existing gist.

        ``name_map`` groups each canonical internal key with the real
        filename(s) the gist carries — other tools keep literal path
        separators (``sub/C.sublime-settings`` vs the encoded
        ``sub%2FC...``), and the PATCH must rename/null those real names
        instead of forking twins. ``removable`` is the subset of this
        machine's persisted deletions the gist actually carries; only those
        are eligible for propagation, compared canonically across key spaces.

        On a failed listing both come back empty: the upload then proceeds
        with plain encoded keys (deletions are withheld, never guessed).
        """
        baseline = (version.get_local_version() or {}).get('files') or {}
        deleted = {
            name for name in baseline
            if name not in files and not manager.user_file_exists(name)
        }
        try:
            g = gist_api.get(gid) or {}
        except Exception:
            # Can't list the gist: update with what we have, never guess
            # deletions or renames from a failed listing.
            return {}, set()
        name_map = auto_sync._name_map(g)
        return name_map, {k for k in deleted if k in name_map}

    def upload(self, installed=None):
        files = manager.get_files(installed=installed)
        if not len(files):
            logger.warning('no files collected to upload (check exclude/include settings)')
            sublime.status_message('SyncSettingsReborn: there are not files to upload')
            return
        gid = settings.get('gist_id')
        logger.info('uploading {} file(s) to gist {}'.format(len(files), gid or '(new)'))
        try:
            gist_api = gist.Gist.from_settings()
            if gid:
                # Gist PATCH is a merge: files absent from the payload are kept
                # on the gist. The listing maps real remote names (foreign
                # tools keep literal separators) to our canonical keys, and
                # resolves this machine's eligible deletions.
                name_map, removable = self._remote_names_and_deletions(
                    gist_api, gid, files)
                payload, _ = auto_sync._build_payload(
                    set(files) | removable, files,
                    auto_sync._content_hashes(files), {}, name_map)
                # Update the existing gist.
                g = gist_api.update(gid, data={'files': payload})
            else:
                # No gist yet. A concurrent auto-sync cycle may create one while
                # we wait, so re-check inside the lock and update that gist
                # instead of forking a second one.
                with auto_sync._gist_create_lock:
                    gid = settings.get('gist_id')
                    if gid:
                        name_map, removable = self._remote_names_and_deletions(
                            gist_api, gid, files)
                        payload, _ = auto_sync._build_payload(
                            set(files) | removable, files,
                            auto_sync._content_hashes(files), {}, name_map)
                        g = gist_api.update(gid, data={'files': payload})
                    else:
                        # No gist yet: create one and remember it so the next
                        # upload updates instead of creating again. No
                        # description prompt, no "backfill gist_id?" question —
                        # this is the one-click reset path.
                        g = gist_api.create(
                            {'files': files, 'description': 'SyncSettingsReborn backup'})
                        settings.update('gist_id', g['id'])
                        logger.info('created new gist {}'.format(g['id']))
            commit = g['history'][0]
            version.update_config_file({
                'hash': commit['version'],
                'created_at': commit['committed_at'],
            })
            # Keep background auto-sync's merge baseline aligned so these files
            # don't look like fresh local edits on its next cycle.
            auto_sync.adopt_manual_upload(files, g)
            logger.info('upload complete')
        except gist.NotFoundError as e:
            decorators.report_gist_not_found(e)
        except Exception as e:
            decorators.report_error(self, e)

    @decorators.check_settings('access_token')
    def run(self):
        self._failed = False
        # sublime.list_packages() only works on the main thread, so take the
        # snapshot here (run() runs there) and hand it to the worker thread.
        installed = manager.installed_packages_snapshot()
        ThreadProgress(
            target=lambda: self.upload(installed),
            message='uploading files',
            success_message='files uploaded',
            success_when=lambda: not self._failed
        )
