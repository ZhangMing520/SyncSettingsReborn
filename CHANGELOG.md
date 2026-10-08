## v4.2.4

- Renamed the settings Command Palette entry to `Preferences: SyncSettingsReborn
  Settings` (matching the Package Control convention) and added `args.default`
  so a clean settings file is created when first edited.

## v4.2.3

- Added a Command Palette entry `SyncSettingsReborn: Edit Settings` that opens
  the package settings through `edit_settings` (with defaults shown alongside
  user overrides), replacing the previous `open_file` entry.

## v4.2.2 — drop the `requests` dependency (stdlib HTTP layer)

The package no longer depends on Package Control's `requests` package. The
copy distributed via the official channel is pinned to upstream requests
2.15.1 (2017), which cannot import on current Sublime builds running Python
3.14: its vendored urllib3 does `from collections import Mapping` (removed in
Python 3.10) and its `requests.utils` imports `cgi` (removed in Python 3.13).
The declared transitive dependencies (`urllib3`, `idna`, `certifi`,
`charset_normalizer`) do not exist in the channel, so they could not fix it.

- All HTTP traffic now goes through a new small standard-library layer
  (`sync_settings_reborn/libs/http.py`) built on `urllib.request`: gist API
  verbs, raw-url file downloads (manual Download and auto-sync), proxy
  settings (`http_proxy` / `https_proxy`), and timeouts; transport errors
  surface as the same `NetworkError`.
- Behaviour the switch had to re-establish explicitly, each pinned by
  `tests/test_http.py`:
  - `download()` writes its target **only on a final 200**. Any other status,
    including another 2xx such as 204/206, leaves the file untouched —
    `fetch_files` installs whatever lands in its temp directory, and an empty
    body would overwrite a real settings file.
  - A configured `http_proxy` / `https_proxy` is **merged with** the
    `HTTP_PROXY` / `HTTPS_PROXY` environment per scheme instead of replacing it:
    `urllib`'s `ProxyHandler` only builds a handler for the schemes it is given,
    so an explicit `http_proxy` alone would otherwise send `api.github.com`
    direct.
  - Requests send `Accept-Encoding: identity`, and a body that still declares a
    `Content-Encoding` is refused as a `NetworkError` rather than written out.
  - `Authorization` is sent as an unredirected header, so a redirect to another
    host cannot replay the token.
  - `urllib` does not redirect PATCH/DELETE, so a 3xx on a write surfaces as an
    `UnexpectedError` instead of a retried (possibly duplicated) write.
- Removed `dependencies.json` — the package now has zero Package Control
  runtime dependencies. Certificate verification uses the host trust store,
  which is what `requests` fell back to here too, since `certifi` is not a
  channel dependency and was never installed.
- Added `.python-version` (`3.8`) so the plugin runs on the modern Python
  host on all 4050+ builds (3.8 on 4050–4204, the 3.8-compatible 3.14 host on
  4205+) instead of falling back to the legacy 3.3 host.
- **Sublime Text 3 is no longer supported.** It has in fact been unloadable
  since v4.2.0 (`auto_sync.py` uses `dataclasses`, Python 3.7+, while ST3
  only ships the 3.3 host); the README now states the real requirement —
  Sublime Text 4, build 4050 or newer — and the Package Control channel
  entry declares `"python_versions": ["3.8"]` so ST3 users are not offered
  the package.
- **Fixed: concurrent Upload + auto-sync could create two Gists.** When
  `gist_id` was empty (e.g. right after `Delete` then `Upload`), a manual
  `Upload` and the background auto-sync loop both saw it empty and each
  created a Gist, leaving an orphan. Gist creation is now single-flight: a
  module-level lock makes the second actor re-read `gist_id` inside the lock
  and update the Gist the first one created instead of forking a second.

## v4.2.1 — packaging: exclude dev/test files from distribution

Added `.gitattributes` so Package Control's per-tag `git archive` no longer
ships `tests/`, `.github/`, Pipfile, and other dev artifacts into users'
`Packages/SyncSettingsReborn/`. No runtime behaviour change.

## v4.2.0 — command redesign and hardening

- **`Upload` is now create-or-update.** When `gist_id` is empty it creates a new
  Gist and saves the id automatically; when `gist_id` is set it updates that
  Gist. No description prompt and no "backfill gist_id?" question.
- Removed `Create and Upload` (superseded by the new `Upload`).
- Removed `Delete and Create`. Resetting is now a two-step, prompt-free flow:
  `Delete` (clears the saved `gist_id`) followed by `Upload` (creates a fresh
  Gist).
- Added a standalone `Delete` command that deletes the remote Gist and clears
  the saved `gist_id` / version info.
