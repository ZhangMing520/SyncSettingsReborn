# -*- coding: utf-8 -*-

from fnmatch import fnmatch
import os
import json
import re
import shutil
import sublime
import threading
import time

from .libs import path, settings, file, http
from .libs.gist import Gist
from .libs.logger import logger

from queue import Queue


def get_content(file):
    if not path.exists(file):
        return ''
    try:
        with open(file, 'rb') as fi:
            # Strict UTF-8: Sublime config files are text, and swallowing
            # decode errors with errors='ignore' would silently corrupt a
            # binary/non-UTF-8 file and upload the damaged bytes. Skip it
            # instead (it is then absent from the sync, like a token file).
            return fi.read().decode('utf-8')
    except UnicodeDecodeError:
        logger.warning('skipping non-UTF-8 file (not uploaded): {}'.format(file))
    except Exception as e:
        logger.warning('file `{}` has errors'.format(file))
        logger.exception(e)
    return ''


def _as_patterns(key):
    """Return `excluded_files`/`included_files` as a list of patterns.

    Users sometimes set these options to a single string instead of a list,
    which used to crash with "'str' object has no attribute 'extend'". Be
    tolerant: a string is treated as a single pattern, and we warn so the
    misconfiguration is visible instead of failing silently.
    """
    patterns = settings.get(key) or []
    if isinstance(patterns, str):
        logger.warning(
            "`{}` should be a list of patterns, but a string was given; "
            "treating it as a single pattern.".format(key)
        )
        return [patterns]
    return list(patterns)


# GitHub's own token prefixes: ghp_ (personal), gho_ (OAuth), ghu_ (user to
# server), ghs_ (server to server), ghr_ (refresh), plus the fine-grained
# github_pat_ form. Leaking any of these into a gist makes GitHub's secret
# scanning silently revoke the token, which is exactly the failure this guards
# against. Only GitHub prefixes are matched on purpose: broadening this to other
# providers would catch nothing real here while risking false positives on
# ordinary settings values.
_GITHUB_TOKEN_RE = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})')


def contains_github_token(content):
    """True when `content` looks like it holds a GitHub token.

    This content rule — not a hard-coded file name — is what keeps a secret out
    of the Gist: any file carrying a token is skipped on upload. The filter is
    upload-only by design: restore (and the offline zip backup) is the inverse
    of a full local mirror and writes files back as they arrive, so the plugin's
    own settings file can be backed up and restored like any other.
    """
    if not content:
        return False
    return _GITHUB_TOKEN_RE.search(content) is not None


def should_exclude(file_name):
    basename = os.path.basename(file_name)
    patterns = _as_patterns('excluded_files')
    dir_patterns = _as_patterns('ignore_dirs')
    if dir_patterns:
        decoded = path.decode(file_name)
        # Match only directory components, never the file name itself, so a file
        # coincidentally named like an ignored directory is not excluded.
        dir_parts = [p for p in decoded.split(path.separator()) if p][:-1]
        for pattern in dir_patterns:
            if any(fnmatch(part, pattern) for part in dir_parts):
                return True
    return _matches(patterns, file_name, basename)


def _matches(patterns, name, basename):
    return any(fnmatch(name, p) or fnmatch(basename, p) for p in patterns)


def should_include(file_name):
    basename = os.path.basename(file_name)
    patterns = _as_patterns('included_files')
    return _matches(patterns, file_name, basename)


def is_synced(file_name):
    """True when a file should be backed up / restored.

    A file is kept unless it is excluded and not explicitly included.
    """
    return not (should_exclude(file_name) and not should_include(file_name))


# Global files/dirs not tied to a single installed package that must always be
# kept, even when skip_uninstalled_packages is enabled. The plugin's own name
# is derived from its settings file so a rename cannot make this set stale.
_GLOBAL_SETTINGS = {
    'Preferences',
    'Package Control',
    os.path.splitext(settings.filename)[0],
}

