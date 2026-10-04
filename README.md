# SyncSettingsReborn (maintained fork)

English | [简体中文](README.zh-CN.md)

> This is a community-maintained revival of the original **Sync Settings**
> plugin, which is no longer updated (see the original author's UNMAINTAINED
> note below). It is published on Package Control as **SyncSettingsReborn**
> so it does not collide with the dead package. Old `gist_id` / `access_token`
> settings are migrated automatically on first run.

# UNMAINTAINED

Sadly, I won't able to continue with the development of SyncSettings, it will be available on package control registry but won't receive new changes.

SyncSettings has been a very challenging project and it tought me how to cope with a community and learn more about the open source world.
Thanks for all the support during this time and I hope that SyncSttings remains being so useful like it was for me.

Happy Coding!

Marcelo

---

# SyncSettingsReborn
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-6-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

[![Package Control installs](https://img.shields.io/packagecontrol/dt/SyncSettingsReborn.svg?maxAge=2592000)](https://packagecontrol.io/packages/SyncSettingsReborn)
[![license](https://img.shields.io/github/license/ZhangMing520/SyncSettingsReborn.svg?maxAge=2592000)](https://github.com/ZhangMing520/SyncSettingsReborn/blob/master/LICENSE)
[![latest release](https://img.shields.io/github/release/ZhangMing520/SyncSettingsReborn.svg)](https://github.com/ZhangMing520/SyncSettingsReborn/releases)
[![CI](https://github.com/ZhangMing520/SyncSettingsReborn/actions/workflows/app.yml/badge.svg)](https://github.com/ZhangMing520/SyncSettingsReborn/actions/workflows/app.yml)

<br />

With [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn), you are able to synchronize your [Sublime Text](http://sublimetext.com/) settings among multiple devices, and keep them updated.

Being powered by GitHub-Gists, [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn) provides you a reliable cross-platform solution to keep your backups secure.

Please, follow the steps below to getting started with [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn).

> [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn) works on Windows, Linux, macOS and requires **Sublime Text 4 (build 4050 or newer)**.

## Sync methods

Besides the default **GitHub Gist** backend, Reborn also offers PackageSync-style ways to sync:

- **Offline Zip backup / restore** — `SyncSettingsReborn: Backup to Zip` and `Restore from Zip` produce a portable archive of your `Packages/User`. The zip is a local, offline export, so it is not filtered for secrets — treat the file as sensitive. Restore is the exact inverse of backup: whatever the archive contains is written back.
- **Backup Package List** — back up only the installed package list.
- **Sync Online (Dropbox / Google Drive / OneDrive)** — `Define Folder`, `Push`, `Pull` commands move a zip in/out of any cloud-synced folder, no background process required.

On restore, `preserve_packages` (default `true`) merges the incoming `installed_packages` with your local list instead of overwriting it, so packages only on this machine are kept.


## Getting Started

1. Run `Package Control: Install Package` command, and looks for [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn)
2. Run `SyncSettingsReborn: Edit User Settings`
3. **if** *Do you already have a gist?*
    1. Copy `gist id` and put it in config file (`https://gist.github.com/<username>/<gist id>`) (`gist_id` property)
    2. Run `SyncSettingsReborn: Download` command to retrieve your backup.
4. **else**
    1. Create an access token [here](https://github.com/settings/tokens/new) with `gist` scope checked.
    2. Put the token in the config file (`access_token` property)
    3. Run `SyncSettingsReborn: Upload` command (it creates a new Gist the first time, then updates it on every later run)
    
### File Format

Please note - the config file uses the JSON format. A simplified example may look like the following.

```
{
	"access_token": "xxxxxxxxxxxxxxxxxxxxxxxxx",
	"gist_id": "xxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

## Options

By default, this plugin operates over [Sublime Text](https://www.sublimetext.com) packages folder (i.e `/Users/<my_user>/Library/Application Support/Sublime Text/Packages/User`), which means, `excluded_files` and `included_files` will look for files inside that folder.

| name | type | description |
|---|---|---|
| `access_token`  | `string` | Brings write permission to [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn) over your gists (edit, delete and create). *(This option is not required, if you only want to download your backups)* | 
| `gist_id`  | `string` | Identifier of your backup on [gist.github.com](https://gist.github.com). |
| `auto_upgrade`  | `boolean` | If `true`, settings are kept in sync with the gist automatically: Sublime pulls the latest gist on startup, and a background loop both pulls remote changes and pushes local changes (upload-on-change) on a timer. The sync is a three-way merge (only changed files move each way), and a file edited on two machines at once is reported as a conflict — the local copy is backed up to `~/.sync_settings_reborn/conflicts/<timestamp>/` and the gist version wins, so nothing is silently lost. Requires `access_token` (and a `gist_id` for pulls; pushes create one if missing). Default `false`. |
| `auto_sync_interval`  | `number` | Background auto-sync poll interval in **minutes**, used only when `auto_upgrade` is `true`. Default `5`. |
| `http_proxy`  | `string` | An HTTP proxy server to use for requests. |
| `https_proxy`  | `string` | An HTTPS proxy server to use for requests. |
| `excluded_files`  | `[]string` | In simple words, this option is a black list. Which means, every file that match with the defined pattern, will be ignored on sync. |
| `included_files`  | `[]string` | In simple words, this option is a white list. Which means, every file that match with the defined pattern, will be included on sync, even if it was included on `excluded_files` option. |
| `skip_uninstalled_packages`  | `boolean` | If `true`, files belonging to a package that is **not currently installed** are skipped on upload — covers leftover `<Package>.sublime-settings`/`.sublime-keymap`/etc. and `<Package>/` data folders left behind by removed plugins. Global files (`Preferences`, `Package Control`, `SyncSettingsReborn`) and generic loose files directly in `User` are always kept. Default `true`. |
| `backup_path`  | `string` | Default output location for `Backup to Zip` (and the package-list backup). Empty falls back to `~/SyncSettingsReborn.zip`. The package-list backup always uses a separate `-packages` filename, so it can never overwrite a full backup. |
| `prompt_for_location`  | `boolean` | When `true`, the backup/restore commands ask for a path before writing/reading; when `false`, they use `backup_path`. Default `true`. |
| `preserve_packages`  | `boolean` | On restore, merge the incoming `installed_packages` with the local list instead of overwriting it, so packages only on this machine are kept. Default `true`. |
| `ignore_dirs`  | `[]string` | fnmatch patterns for whole directory names to skip when collecting files (e.g. `["node_modules"]`). |
| `online_sync_folder`  | `string` | Folder (typically a cloud-synced directory like Dropbox / Google Drive) used by the `Sync Online` commands to read/write the settings zip. Empty until you run `Sync Online - Define Folder`. |

> Note: `excluded_files` and `included_files` are patterns defined as [unix shell style](https://tldp.org/LDP/GNU-Linux-Tools-Summary/html/x11655.htm).

> **Gist size limit:** GitHub's gist REST API returns at most ~1 MiB of content per file in the inline `content` field and marks larger files `truncated: true` (only partial content). This **only affects the inline field** — the `raw_url` endpoint (git backend) always serves the complete file, and uploads are never truncated (GitHub stores whatever you send). Both the manual `Download` command and background auto-sync fetch content via `raw_url`, so files of any size are restored intact (including files pushed via git, whose inline `content` is empty). The offline zip backup is unaffected.


## Commands

### GitHub Gist (cloud sync)

| command | description |
|---|---|
| **SyncSettingsReborn: Upload** | Creates a Gist the first time (when `gist_id` is empty) and updates it on every later run. No prompts — the new gist id is saved automatically. |
| **SyncSettingsReborn: Download** | Restores the latest backup from the `gist_id` in your settings into `Packages/User`. |
| **SyncSettingsReborn: Delete** | Deletes the remote Gist (this action is irreversible) and clears the saved `gist_id`. |

> **Resetting your backup:** Run `SyncSettingsReborn: Delete` (clears the saved
> `gist_id`), then `SyncSettingsReborn: Upload` — Upload will create a fresh Gist
> with your current files in one click, with no description prompt or `gist_id`
> question.

### Offline Zip / Package list

| command | description |
|---|---|
| **SyncSettingsReborn: Backup to Zip** | Pack `Packages/User` into a portable zip (no network required). |
| **SyncSettingsReborn: Restore from Zip** | Restore `Packages/User` from a zip archive. |
| **SyncSettingsReborn: Backup Package List** | Export only the installed-package list (`installed_packages`). |

### Sync Online (Dropbox / Google Drive / OneDrive)

| command | description |
|---|---|
| **SyncSettingsReborn: Sync Online - Define Folder** | Choose a cloud-synced folder to use as the sync location. |
| **SyncSettingsReborn: Sync Online - Push** | Push a settings zip into that folder. |
| **SyncSettingsReborn: Sync Online - Pull** | Pull and restore a settings zip from that folder. |

### Utilities

| command | description |
|---|---|
| **SyncSettingsReborn: Show Logs** | Open the `SyncSettingsReborn` log file for troubleshooting. |
| **SyncSettingsReborn: Edit User Settings** | Open the plugin's user settings file. |

## Contributors

Thank you for contribute to this project:
<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tr>
    <td align="center"><a href="https://ferronrsmith.github.io/"><img src="https://avatars2.githubusercontent.com/u/159764?v=4?s=100" width="100px;" alt=""/><br /><sub><b>Ferron H</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=ferronrsmith" title="Code">💻</a></td>
    <td align="center"><a href="https://github.com/tomahl"><img src="https://avatars0.githubusercontent.com/u/1665481?v=4?s=100" width="100px;" alt=""/><br /><sub><b>tomahl</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=tomahl" title="Code">💻</a></td>
    <td align="center"><a href="https://nachvorne.de"><img src="https://avatars3.githubusercontent.com/u/2073401?v=4?s=100" width="100px;" alt=""/><br /><sub><b>Johannes Weber</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=JohaWeber" title="Code">💻</a></td>
    <td align="center"><a href="https://mwilliammyers.com"><img src="https://avatars1.githubusercontent.com/u/2526129?v=4?s=100" width="100px;" alt=""/><br /><sub><b>William Myers</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=mwilliammyers" title="Code">💻</a></td>
    <td align="center"><a href="https://github.com/TheSecEng"><img src="https://avatars1.githubusercontent.com/u/32599364?v=4?s=100" width="100px;" alt=""/><br /><sub><b>Terminal</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=TheSecEng" title="Code">💻</a></td>
    <td align="center"><a href="https://github.com/mariohuq"><img src="https://avatars.githubusercontent.com/u/15021607?v=4?s=100" width="100px;" alt=""/><br /><sub><b>mariohuq</b></sub></a><br /><a href="https://github.com/mfuentesg/SyncSettings/commits?author=mariohuq" title="Code">💻</a></td>
  </tr>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

## Issues

If you are experimenting an error, or an unusual behavior. Please let me know,  creating a [new issue](https://github.com/ZhangMing520/SyncSettingsReborn/issues/new) appending the logs provided by the  `SyncSettingsReborn: Show logs` command.

## Development

You are welcome to contribute to this project, whenever you want.

The package has **no runtime dependencies** — all HTTP traffic uses the
Python standard library. Development uses the repository's local virtualenv
(Python 3.14):

```
$ python3.14 -m venv .venv
$ .venv/bin/python -m pip install -r requirements-dev.txt
```

**Run tests**

```
$ .venv/bin/python -m pytest tests/ -q
$ .venv/bin/python -m flake8 sync_settings_reborn tests
```


## License

SyncSettingsReborn is licensed under the MIT license along with all source code.

```
Copyright (c) since 2015, Marcelo Fuentes <marceloe.fuentes@gmail.com>.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