- **Never upload a GitHub token.** The rule is applied to file *contents*, not
  to a hard-coded file name: any file carrying a GitHub token (`ghp_` / `gho_` /
  `ghu_` / `ghs_` / `ghr_` / `github_pat_`) is skipped when uploading to the
  Gist — the only direction where a secret would leave your machine. Previously
  only *this* plugin's settings file was protected by name, so leftovers from
  removed plugins (e.g. `PackageSync.sublime-settings`) could leak a token into a
  gist, which made GitHub secret scanning silently revoke it.
- Removed the hard-coded `SyncSettingsReborn.sublime-settings` exclusion. No file
  name is special-cased any more, so a config file is judged only by the
  exclude/include patterns and, for uploads, by content. As a result the offline
  zip now contains that file too (`gist_id` and your preferences are backed up).
- Restore is now the exact inverse of backup: files are written back as they
  arrive, with no token filtering. Filtering there would mean a file could be
  backed up but never restored, and the upload rule already guarantees the Gist
  cannot carry a token.
- Fixed `Backup Package List` overwriting a full zip backup: it now always writes
  a separate `-packages` file (`~/SyncSettingsReborn-packages.zip`).
- **Breaking default:** `skip_uninstalled_packages` now defaults to `true`
  (it was effectively off). ANY top-level `User/` subfolder whose name does not
  match an installed package is skipped — including folders you created
  yourself (e.g. `Snippets/`). Set it to `false` if you keep personal resources
  in such folders.
- Fixed `skip_uninstalled_packages` being ignored by the zip backup / package
  list commands; it now applies to every backup path, not just the Gist upload.
- Fixed `skip_uninstalled_packages` silently doing nothing: `list_packages()`
  raises when called from the upload/backup worker thread, which degraded to
  "keep every file". The package list is now snapshotted on the main thread and
  passed to the worker.
- **Implemented `auto_upgrade` auto-sync** (previously a dead setting). When
  `true`, Sublime pulls the latest Gist on startup (the original behaviour) and a
  background daemon thread keeps the mirror in sync on a timer
  (`auto_sync_interval`, default 5 minutes). The loop is a **three-way merge**
  against the snapshot last synced, not a blind pull-everything / push-everything:
  only files that changed remotely are pulled, only files that changed locally
  are pushed, so a change made only on this machine is never clobbered by a
  remote change to a different file.
  - **Conflict handling:** when the *same* file was changed both locally and
    remotely since the last sync, it is flagged (logged + status message), the
    local version is backed up under `~/.sync_settings_reborn/conflicts/<ts>/`
    so the edit is recoverable, and the remote version wins (the gist stays the
    shared source of truth) — instead of one side being silently lost.
  - Requires `access_token`; pulls also need a `gist_id` (pushes create one if
    missing).
  - The three-way merge baseline (per-file content hashes and the last gist
    revision) is **persisted in `sync.json`**, so edits made while Sublime was
    closed survive a restart: previously the baseline was seeded from current
    disk contents on every start, which made offline edits invisible and let a
    remote version overwrite them without a conflict backup.
  - **File deletions propagate both ways.** A file deleted locally is removed
    from the Gist (`{"file": null}`), and a file deleted in the Gist is removed
    locally. Files merely filtered out by token/exclude rules (still on disk)
    are never mistaken for deletions. Local delete requests are path-traversal
    guarded.
  - When no `gist_id` exists, the first push creates a Gist containing the
    **full mirror** of current files, not just files touched since startup.
  - After a remote pull the local tree is **re-collected before pushing**, so
    Package Control's preserve-merge result is what gets uploaded (previously
    pre-pull contents could be pushed back, causing permanent churn).
  - `auto_sync_interval` values ≤ 0 or < 1 minute are ignored (minimum 1
    minute), instead of producing a zero/negative-timeout busy loop.
  - A deleted/404 Gist pauses auto-sync after showing a one-time dialog,
    instead of failing silently forever on every cycle.
  - Gist API calls carry a 30 s timeout, and startup sync never blocks
    Sublime's main thread.
  - Manual `Upload`/`Download` commands re-baseline auto-sync so their changes
    are not re-merged as conflicts.
- Removed the dead legacy `sync_version` helpers (`get_remote_version`,
  `show_update_dialog`, `upgrade`) — they were the original, never-wired-up
  `auto_upgrade` implementation (no caller, and clicking "update" only wrote the
  local record without actually downloading). The real auto-sync now lives in
  `auto_sync.py`. `get_local_version` / `update_config_file` are kept (still used
  to record the last-synced gist revision).

- **`Upload` now propagates local deletions to the Gist.** GitHub's gist PATCH
  is a merge: files absent from the payload are kept on the Gist, so deleting a
  config locally used to leave a stale copy in the Gist. `Upload` now fetches the
  current Gist file list and sends `null` for any remote file that is genuinely
  gone from disk (filtered-out files that still exist locally are left untouched,
  so only real deletions propagate).
