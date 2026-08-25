"""商店适配器包。

各适配器由 app_store.registry 在需要时惰性导入，
因此不装 Google API 依赖也不影响其它命令的使用。
"""
