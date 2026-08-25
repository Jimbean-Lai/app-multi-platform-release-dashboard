# Web 看板使用手册

看板是日常最直观的操作界面，支持选择应用、查看版本、勾选平台一键发布。

## 启动

```bash
appstore web --port 8090 --credentials config/credentials.json --catalog apps/catalog.json
```

打开浏览器 → `http://127.0.0.1:8090`

## 界面布局

左侧栏：应用列表（按分类分组）
右侧主区：

1. **顶部**：当前选中应用信息（名称 / 包名 / 版本）
2. **三列卡片**
   - **应用信息**：包名、versionName、versionCode
   - **构建产物**：AAB/APK 检测
   - **发布**：版本名、版本号、**平台多选复选框**、更新说明、**定时上线时间选择**、发布/查询按钮
3. **平台状态**：各平台已上架版本、审核状态、草稿版本（三行）
4. **日志**：发布任务进度与结果

## 发布操作步骤

1. 左侧选择目标 App → 右侧加载应用信息
2. 填写/确认：
   - **版本名**（如 `4.6.1`）
   - **versionCode**（留空自动）
   - **勾选发布平台**（可多选，不含 Apple）
   - **更新说明**
   - **定时上线**（选时间；留空=立即上线）
3. 点 **"发布"** → 后台异步执行
4. 右侧日志区显示每个平台的进度
5. 点 **"查询状态"** 刷新版本信息

### 平台多选注意

- 复选框全选/清空按钮在平台列表下方
- Apple 不参与发布（仅查询）
- 如果你勾选了多个平台，后台会**依次**执行发布

## HTTP API

看板也提供 JSON API，供脚本/CI 集成。

### 查询状态

```http
POST /api/status
Content-Type: application/json

{"app_id": "philips-easykey-plus"}
```

返回：

```json
{
  "ok": true,
  "package": "com.philips.easykey.lock",
  "statuses": [
    {"platform": "google", "state": "published", "live_version_names": ["202604101 (4.5.0)"], "draft_version_names": ["202608181 (4.6.1)"], "reviewing_version_names": [], "review_message": "production:草稿未送审"},
    {"platform": "xiaomi", "state": "published", "live_version_names": ["4.6.0"]},
    ...
  ]
}
```

### 发布

```http
POST /api/publish
Content-Type: application/json

{
  "app_id": "philps-easykey-plus",
  "platform": "xiaomi,oppo",
  "dry_run": false,
  "version_name": "4.6.1",
  "version_code": 202608181,
  "release_notes": "修复问题",
  "online_time": "2026-09-01T10:00"
}
``

- `platform`：逗号分隔多个；`"all"` = 全部已配平台（不含 Apple）
- `online_time`：ISO 格式时间（`YYYY-MM-DDTHH:MM`），留空=立即

返回 `{"ok": true, "task_id": "xxx"}`，然后 GET `/api/tasks/<id>` 轮询进度。

### 获取发布任务进度

```http
GET /api/tasks/{task_id}
```

返回：

```json
{
  "status": "running",
  "progress": 45,
  "stage": "上传文件",
  "steps": ["...", "..."],
  "errors": []
}
```

### 获取应用列表

```http
GET /api/apps
```

### 获取平台列表

```http
GET /api/platforms
```

### 校验凭证

```http
POST /api/validate
```

### 列出目录可检测的构建文件

```http
POST /api/files
Content-Type: application/json

{"app_id": "philips-easykey-plus"}
```