# File types that are conventionally named `<PackageName>.<ext>` and therefore
# attributable to a specific package (so a leftover from an uninstalled package
# can be identified and skipped). Generic files (.txt, .sublime-project, etc.)
# are not in this set and are never skipped by this option.
_PACKAGE_SCOPED_EXTS = {
    '.sublime-settings', '.sublime-keymap', '.sublime-commands',
    '.sublime-mousemap', '.sublime-menu',
}


def should_skip_uninstalled(rel_path, installed):
    """True when `rel_path` (relative to User) belongs to a package that is
    not currently installed.

    Covers `<Package>.<ext>` files directly in User (for package-scoped exts)
    and files inside a `<Package>/` data directory, so a removed plugin's
    leftovers (settings file and/or data dir) are both skipped. Generic top-level
    files and the whitelisted globals are never skipped.

    `installed` being empty/None means "do not filter": a failed snapshot must
    never turn into "every package-scoped file looks uninstalled".
    """
    if not installed:
        return False
    parts = [p for p in rel_path.split(path.separator()) if p]
    if not parts:
        return False
    if len(parts) == 1:
        component, ext = os.path.splitext(parts[0])
        if ext not in _PACKAGE_SCOPED_EXTS:
            return False
    else:
        component = parts[0]
    if component in _GLOBAL_SETTINGS:
        return False
    return component not in installed


def _packages_on_disk():
    """Package names derived from the filesystem.

    Version-independent (some Sublime builds expose no package-listing API at
    all) and, unlike Package Control's list, it also sees packages that were
    placed by hand — a symlinked checkout, for instance:
    `Installed Packages/<Name>.sublime-package` and `Packages/<Name>/`.
    """
    names = set()
    try:
        for entry in os.listdir(sublime.installed_packages_path()):
            if entry.endswith('.sublime-package'):
                names.add(os.path.splitext(entry)[0])
    except Exception as e:
        logger.debug('cannot list Installed Packages: {}'.format(e))
    try:
        packages_dir = sublime.packages_path()
        for entry in os.listdir(packages_dir):
            if entry == 'User':
                continue  # the user's own config dir, not a package
            full = os.path.join(packages_dir, entry)
            if os.path.isdir(full):
                names.add(entry)
            elif entry.endswith('.sublime-package'):
                names.add(os.path.splitext(entry)[0])
    except Exception as e:
        logger.debug('cannot list Packages: {}'.format(e))
    return names


def installed_packages_snapshot():
    """Snapshot of currently installed package names, or None to not filter.

    Several sources are unioned because none is complete on its own: Package
    Control's `installed_packages` misses manually placed packages, the
    filesystem check works on every build, and `sublime.list_packages()` is
    absent on some builds. Returns None when the option is off or every source
    comes up empty, so a failure filters nothing rather than everything.
    """
    if not settings.get('skip_uninstalled_packages'):
        return None
    names = set(local_installed_packages())
    names |= _packages_on_disk()
    lister = getattr(sublime, 'list_packages', None)
    if callable(lister):
        try:
            names.update(lister())
        except Exception as e:
            logger.warning('sublime.list_packages() failed; '
                           'ignoring that source')
            logger.exception(e)
    return names or None


def resolve_installed(installed=None):
    """Normalize a caller-supplied snapshot, taking one if none was given."""
    if installed is None:
        return installed_packages_snapshot()
    return installed or None


def iter_user_files(installed=None):
    """Yield (absolute_path, rel_path) for each file under Packages/User that
    passes the shared sync filters (exclude/include + uninstalled-package).

    Both exporters — the Gist upload and the offline zip — build on this so
    they can never silently diverge in which files they collect.
    """
    user_path = path.join(sublime.packages_path(), 'User')
    installed = resolve_installed(installed)
    seen = set()
    for f in path.list_files(user_path):
        rel = f.replace('{}{}'.format(user_path, path.separator()), '')
        encoded = path.encode(rel)
        if encoded in seen:
            continue
        seen.add(encoded)
        if not is_synced(f):
            continue
        if should_skip_uninstalled(rel, installed):
            logger.info('skipping file for uninstalled package: {}'.format(rel))
            continue
        yield f, rel


