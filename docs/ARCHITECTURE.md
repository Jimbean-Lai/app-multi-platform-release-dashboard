# 架构说明

## 分层设计

```
appstore CLI (cli.py) ── Web 看板 (web.py)
     |                        |
     v                         v
Registry (registry.py) -- 按平台名惰性加载适配器
     |
     +-- StoreAdapter (base.py) -- 抽象基类
         |
         +-- GoogleAdapter (stores/google.py)  ready  (publish+query)
         +-- AppleAdapter  (stores/apple.py)   ready  (query only)
         +-- XiaomiAdapter (stores/xiaomi.py)  ready  (publish+query+定时)
         +-- OPPOAdapter   (stores/oppo.py)    ready  (publish+query+定时)
         +-- VivoAdapter   (stores/vivo.py)    ready  (publish+query+定时)
         +-- HonorAdapter  (stores/honor.py)   ready  (publish+query+定时)
         +-- HuaweiAdapter (stores/huawei.py)  ready  (publish+query+定时)
```

## 核心数据模型（models.py）

- `Platform`：枚举 google/apple/xiaomi/oppo/vivo/honor/huawei
- `AuditState`：DRAFT / SUBMITTED / REVIEWING / REJECTED / PUBLISHED / UNKNOWN
- `Release`：包名、版本名、版本号、APK/AAB 路径、更新说明、metadata（online_time 等）
- `SubmitResult`：平台、成功与否、消息、remote_reference（任务 ID）
- `StoreStatus`：live_version_names/codes、draft_version_names、reviewing_version_names、review_message

## 发布流程（publish）

1. 用户从 CLI 或 Web 发起：应用 ID + 平台选择 + 版本信息
2. `catalog.to_release()` 从 apps/catalog.json 组装 `Release`（可被 CLI 参数覆盖）
3. CLI/Web 为每个目标平台调用 `adapter.publish(release, dry_run)`
4. 适配器依次：校验凭证 → 上传安装包 → 组装版本信息 → 调平台提交审核接口
5. 返回 `SubmitResult`；Web 端多平台按任务后台串行执行

## 查询流程（query_status）

1. 用户提供包名（或应用 ID）
2. 为每个已配置凭证的平台调用 `adapter.query_status(package_name)`
3. 适配器查询各平台最新发布 / 审核状态
4. 映射为统一 `StoreStatus`（已上架/草稿/审核中/消息）

## 凭证模型

`config/credentials.json`（本地私有）顶层按平台分键；**多应用**用 `apps` 映射：

```json
{
  "oppo": { "apps": { "com.a.b": {"client_id": "..", "client_secret": ".."} } },
  "huawei": { "apps": { "com.a.b": {"client_id": "..", "client_secret": "..", "app_id": ".."} } }
}
```

## dry-run 行为

- Google：上传到 edit 但**不 commit**
- 其他平台：仅本地校验凭证、安装包、字段，**不发起真实 API 请求**

## 扩展新平台

1. 在 `app_store/stores/` 下创建 `xxx.py` 继承 `StoreAdapter`
2. 设置 `platform`、`required_credential_fields`、`availability`
3. 实现 `publish()` 和 `query_status()`
4. 在 `app_store/registry.py` 注册模块路径
5. 在 `models.Platform` 枚举中添加成员
6. 更新 `docs/STORE_ACCESS.md` 接入清单
