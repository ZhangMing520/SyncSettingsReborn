# SyncSettingsReborn（社区维护版）

[English](README.md) | 简体中文

> 本插件是原版 **Sync Settings** 的社区维护续作——原插件已停止更新（见下方原作者的
> UNMAINTAINED 声明）。本版本以 **SyncSettingsReborn** 之名发布在 Package Control 上，
> 不会与已停更的旧包冲突。首次运行时会自动迁移旧版的 `gist_id` / `access_token` 等配置。

> ## 原版声明：UNMAINTAINED（已停止维护）
>
> 很遗憾，我已无法继续开发 SyncSettings。它仍会保留在 Package Control 仓库中，但不会再有新的改动。
>
> SyncSettings 是一个非常有挑战性的项目，它教会了我如何与社区相处，也让我对开源世界有了更多了解。
> 感谢大家一直以来的支持，希望 SyncSettings 能继续像陪伴我一样陪伴你们。
>
> 祝编码愉快！
>
> Marcelo

---

# SyncSettingsReborn

[![Package Control 安装量](https://img.shields.io/packagecontrol/dt/SyncSettingsReborn.svg?maxAge=2592000)](https://packagecontrol.io/packages/SyncSettingsReborn)
[![license](https://img.shields.io/github/license/ZhangMing520/SyncSettingsReborn.svg?maxAge=2592000)](https://github.com/ZhangMing520/SyncSettingsReborn/blob/master/LICENSE)
[![最新版本](https://img.shields.io/github/release/ZhangMing520/SyncSettingsReborn.svg)](https://github.com/ZhangMing520/SyncSettingsReborn/releases)
[![CI](https://github.com/ZhangMing520/SyncSettingsReborn/actions/workflows/app.yml/badge.svg)](https://github.com/ZhangMing520/SyncSettingsReborn/actions/workflows/app.yml)

<br />
<br />

借助 [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn)，你可以在多台设备之间同步
[Sublime Text](http://sublimetext.com/) 的配置，并保持持续更新。

插件以 GitHub Gist 作为云端后端，为你提供可靠、跨平台且安全的备份方案。

请按照下面的步骤开始使用 [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn)。

> 支持 Windows、Linux、macOS，要求 **Sublime Text 4（build 4050 或更新）**。

## 同步方式

除了默认的 **GitHub Gist** 云端后端，Reborn 还提供了类 PackageSync 的同步方式：

- **离线 Zip 备份 / 恢复** —— `SyncSettingsReborn: Backup to Zip` 和 `Restore from Zip`
  会把 `Packages/User` 打包成一个可移植的压缩包。zip 属于本地离线导出，**不会过滤敏感信息**，
  请妥善保管该文件。恢复是备份的完全逆操作：压缩包里有什么就写回什么。
- **仅备份插件列表** —— 只备份已安装插件的清单。
- **在线同步（Dropbox / Google Drive / OneDrive）** —— 通过 `Define Folder`、`Push`、`Pull`
  命令把 zip 推进/拉出任意网盘同步文件夹，无需常驻后台进程。

恢复时，`preserve_packages`（默认 `true`）会把压缩包中的 `installed_packages` 与本地清单**合并**
而非覆盖，因此只在本机安装的插件会被保留。

## 快速开始

1. 执行 `Package Control: Install Package`，搜索并安装
   [SyncSettingsReborn](https://packagecontrol.io/packages/SyncSettingsReborn)
2. 执行 `SyncSettingsReborn: Edit User Settings`
3. **如果**你已经有一个 gist：
   1. 复制 `gist id`（即 `https://gist.github.com/<用户名>/<gist id>` 中的 id），填入配置文件的
      `gist_id` 字段
   2. 执行 `SyncSettingsReborn: Download` 拉取你的备份
4. **否则**：
   1. 在[这里](https://github.com/settings/tokens/new)创建一个勾选 `gist` 权限的 access token
   2. 把 token 填入配置文件的 `access_token` 字段
   3. 执行 `SyncSettingsReborn: Upload`（首次运行会自动创建新 Gist，之后每次运行都是更新）

### 配置文件格式

配置文件为 JSON 格式，最简示例如下：

```json
{
	"access_token": "xxxxxxxxxxxxxxxxxxxxxxxxx",
	"gist_id": "xxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

## 配置项

插件默认操作 Sublime Text 的用户配置目录（例如
`/Users/<用户名>/Library/Application Support/Sublime Text/Packages/User`），
`excluded_files` 和 `included_files` 的匹配路径都相对于该目录。

| 名称 | 类型 | 说明 |
|---|---|---|
| `access_token` | `string` | 赋予插件对你 gist 的写权限（编辑、删除、创建）。*仅下载备份时不需要此项。* |
| `gist_id` | `string` | 备份在 [gist.github.com](https://gist.github.com/) 上的唯一标识。 |
| `auto_upgrade` | `boolean` | 为 `true` 时自动保持同步：Sublime 启动时拉取最新 gist，后台循环还会定时拉取远端变更并推送本地变更（变更即上传）。同步采用三方合并（每次只传输有变化的文件）；若同一文件在两台机器上同时被修改，会被判定为冲突——本地副本备份到 `~/.sync_settings_reborn/conflicts/<时间戳>/`，以 gist 版本为准，不会静默丢失数据。需要 `access_token`（拉取还需要 `gist_id`；推送时若缺失会自动创建）。默认 `false`。 |
| `auto_sync_interval` | `number` | 后台自动同步的轮询间隔，单位为**分钟**，仅在 `auto_upgrade` 为 `true` 时生效。默认 `5`。 |
| `http_proxy` | `string` | 请求使用的 HTTP 代理服务器。 |
| `https_proxy` | `string` | 请求使用的 HTTPS 代理服务器。 |
| `excluded_files` | `[]string` | 黑名单：所有匹配给定模式的文件在同步时都会被忽略。 |
| `included_files` | `[]string` | 白名单：所有匹配给定模式的文件都会参与同步，即使它同时命中了 `excluded_files`。 |
| `skip_uninstalled_packages` | `boolean` | 为 `true` 时，上传会跳过属于**当前未安装插件**的文件——覆盖已卸载插件遗留的 `<插件名>.sublime-settings`/`.sublime-keymap` 等文件以及 `<插件名>/` 数据目录。全局文件（`Preferences`、`Package Control`、`SyncSettingsReborn`）和直接位于 `User` 根目录的普通散文件始终保留。默认 `true`。 |
| `backup_path` | `string` | `Backup to Zip`（及插件列表备份）的默认输出位置。留空时回退到 `~/SyncSettingsReborn.zip`。插件列表备份固定使用带 `-packages` 后缀的独立文件名，绝不会覆盖完整备份。 |
| `prompt_for_location` | `boolean` | 为 `true` 时，备份/恢复命令执行前会先询问路径；为 `false` 时直接使用 `backup_path`。默认 `true`。 |
| `preserve_packages` | `boolean` | 恢复时将压缩包中的 `installed_packages` 与本地清单合并而非覆盖，保留只在本机安装的插件。默认 `true`。 |
| `ignore_dirs` | `[]string` | 收集文件时按 fnmatch 模式整体跳过的目录名（例如 `["node_modules"]`）。 |
| `online_sync_folder` | `string` | `Sync Online` 系列命令读写设置 zip 时使用的文件夹（通常是 Dropbox / Google Drive 等网盘同步目录）。在执行 `Sync Online - Define Folder` 之前留空。 |

> 注意：`excluded_files` 和 `included_files` 使用 [Unix shell 风格](https://tldp.org/LDP/GNU-Linux-Tools-Summary/html/x11655.htm)的通配符模式。

> **Gist 大小限制：** GitHub gist REST API 的内联 `content` 字段每个文件最多返回约 1 MiB
> 内容，更大的文件会被标记 `truncated: true`（内容不完整）。但这**只影响内联字段**——
> `raw_url` 端点（git 后端）始终返回完整文件，且上传不会被截断（发送多少 GitHub 就存多少）。
> 手动 `Download` 命令和后台自动同步都通过 `raw_url` 获取内容，因此任何大小的文件都能完整恢复
> （包括通过 git 推送、内联 `content` 为空的文件）。离线 zip 备份不受此限制影响。

## 命令一览

### GitHub Gist（云端同步）

| 命令 | 说明 |
|---|---|
| **SyncSettingsReborn: Upload** | 首次（`gist_id` 为空时）创建 Gist，之后每次运行都更新它。无任何弹窗提示——新 gist id 会自动保存。 |
| **SyncSettingsReborn: Download** | 根据设置中的 `gist_id`，把最新备份恢复到 `Packages/User`。 |
| **SyncSettingsReborn: Delete** | 删除远端 Gist（**不可撤销**），并清空已保存的 `gist_id`。 |

> **重置备份：** 先执行 `SyncSettingsReborn: Delete`（清空已保存的 `gist_id`），
> 再执行 `SyncSettingsReborn: Upload`——Upload 会一键用当前文件创建一个全新的 Gist，
> 没有描述弹窗，也不会询问 `gist_id`。

### 离线 Zip / 插件列表

| 命令 | 说明 |
|---|---|
| **SyncSettingsReborn: Backup to Zip** | 把 `Packages/User` 打包为可移植 zip（无需网络）。 |
| **SyncSettingsReborn: Restore from Zip** | 从 zip 压缩包恢复 `Packages/User`。 |
| **SyncSettingsReborn: Backup Package List** | 仅导出已安装插件清单（`installed_packages`）。 |

### 在线同步（Dropbox / Google Drive / OneDrive）

| 命令 | 说明 |
|---|---|
| **SyncSettingsReborn: Sync Online - Define Folder** | 选择一个网盘同步文件夹作为同步位置。 |
| **SyncSettingsReborn: Sync Online - Push** | 把设置 zip 推送到该文件夹。 |
| **SyncSettingsReborn: Sync Online - Pull** | 从该文件夹拉取 zip 并恢复。 |

### 实用工具

| 命令 | 说明 |
|---|---|
| **SyncSettingsReborn: Show Logs** | 打开 `SyncSettingsReborn` 日志文件，用于排查问题。 |
| **SyncSettingsReborn: Edit User Settings** | 打开插件的用户配置文件。 |

## 问题反馈

如果你遇到报错或异常行为，请提交 [new issue](https://github.com/ZhangMing520/SyncSettingsReborn/issues/new)，
并附上 `SyncSettingsReborn: Show Logs` 命令提供的日志内容。

## 参与开发

欢迎随时为这个项目贡献代码。

本插件**没有任何运行时第三方依赖**，所有网络请求均使用 Python 标准库完成。
开发使用仓库自带的本地虚拟环境（Python 3.14）：

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

**运行测试**

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m flake8 sync_settings_reborn tests
```

## 许可证

SyncSettingsReborn 与所有源代码均基于 MIT 许可证发布。

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
