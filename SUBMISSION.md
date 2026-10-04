# Submitting "SyncSettingsReborn" to Package Control

The original `SyncSettings` package is no longer maintained
(https://github.com/mfuentesg/SyncSettings). This fork revives it under a new
name so it can be listed independently on Package Control without colliding
with the dead package.

## How Package Control distribution works

You do **not** upload a zip. You register your **Git repository** in the
official channel repository. Package Control then pulls releases from your tags.

## Step 1 — Make sure your repo is ready

- This repo must be public on GitHub.
- It should have a Git **tag** for the release you want to publish
  (e.g. `v4.0.0`). Package Control reads tags.
- The `details` URL below must point to **your** repository.

If you want to rename the GitHub repo to `SyncSettingsReborn`, do it before
step 2 and update the `details` URL accordingly.

## Step 2 — Submit to `package_control_channel`

Two options:

**Option A — web form (easiest)**
Open https://packagecontrol.io/submit and enter your repository URL:

```
https://github.com/ZhangMing520/SyncSettingsReborn
```

**Option B — pull request (recommended, fully controlled)**

The channel uses a **centralized** `repository/<letter>.json` schema — every
package is one entry in a single `packages` array, NOT a standalone
`packages/<Letter>/<Name>.json` file (that old layout is no longer accepted; a
PR using it gets rejected as "invalid").

1. Fork https://github.com/sublimehq/package_control_channel
   (the repo formerly lived under `wbond/` and now redirects to `sublimehq/`).
2. Edit the existing `repository/s.json` and insert one entry into the
   `packages` array, in alphabetical order by `name` (SyncSettingsReborn goes
   after "Syncrow", before "Synesthesia"):

```json
{
    "name": "SyncSettingsReborn",
    "details": "https://github.com/ZhangMing520/SyncSettingsReborn",
    "labels": ["settings", "utilities"],
    "releases": [
        {
            "sublime_text": ">=4050",
            "python_versions": ["3.8"],
            "tags": true
        }
    ]
}
```

`python_versions: ["3.8"]` (plus `sublime_text: ">=4050"`) is **required**: the
plugin no longer loads on Sublime Text 3. ST3 only ships the Python 3.3 host,
while `auto_sync.py` needs 3.7+ (`dataclasses`) and the package ships
`.python-version` (`3.8`). Without this restriction Package Control would
offer the release to ST3 users, where it fails to import.

3. Open a PR against `sublimehq/package_control_channel`. The diff should be a
   single change to `repository/s.json` (the entry added) — nothing else.

Use the PR template (checklist + package description) when opening the PR; a
bare JSON change is rejected as "invalid".

## Result

Once merged, users can install it via:
`Command Palette → Package Control: Install Package → SyncSettingsReborn`.

Future updates: just push a new tag (e.g. `v4.0.1`) to your repo; Package
Control picks it up automatically. No need to re-submit.

## Notes

- The package install folder name is taken from the channel `name`
  ("SyncSettingsReborn"), so the installed path is
  `Packages/SyncSettingsReborn/`.
- The package has **no Package Control runtime dependencies** (no
  `dependencies.json`): all HTTP traffic uses a standard-library
  (`urllib.request`) layer in `sync_settings_reborn/libs/http.py`. This
  avoids the channel's `requests` dependency, which is pinned to 2.15.1
  (2017) and fails to import on Sublime's current Python 3.14 host.
- `.python-version` (`3.8`) selects the modern Python host and must ship in
  the tag archive (it is not `export-ignore`d).
- The settings file was renamed to `SyncSettingsReborn.sublime-settings` and
  old config is migrated automatically on first run.
