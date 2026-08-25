# App 多平台发布看板（App Multi-Platform Release Dashboard）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![GitHub stars](https://img.shields.io/github/stars/Jimbean-Lai/app-multi-platform-release-dashboard?style=social)](https://github.com/Jimbean-Lai/app-multi-platform-release-dashboard/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/Jimbean-Lai/app-multi-platform-release-dashboard?style=social)](https://github.com/Jimbean-Lai/app-multi-platform-release-dashboard/network)

> ⭐ 如果这个项目对你有帮助，欢迎 **Star** 支持，也欢迎 **Watch** 关注更新、**Fork** 二次开发。

把同一个 App 一键发布到 **华为 AppGallery / OPPO / vivo / 小米 / 荣耀 / Google Play**（+ 苹果只查版本），
并随时查询**审核进度**与**已上架版本号**。内置 Web 看板，可视化操作。

## 核心能力

- **7 大平台全覆盖**：发布 + 查询（Apple 仅查询已上架版本）
- **统一发布模型**：一个应用配置 + 新 APK/AAB + 更新说明 → 选平台 → 一键发布
- **平台多选发布**：Web 看板可按勾选平台发布（排除 Apple），支持「全部平台」
- **定时上线**：小米/OPPO/vivo/荣耀/华为 均支持定时发布
- **自动复用资料**：OPPO 更新版本时自动沿用现网图标/简介/截图，无需重传
- **三态查询**：Google Play 区分「已上架 / 草稿未送审 / 审核中」
- **状态看板**：Web 界面直观展示每个 App 在各平台的已上架版本
- **dry-run 安全校验**：先本地校验凭证、安装包、字段，不真实提交

## 平台能力一览

| 平台 | 查询 | 发布 | 定时上线 | 备注 |
| --- | --- | --- | --- | --- |
| Google Play | ✅ 三态 | ✅ | ❌（需控制台操作） | 发布需 **AAB**（APK 已不被接受）；仅上传草稿，送审在控制台 |
| Apple App Store | ✅ 版本 | ❌ | — | 只查询已上架版本（iTunes Lookup 公开接口） |
| 小米应用商店 | ✅ | ✅ | ✅ | X509 公钥 RSA 加密签名 |
| OPPO 软件商店 | ✅ | ✅ | ✅ | 更新自动复用现有发布资料 |
| vivo 应用商店 | ✅ | ✅ | ✅ | 上传 APK → 同步更新 |
| 荣耀应用市场 | ✅ | ✅ | ✅ | 审核结果含审核意见 |
| 华为 AppGallery | ✅ | ✅ | ✅ | OBS 上传 APK（fileType=3 安卓） |

## 项目结构

```
.
├── app_store/
│   ├── cli.py            # 命令行入口（publish/status/web...）
│   ├── web.py            # Web 看板服务器（stdlib http.server）
│   ├── base.py           # StoreAdapter 抽象基类
│   ├── registry.py       # 平台 -> 适配器注册表
│   ├── config.py         # 凭证 / 应用目录加载
│   ├── catalog.py        # 应用目录 → Release 模型
│   ├── models.py         # 统一数据模型（Platform/AuditState/...）
│   ├── templates/
│   │   └── index.html    # Web 看板前端（单文件）
│   └── stores/           # 各平台适配器
│       ├── google.py / apple.py / xiaomi.py
│       ├── oppo.py / vivo.py / honor.py / huawei.py
├── apps/catalog.json     # 应用目录（包名、构建产物路径、版本）
├── config/               # 凭证（credentials.json 本地私有）
└── docs/                 # 文档
```

## 快速开始

### 1. 环境准备

```bash
cd /path/to/app 上架及查询
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 依赖：requests、pycryptodome、cryptography（小米签名）、google-api-python-client（Google，可选）
```

### 2. 配置凭证

复制 `config/example.credentials.json` 为 `config/credentials.json`（已 gitignore，不会入库），
按各平台说明填写。**多应用场景**用 `apps` 映射按包名区分凭证（见 [docs/CREDENTIALS_GUIDE.md](docs/CREDENTIALS_GUIDE.md)）。

### 3. 配置应用目录

编辑 `apps/catalog.json`，为每个应用配置包名、APK/AAB 路径：

```json
{
  "categories": [
    {
      "name": "智能门锁",
      "apps": [
        {
          "id": "philips-easykey-plus",
          "name": "Philips EasyKey Plus",
          "package_name": "com.philips.easykey.lock",
          "apk_build": "/path/to/app-release.apk",
          "aab_build": "/path/to/app-release.aab",
          "version_name": "4.6.1",
          "version_code": null,
          "track": "production",
          "release_notes": ""
        }
      ]
    }
  ]
}
```

### 4. 校验配置

```bash
appstore platforms                          # 列出平台与状态
appstore validate --credentials config/credentials.json
appstore apps                               # 查看应用目录
```

### 5. 启动 Web 看板（推荐日常使用）

```bash
appstore web --port 8090 --credentials config/credentials.json --catalog apps/catalog.json
# 打开 http://127.0.0.1:8090
```

看板上可：查看每个应用各平台已上架版本 / 选择平台一键发布 / 填版本号与更新说明 / 定时上线。

### 6. 命令行发布与查询

```bash
# dry-run 校验（推荐先跑）
appstore publish --app philips-easykey-plus --platform xiaomi,oppo --dry-run --credentials config/credentials.json

# 真实发布（勾选多平台：小写平台名逗号分隔）
appstore publish --app philips-easykey-plus --platform xiaomi,oppo,vivo --credentials config/credentials.json

# 发布到所有已配置平台（不含 Apple）
appstore publish --app philips-easykey-plus --all --credentials config/credentials.json

# 指定版本信息覆盖目录配置
appstore publish --app philips-easykey-plus --platform xiaomi --version-name 4.6.1 --version-code 202608181 --release-notes "修复问题" --credentials config/credentials.json

# 定时上线（小米/OPPO/vivo/荣耀/华为）
appstore publish --app philips-easykey-plus --platform xiaomi --online-time "2026-09-01 10:00" --credentials config/credentials.json

# 查询某 App 所有平台状态
appstore status --app philips-easykey-plus --credentials config/credentials.json

# 查询某包名
appstore status --package com.philips.easykey.lock --credentials config/credentials.json

# JSON 输出（脚本/CI 集成，--json 须在子命令前）
appstore --json status --app philips-easykey-plus --credentials config/credentials.json
```

## 安全提醒

- 凭证（client_secret / 服务账号私钥 / 签名密钥）只保存在 `config/credentials.json`（已在 .gitignore），**切勿提交到仓库**
- `config/example.credentials.json` 用占位符，供脚手架使用
- 发布会真实消耗平台配额并触发审核，请先 `--dry-run` 验证
- 上架最终结果以平台后台为准

## 详细文档

- [凭证配置指南（含多应用）](docs/CREDENTIALS_GUIDE.md)
- [Web 看板使用手册](docs/WEB_GUIDE.md)
- [CLI 参考手册](docs/CLI_REFERENCE.md)
- [各平台接入清单](docs/STORE_ACCESS.md)
- [架构说明](docs/ARCHITECTURE.md)