def get_files(installed=None):
    files_with_content = dict()
    for f, rel in iter_user_files(installed):
        content = get_content(f)
        if not content.strip():
            continue
        if contains_github_token(content):
            logger.warning('skipping file that appears to contain a GitHub token: {}'.format(rel))
            continue
        files_with_content[path.encode(rel)] = {'content': content, 'path': f}
    return files_with_content


def download_file(q):
    while not q.empty():
        url, name, proxies = q.get()
        try:
            status = http.download(url, name, proxies=proxies)
            if status != 200:
                # Nothing was written for a non-200: download() owns that rule,
                # since fetch_files installs whatever is in the temp dir.
                logger.warning('download skipped (status {}): {}'.format(status, url))
        except Exception as e:
            # Never swallow silently: a failed per-file download would leave an
            # empty temp dir and the restore step would appear to "do nothing".
            logger.warning('download failed: {}'.format(url))
            logger.exception(e)
        finally:
            q.task_done()


def fetch_files(files, to=''):
    # Always (re)create the destination so a leftover temp folder from a prior
    # failed run can never cause `move_files` to fail with a missing directory.
    if path.exists(to, folder=True):
        shutil.rmtree(to, ignore_errors=True)
    os.makedirs(to, exist_ok=True)

    # Honour the same proxy configuration as the gist API client, so a user
    # who set http_proxy/https_proxy gets consistent behaviour for the actual
    # file bytes (raw.githubusercontent.com) too.
    proxies = Gist.from_settings().proxies

    rq = Queue(maxsize=0)
    user_path = path.join(sublime.packages_path(), 'User')
    items = files.items()
    for k, gfile in items:
        decoded_name = path.decode(k)
        name = path.join(user_path, decoded_name)
        if not is_synced(name):
            continue
        # A missing raw_url skips this file rather than aborting the whole
        # download (gfile['raw_url'] here used to raise mid-loop).
        raw_url = gfile.get('raw_url')
        if not raw_url:
            logger.warning('gist file `{}` has no raw_url; skipping'.format(k))
            continue
        # Canonicalise the gist key into this plugin's internal key space so a
        # temp file written here decodes back to the same on-disk path whether
        # the gist was created by this plugin or an external tool.
        rq.put((raw_url, path.join(to, path.canonical(k)), proxies))

    threads = min(10, len(items))
    for i in range(threads):
        worker = threading.Thread(target=download_file, args=(rq,))
        worker.setDaemon(True)
        worker.start()
        time.sleep(0.1)
    rq.join()


def _is_within(user_real, target):
    target_real = os.path.realpath(target)
    return target_real == user_real or target_real.startswith(user_real + os.sep)


def _write_one(user_path, user_real, name, data):
    # The decode + traversal guard lives in one place (_resolve_inside_user).
    target = _resolve_inside_user(user_real, name)
    if target is None:
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    mode = 'wb' if isinstance(data, (bytes, bytearray)) else 'w'
    with open(target, mode) as f:
        f.write(data)


def local_installed_packages():
    """The package list Package Control currently has recorded locally."""
    try:
        local_settings = sublime.load_settings('Package Control.sublime-settings')
        local_list = local_settings.get('installed_packages') or []
        return local_list if isinstance(local_list, list) else []
    except Exception:
        return []


def installed_packages_from_content(content):
    """Parse the `installed_packages` list out of Package Control settings content."""
    if not content:
        return []
    try:
        data = file.encode_json(content)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    pkgs = data.get('installed_packages') or []
    return pkgs if isinstance(pkgs, list) else []


