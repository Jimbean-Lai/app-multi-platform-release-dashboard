# 使用手册（简明）

完整文档入口：
- [README](../README.md) — 总览与快速开始
- [CREDENTIALS_GUIDE.md](CREDENTIALS_GUIDE.md) — 凭证配置（含多应用）
- [CLI_REFERENCE.md](CLI_REFERENCE.md) — 命令行全部参数
- [WEB_GUIDE.md](WEB_GUIDE.md) — Web 看板使用 + HTTP API
- [STORE_ACCESS.md](STORE_ACCESS.md) — 各平台接口清单

## 最常用操作

```bash
# 启动看板
appstore web --port 8090 --credentials config/credentials.json --catalog apps/catalog.json

# 发布（多选平台）
appstore publish --app example-app --platform xiaomi,oppo --credentials config/credentials.json

# 查询
appstore status --app example-app --credentials config/credentials.json
```