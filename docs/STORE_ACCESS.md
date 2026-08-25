# 各平台接入清单（2026-08 实测）

> 全部平台已实现 publish + query_status（Apple 仅 query）。以下为各平台实测用的接口与凭证要点。

## Google Play

| 项目 | 说明 |
| --- | --- |
| API | androidpublisher v3（google-api-python-client） |
| 认证 | 服务账号 OAuth2（Google Cloud 创建服务账号并授予 Play Console 权限） |
| 凭证 | `google.service_account_json` |
| 发布 | ✅ status=draft + changesNotSentForReview=True（仅存草稿，送审在 Console 人工操作） |
| 查询 | ✅ 三态：live（completed）/ draft（未送审）/ reviewing（审核中） |

**注意**：应用若已是 AAB-only，传 APK 会报 `APKs are not allowed for this application`，必须传 AAB。

## Apple App Store

| 项目 | 说明 |
| --- | --- |
| 查询 | iTunes Lookup 公开接口（https://itunes.apple.com/lookup） |
| 认证 | 无（公开） |
| 凭证 | `apple.apps`：安卓包名 → 数字 App ID 或 bundleId |
| 发布 | ❌ 不支持（仅查询已上架版本） |

## 小米应用商店

| 项目 | 说明 |
| --- | --- |
| 域名 | https://api.developer.xiaomi.com/devupload |
| 签名 | X509 公钥 RSA-1024 分段（PKCS1v1.5），证书 `public_key` 字段 |
| 接口 | /`dev/push`（发布，onlineTime 定时）、/`dev/query`（查询） |
| 凭证 | `email` / `password` / `public_key` |
| 定时 | ✅ `onlineTime`（毫秒时间戳） |

**注意**：小米查询不返回「审核中」版本（需后台人工看）；无提前发布接口。

## OPPO 软件商店

| 项目 | 说明 |
| --- | --- |
| 域名 | https://oop-openapi-cn.heytapmobi.com |
| 认证 | OAuth2 token（48h）+ HMAC-SHA256 签名 |
| 接口 | `/resource/v1/upload/get-upload-url` → 上传 → `/resource/v1/app/upd`（发布）→ `/resource/v1/app/info`（查询） |
| 凭证 | `client_id`（**19 位**）/ `client_secret` |
| 定时 | ✅ `online_type=2` + `sche_online_time`（"YYYY-MM-DD HH:MM:SS"） |

**亮点**：更新版本自动读 `app/info` 复用现网资料（图标/简介/截图 URL），无需重新上传。

## vivo 应用商店

| 项目 | 说明 |
| --- | --- |
| 域名 | https://developer-api.vivo.com.cn/router/rest |
| 签名 | HMAC-SHA256，参数 ASCII 排序 key=value& |
| 接口 | `app.upload.apk.app`（multipart 上传→serialnumber）/ `app.sync.update.app`（发布）/ `app.query.details`（查询） |
| 凭证 | `access_key` / `access_secret` |
| 定时 | ✅ `onlineType=2` + `scheOnlineTime`（"yyyy-MM-dd HH:mm:ss"） |

## 荣耀应用市场

| 项目 | 说明 |
| --- | --- |
| Token | POST https://iam.developer.honor.com/auth/token（client_credentials，form） |
| OpenAPI | https://appmarket-openapi-drcn.cloud.honor.com/openapi/v1/publish |
| 接口 | `get-app-id` / `get-app-detail` / `get-app-current-release` / `get-file-upload-url` / `file-upload` / `update-app-info` / `submit-audit` / `get-audit-result` |
| 凭证 | `client_id` / `client_secret`（账号级，可管理账号下所有应用） |
| 定时 | ✅ `publishType=2` + 定时时间 |
| 查询 | `auditResult`：0 审核中 / 1 通过 / 2 不通过 / 4 编辑未提交 |

## 华为 AppGallery

| 项目 | 说明 |
| --- | --- |
| Token | POST https://connect-api.cloud.huawei.com/api/oauth2/v1/token（JSON body） |
| OpenAPI | https://connect-api.cloud.huawei.com/api/publish/v2（**Android**）/ v3（HarmonyOS 5+） |
| 接口 | `appid-list` / `app-info` / `upload-url/for-obs` / 上传 OBS / `app-submit` |
| 凭证 | `client_id`（19 位 **API 客户端 project_client_id**）/ `client_secret` / `app_id` |
| 定时 | ✅ `releaseTime`（UTC："yyyy-MM-ddTHH:mm:ssZZ"） |

**关键坑（必读）**：
- 必须使用 AGC 控制台 → 用户与访问 → API 凭证 创建的 **project_client_id 类型** API 客户端（19 位 client_id + 64 位 secret）
- 「常规」页的 OAuth2.0 client_id（= App ID）不能调发布 API（一律 403）
- 一个 API client 属于一个项目，只能管理该项目下应用；华为/荣耀/小米等**不同应用可各自建 client**
- fileType：3=安卓 APK/AAB，1=鸿蒙 RPK/HAP