- **Auto-pull installs missing packages.** After an auto-sync pull, packages
  present on the Gist but missing locally are best-effort installed via Package
  Control's `advanced_install_package` command (wrapped so a failure can never
  block the file restore). A fresh machine now converges to the same plugin set
  without a manual `Download`.
- **Auto-sync fetches content via `raw_url`, not the API inline field.** GitHub's
  REST API truncates a file's inline `content` past ~1 MiB (`truncated: true`) and
  omits it for files pushed through git, so the background merge could never pull
  large or git-imported files. It now downloads each file's `raw_url` (the same
  path the manual `Download` command already used), so files of any size restore
  intact. The gist listing is still taken from the API for the file map and
  revision check.
- **Manual `Download` honours the configured proxy.** `fetch_files` now passes
  the proxy from `Gist.from_settings().proxies` when fetching raw file bytes,
  matching the gist API client and auto-sync (previously only the listing used
  the proxy while the file bytes ignored it).
- **Canonical file keys for nested / special-character names.** Gist filenames
  are mapped through `path.encode(path.decode(name))` in auto-sync, manual
  `Download`, and the manual-Download baseline, so a file created by another tool
  with a literal path separator (e.g. `sub/C.sublime-settings`) matches this
  plugin's percent-encoded key space (`sub%2FC.sublime-settings`) instead of
  being re-pulled every cycle.
- **Gists from other tools converge instead of forking twins.** Writes (auto-sync
  and `Upload`) now map every gist file to its canonical internal key via a shared
  `name_map`. A file stored under a foreign name is renamed in place (the gist
  rename API), and any leftover duplicate copy is removed (`null`); a local
  deletion nulls every real remote name the gist carries. So an externally created
  gist re-baselines correctly after the next push instead of accumulating twin
  files.
- **Bounded per-key retry for content that cannot be fetched.** A merge records
  keys whose `raw_url` could not be fetched (network error / non-200) and, on an
  unchanged-revision idle poll, re-requests only those `raw_url`s (bounded by
  `PENDING_MAX_ATTEMPTS = 5`) instead of re-downloading the whole gist. A
  permanently failing key is dropped with a loud error while keeping its last
  synced baseline — it is never mistaken for a remote deletion; a new gist
  revision rearms the retry budget. The pending set is persisted in `sync.json`.
- **Files are no longer uploaded corrupted.** `get_content` used `decode('utf-8',
  errors='ignore')`, which silently dropped bytes from a non-UTF-8 file and
  pushed the damaged copy to every machine. It now decodes strictly and skips
  (with a warning) any file that is not valid UTF-8, like token files.

## v4.1.0 — PackageSync-style sync

Added sync capabilities alongside the existing GitHub Gist backend:

- Offline **Zip backup / restore** (`Backup to Zip`, `Restore from Zip`) — a
  portable backup of `Packages/User`, never including your `access_token`.
- **Backup Package List** — back up only `Package Control.sublime-settings`.
- **Sync Online** commands (`Define Folder` / `Push` / `Pull`) for a Dropbox /
  Google Drive / OneDrive based workflow with no background process.
- New settings: `prompt_for_location` / `backup_path`, `preserve_packages`
  (default true — merges `installed_packages` on restore instead of overwriting),
  `ignore_dirs`, `online_sync_folder`.

## v4.0.0 — SyncSettingsReborn

Maintained revival of the unmaintained original package, published under a new
name so it can coexist with (and replace) the dead package on Package Control.

- Renamed package to `SyncSettingsReborn` (commands `sync_settings_reborn_*`,
  settings file `SyncSettingsReborn.sublime-settings`).
- Automatic migration of `gist_id` / `access_token` / proxy / include-exclude
  settings from the old `SyncSettings.sublime-settings` on first run.
- Fixed plugin failing to load: `requests` transitive dependencies
  (urllib3, idna, certifi, charset_normalizer) are now declared in
  `dependencies.json`.
- Fixed silent failures / empty logs: errors are written to
  `~/.sync_settings_reborn/sync.log` and surfaced to the user; unified network
  error handling (SSL / timeout / proxy / server errors).
