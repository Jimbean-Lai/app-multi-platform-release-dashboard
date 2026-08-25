# CLI 参考手册

`appstore` 是项目命令行入口。

## 全局参数

| 参数 | 说明 |
| --- | --- |
| `--json` | 输出 JSON 格式（须在子命令前：`appstore --json publish ...`） |
| `--catalog` | 应用目录路径（默认 `apps/catalog.json`） |

## 子命令

### `platforms`

列出所有已注册的平台及其可用状态。

```
appstore platforms
```

输出：
| 平台 | display_name | 状态 |
| --- | --- | --- |
| google | Google Play | ready |
| apple | Apple | ready |
| xiaomi | 小米应用商店 | ready |
| oppo | OPPO 软件商店 | ready |
| vivo | vivo 应用商店 | ready |
| honor | 荣耀应用市场 | ready |
| huawei | 华为 AppGallery | ready |

### `apps`

列出应用目录中所有应用（按分类分组）。

```bash
appstore apps
```

### `validate`

校验凭证文件完整性。

```bash
appstore validate --credentials config/credentials.json
```

### `publish`

发布/更新应用到目标平台。

```bash
appstore publish [--app APP_ID] [--platform PLATFORM(S)] [--all]
                 [--credentials CREDENTIALS] [--dry-run]
                 [--version-name VN] [--version-code VC]
                 [--release-notes NOTES] [--online-time TIME]
                 [--apk-path PATH] [--aab-path PATH] [--track TRACK]
```

**参数**

| 参数 | 说明 |
| --- | --- |
| `--app` | 应用目录 ID（`appstore apps` 查看） |
| `--platform` | 目标平台，支持**逗号多选**如 `xiaomi,oppo,vivo`（排除 apple） |
| `--all` | 发布到 credentials 中所有已配平台（不含 apple） |
| `--credentials` | 凭证路径（默认 `config/credentials.json`） |
| `--dry-run` | 仅做本地校验，不发起真实 API 请求 |
| `--version-name` | 覆盖版本名（如 `1.0.1`） |
| `--version-code` | 覆盖 versionCode（数字） |
| `--release-notes` | 更新说明 |
| `--online-time` | 定时上线（`YYYY-MM-DD HH:MM` 本地时间）；留空=立即 |
| `--apk-path` | 显式指定 APK 路径 |
| `--aab-path` | 显式指定 AAB 路径 |
| `--track` | Google Play 轨道（production/beta/alpha/internal） |

**示例**

```bash
# dry-run 验证（始终推荐先跑）
appstore publish --app example-app --platform xiaomi,oppo --dry-run

# 多平台真实发布
appstore publish --app example-app --platform xiaomi,oppo,vivo

# 全部已配平台
appstore publish --app example-app --all

# 定时上线（9月1日10点自动上架）
appstore publish --app example-app --platform xiaomi --online-time "2026-09-01 10:00"

# JSON 输出
appstore --json publish --app example-app --all --dry-run
```

### `status`

查询应用在各平台的已上架版本和审核进度。

```bash
appstore status [--app APP_ID | --package PACKAGE]
                [--platform PLATFORM] [--credentials CREDENTIALS]
```

| 参数 | 说明 |
| --- | --- |
| `--app` | 应用目录 ID |
| `--package` | 直接指定包名（与 --app 二选一） |
| `--platform` | 指定平台（不填则查凭证中所有平台） |
| `--credentials` | 凭证路径 |

**示例**

```bash
# 查某 App 所有平台
appstore status --app example-app

# 查某包名
appstore status --package com.example.app

# 指定平台
appstore status --app example-app --platform google

# JSON 输出
appstore --json status --app example-app
```

### `web`

启动 Web 可视化看板（推荐日常使用）。

```bash
appstore web [--port PORT] [--host HOST]
             [--credentials CREDENTIALS] [--catalog CATALOG]
             [--open]
```

| 参数 | 说明 |
| --- | --- |
| `--port` | 监听端口（默认 8090） |
| `--host` | 监听地址（默认 127.0.0.1） |
| `--credentials` | 凭证路径 |
| `--catalog` | 应用目录路径 |
| `--open` | 自动打开浏览器 |

**示例**

```bash
appstore web --port 8090 --credentials config/credentials.json --catalog apps/catalog.json
# → http://127.0.0.1:8090
```