def install_missing_packages(remote_packages):
    """Best-effort install of packages present remotely but missing locally.

    Uses Package Control's ``advanced_install_package`` command. A failure here
    (e.g. Package Control absent, or the window busy) is non-fatal and only
    logged, since the file restore that triggered it must never be blocked.
    """
    try:
        if not remote_packages:
            return
        local = local_installed_packages()
        missing = set(remote_packages).difference(local)
        if not missing:
            return
        names = sorted(missing)
        window = sublime.active_window()
        if window is None:
            logger.warning('no active window; skipping install of packages: {}'.format(names))
            return

        # Dispatch on the main thread: this runs from a background worker (the
        # auto-sync loop and the Download command thread), and running a window
        # command from off the main thread is not the documented-safe path.
        # Fire-and-forget keeps the best-effort, non-blocking contract.
        def _install():
            window.run_command(
                'advanced_install_package', {'packages': names})
        sublime.set_timeout(_install, 0)
        logger.info('requested install of missing packages: {}'.format(names))
    except Exception as e:
        logger.warning('skipping package installation')
        logger.exception(e)


def _merge_installed_packages(data):
    """Union the remote `installed_packages` with the local list so a restore
    never drops packages the user already has on this machine."""
    try:
        parsed = file.encode_json(data.decode('utf-8', errors='ignore'))
    except Exception:
        return data
    if not isinstance(parsed, dict):
        return data
    remote_list = parsed.get('installed_packages') or []
    if not isinstance(remote_list, list):
        remote_list = []
    parsed['installed_packages'] = sorted(set(remote_list) | set(local_installed_packages()))
    return json.dumps(parsed, indent=4).encode('utf-8')


def write_user_files(files, preserve_packages=True):
    """Write collected files ({name: content}) into Packages/User.

    `Preferences`/`Package Control` files are written last. When
    `preserve_packages` is true, the incoming `Package Control.sublime-settings`
    is merged with the local `installed_packages` instead of overwriting it.
    """
    user_path = path.join(sublime.packages_path(), 'User')
    user_real = os.path.realpath(user_path)
    deferred = {}
    for key, data in files.items():
        name = path.decode(key)
        if name.endswith('Preferences.sublime-settings') or name.endswith('Package Control.sublime-settings'):
            deferred[key] = data
            continue
        _write_one(user_path, user_real, key, data)

    for key, data in deferred.items():
        name = path.decode(key)
        if name.endswith('Package Control.sublime-settings') and preserve_packages:
            data = _merge_installed_packages(data)
        _write_one(user_path, user_real, key, data)


def _resolve_inside_user(user_real, key):
    """Decode an encoded/raw relative name and return its real path only when
    it stays inside Packages/User (same guard as _write_one)."""
    user_path = path.join(sublime.packages_path(), 'User')
    target = path.join(user_path, path.decode(key))
    if not _is_within(user_real, target):
        logger.warning('refusing path outside Packages/User: {}'.format(target))
        return None
    return target


def user_file_exists(key):
    """True when a User file named `key` exists on disk, regardless of the
    upload filters (exclude/include, token scan, empty skip, uninstalled skip).

    Auto-sync uses this to tell a real deletion apart from a file that is
    merely filtered out: only a missing file may be deleted from the gist.
    """
    user_path = path.join(sublime.packages_path(), 'User')
    target = _resolve_inside_user(os.path.realpath(user_path), key)
    return target is not None and os.path.isfile(target)


def delete_user_files(names):
    """Delete the given (encoded- or raw-name) files from Packages/User.

    Used by auto-sync to propagate remote deletions. Only files inside
    Packages/User are ever removed; directories are never touched.
    """
    user_path = path.join(sublime.packages_path(), 'User')
    user_real = os.path.realpath(user_path)
    for key in names:
        target = _resolve_inside_user(user_real, key)
        if target is None:
            continue
        try:
            os.remove(target)
            logger.info('auto-sync removed file deleted on the gist: {}'.format(key))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning('could not delete synced file {}: {}'.format(key, e))


def move_files(origin):
    if not path.exists(origin, folder=True):
        logger.warning('download temp folder is missing, nothing to restore: {}'.format(origin))
        return
    files = {}
    for f in os.listdir(origin):
        with open(path.join(origin, f), 'rb') as fh:
            files[f] = fh.read()
    write_user_files(files, preserve_packages=True)