- Fixed crash when `excluded_files` / `included_files` were set to a string
  instead of a list (#200).

## v3.2.0

This version deprecates sublime text v2 and includes minor improvements.

https://github.com/mfuentesg/SyncSettings/issues/169
https://github.com/mfuentesg/SyncSettings/pull/192
https://github.com/mfuentesg/SyncSettings/issues/188

## v3.1.0

This version includes some minor fixes.

- https://github.com/mfuentesg/SyncSettings/issues/185

## v3.0.8

This version includes some minor fixes.

- https://github.com/mfuentesg/SyncSettings/issues/165
- https://github.com/mfuentesg/SyncSettings/issues/164

## v3.0.7

This version includes encoding fixes, and minor improvements.

- https://github.com/mfuentesg/SyncSettings/issues/144
- https://github.com/mfuentesg/SyncSettings/issues/119

## v3.0.3

This version includes encoding issues on download command, and minor improvements.

- https://github.com/mfuentesg/SyncSettings/issues/115
- https://github.com/mfuentesg/SyncSettings/issues/113

## v3.0.2

Fix error when installed_packages key does not exists

- https://github.com/mfuentesg/SyncSettings/issues/112 

## v3.0.1

Are you behind a restricted network?

This version is for you, `http_proxy` and `https_proxy` properties were added to avoid those annoying network restrictions.

https://github.com/mfuentesg/SyncSettings/issues/87

## v3.0.0

I am happy to announce a new version of SyncSettingsReborn.
This version includes a lot of improvements and bug fixes

In the previous version of `SyncSettingsReborn`, all files are replaced automatically once completed the download,
causing errors like infinite sublime text alerts when a dependency is not installed in your computer.

In this version, SyncSettingsReborn will use `Package Control` commands, to ensure the installation of your packages,
before to update `Preferences.sublime-settings` and `Package Control.sublime-settings` files.


Improvements:

- Add `unix shell style` for `excluded_files` and `included_files` options, using `fnmatch` library (wildcard).
- Improve error messages due to connection error, or insufficient token permissions.
- Delete custom logger by builtin logger
- Delete stylized popups by status bar messages
- Add ability to retrieve a gist without an access token
- Better documentation

Bug fixes:

- Exclude `SyncSettingsReborn.sublime-settings` on sync (https://github.com/mfuentesg/SyncSettings/issues/80)
- Fix files priority (https://github.com/mfuentesg/SyncSettings/issues/82)
- Colour scheme needs to load first (https://github.com/mfuentesg/SyncSettings/issues/90)
- Fix utf-8 error (https://github.com/mfuentesg/SyncSettings/issues/83)


## 2.4.4

Solved issues:
- Not able to exclude arbitrary files (https://github.com/mfuentesg/SyncSettings/issues/51)


## 2.4.3

Solved issues:
- Syncing not works (https://github.com/mfuentesg/SyncSettings/issues/67)

## 2.4.2

Solved issues:
- Fails with Dev Channel, Build 3125 (https://github.com/mfuentesg/SyncSettings/issues/63)

## 2.4.0

- Rename cache file from `.sync_settings_reborn_cache` to `.sync-settings.cache` (~/.sync_settings_reborn_reborn_cache)
- New Command `SyncSettingsReborn: Edit User Settings` by @JohaWeber
- Bug Logging was improved

Issues:
- Remove SyncSettings references from download process (https://github.com/mfuentesg/SyncSettings/issues/50)
- Download doesn't work and clears Gist ID (https://github.com/mfuentesg/SyncSettings/issues/46)
- downloading append a newline in configfile (https://github.com/mfuentesg/SyncSettings/issues/45)
- sync_settings_reborn_cache links to wrong directory (https://github.com/mfuentesg/SyncSettings/issues/42)

## 2.3.1

* Fix encoding bug
* Allow special chars like 'ç'

## 2.3.0

* Check if your settings are up to date on startup
* Add PopUp support to ST Build 3070 or higher
* Auto upgrade your Settings if the auto_upgrade option is enabled
* auto_upgrade option was added
* Minor Enhancements
* MIT license was added


## 2.2.6

* included_files option was added
* Minor Fixes


## 2.2.5

* Minor Fixes

## 2.2.4

* Add Delete and Create Command
* Add Delete Command
* Enhancement on Excluded files filter
* Refactoring
* Minor Fixes

## 2.2.2

* Add Support to Python 2.7


## 2.2.1

This version has some bug fixes

* Restore base encoding to read the files
* Minor fixes

## 2.2.0

This version has some bug fixes

* Code 422 - Validation Failed
* Re-order file structure
* Enhance testing

##2.1.1

This version has some bug fixes

* Add base encoding to read the files
* When a file not exists in other host this file is not created
* Function enhancements

## 2.0.0

This version has some bug fixes found and new features

* All files inside on User folder will be included
* Enhancements on the excluded files list
  - Exclude by filename
  - Exclude by extension
  - Exclude by folder
* Show progress indicator on the status bar
* Error messages more descriptive
* Minor bug fixes

## 1.2.0

This version executes each command as a thread, allowing that the application is not lock.

* Added threading support

## 1.1.0

* Now your operations and errors are saved
* New commands added
* Custom Exception Added
* Fix minor errors

## 1.0.1

This version has some bug fixes found

* When files do not exist
* Remove the file repeated in the list of excluded files
* Include Default <platform>.sublime-keymap files and the User Settings
* Include Changelog file
