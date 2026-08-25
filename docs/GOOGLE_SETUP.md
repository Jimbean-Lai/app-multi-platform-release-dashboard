# Google Play 服务账号创建指引（Google API 凭证）

> 作用：让本工具能调用 **Google Play Developer API** 上传 AAB/APK、查询已上架版本与审核状态。
> 预计耗时：10~20 分钟。需要：一个 Google 账号（已注册 Play Console 开发者）。

## 目标产物

一个 JSON 文件，例如：

\`\`\`
~/.config/appstore/google-play-service-account.json
\`\`\`

文件内容形如（这是 Google 自动生成的，不要手写）：

\`\`\`json
{
  "type": "service_account",
  "project_id": "xxx",
  "private_key_id": "xxx",
  "private_key": "-----BEGIN PRIVATE KEY-----\n...",
  "client_email": "xxx@xxx.iam.gserviceaccount.com",
  "client_id": "xxx",
  ...
}
\`\`\`

## 完整步骤

### 第 1 步：进入 Google Cloud Console

打开浏览器访问：
**https://console.cloud.google.com/**
> 用同一个 Google 账号登录（与 Play Console 相同账号）。

### 第 2 步：创建或选择项目

1. 顶部项目选择器（左上角 "选择项目" 下拉）→ **新建项目**
2. 项目名称建议：\`app-store-publisher\`（可随意）
3. 创建后，在顶部确认已切换到该项目

### 第 3 步：启用 Android Publisher API（关键！）

1. 访问：**https://console.cloud.google.com/apis/library/androidpublisher.googleapis.com**
2. 如果当前项目不是刚创建的那个，先切换项目
3. 点 **启用 (Enable)**

### 第 4 步：创建服务账号

1. 访问：**https://console.cloud.google.com/iam-admin/serviceaccounts**
2. 点 **创建服务账号**（Create service account）
3. 填写：
   - 名称：\`app-publisher\`
   - 服务账号 ID：自动生成，可留空
4. 点 **创建并继续**
5. 角色（Role）步骤：可以**直接跳过**（权限在 Play Console 那边授予），点 **完成**
6. 在服务账号列表中点开刚创建的账号 → **密钥 (Keys)** 标签 → **添加密钥** → **创建新密钥**
7. 密钥类型选 **JSON** → 创建 → 浏览器自动下载一个 \`xxxx.json\`

> 警告：这个 JSON 就是我们要的凭证，**只下载这一次**（私钥不可再次下载）。

### 第 5 步：把 JSON 放到安全位置

\`\`\`bash
mkdir -p ~/.config/appstore
# 把下载的 JSON 移过去，并改个好记的名字（用你真实的下载文件名替换）
mv ~/Downloads/xxxx.json ~/.config/appstore/google-play-service-account.json
chmod 600 ~/.config/appstore/google-play-service-account.json
\`\`\`

### 第 6 步：在 Google Play Console 授权（最关键！）

1. 打开 **https://play.google.com/console/** （确保是你发布 Philips EasyKey Plus 的开发者账号）
2. 左侧菜单 → **设置 Setup** → **API 访问 API access**
3. 首次进入会看到"关联 Google Cloud 项目"，选择我们在第 2 步创建的项目 → **关联**
4. 页面底部 "服务账号 Service accounts" 区域会列出刚创建的服务账号，点 **查看链接/授予权限**
5. 弹出窗口勾选访问权限：
   - 查看应用信息（View app information）——必选，查询版本/审核状态
   - 管理并发布应用（Manage and publish apps）——发布需要
   - 上传应用包（Upload app bundles）——上传 AAB/APK 需要
6. 保存

> 警告：如果这一步没做，工具调用会报 **403 forbidden / 权限不足**。

### 第 7 步：告诉工具凭证位置

在项目目录 \`config/credentials.json\` 里填入第 5 步的路径：

\`\`\`json
{
  "google": {
    "service_account_json": "/path/to/google-play-service-account.json"
  }
}
\`\`\`

---

## 验证

\`\`\`bash
cd /path/to/project
./.venv/bin/python -m app_store.cli validate --credentials config/credentials.json
\`\`\`

- 输出 \`✔ google: OK\` 表示依赖就绪（还不代表 API 通）。
- 然后执行：

\`\`\`bash
./.venv/bin/python -B -m app_store.cli status --app philips-easykey-plus --credentials config/credentials.json
\`\`\`

- 如果返回 Philips EasyKey Plus 的审核状态/已上架版本数，说明授权已完成
- 如果报 403/404，多半是第 6 步授权未完成，或包名与 Play 上不一致。

---

## 常见问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| HttpError 403 ... permission denied | 第 6 步未授权 | 补授权：设置 → API 访问 |
| 404 找不到应用 | 包名与 Play 上不一致 | 确认 com.philips.easykey.lock |
| invalid_grant | 服务账号 JSON 变了/错文件 | 确认读对了下载的 JSON |
| 找不到"API 访问"菜单 | 非主账号/企业账号权限 | 用开发者主账号登录 Play Console |

## 界面链接（随时访问）

- Cloud Console 服务账号：https://console.cloud.google.com/iam-admin/serviceaccounts
- Cloud API 启用：https://console.cloud.google.com/apis/library/androidpublisher.googleapis.com
- Play Console API 访问：https://play.google.com/console


---

## 找不到 Android Publisher API？（官方改版后常见）

这个 API 现在在控制台里显示的中文名是 **「Google Play Android Developer API」**，
英文 **Google Play Android Developer API**，服务 ID 是 androidpublisher.googleapis.com。
搜「Android Publisher」可能是搜不到的。

### 方法 A：顶部搜索框（最快）

1. 打开 https://console.cloud.google.com/
2. 确认**已选择项目**（页面顶部「选择一个项目」/ 项目名下拉，若空就先新建项目）
3. 在**顶部中央搜索框**输入：`androidpublisher`
4. 结果里点 **Google Play Android Developer API**
5. 点 **启用 (Enable)**
6. 稍等出现「已启用」即可

### 方法 B：菜单导航

1. https://console.cloud.google.com/
2. 左上角 **三横线菜单** → **更多产品**（或直接找）
3. 打开：**API 和服务 (APIs & Services)** → **库 (Library)**
4. 左侧没有就直接用库页顶部的搜索框搜 `androidpublisher`
5. 点进 **Google Play Android Developer API** → **启用**

### 方法 C：直接链接（需要已登录且已选项目）

https://console.cloud.google.com/apis/library/androidpublisher.googleapis.com

如果这个链接打开后提示「请先选择项目」，就先到控制台创建一个项目再回来。

### 排查清单

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 搜索「Android Publisher」没结果 | 官方改名了 | 直接搜 `androidpublisher` |
| 点「启用」是灰的/没反应 | 页面没选中项目 | 顶部项目选择器选一个项目 |
| 直接链接打开要求先建项目 | 还没创建项目 | 先新建项目（名称随意，如 app-store-publisher） |
| 提示没有权限启用 API | 登录账号不是项目管理员 | 用同一个 Google 主账号登录（和 Play Console 同一个） |

> 提示：这个 API 只需要**启用一次**，之后创建服务账号、在 Play Console 授权即可管理全部 6 个应用。
