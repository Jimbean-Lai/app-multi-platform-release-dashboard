"""适配器注册表：平台名 -> 适配器类（按需惰性加载）。

惰性加载保证不安装 Google API 依赖也能使用 CLI 的 platforms / validate 等命令。
"""
from __future__ import annotations

import importlib
from typing import Any, Dict, List, Type

from .base import StoreAdapter, StoreError
from .models import Platform

_PLATFORM_MODULES = {
    Platform.HUAWEI.value: "app_store.stores.huawei",
    Platform.OPPO.value: "app_store.stores.oppo",
    Platform.VIVO.value: "app_store.stores.vivo",
    Platform.XIAOMI.value: "app_store.stores.xiaomi",
    Platform.HONOR.value: "app_store.stores.honor",
    Platform.GOOGLE.value: "app_store.stores.google",
    Platform.APPLE.value: "app_store.stores.apple",
}

_REGISTRY: Dict[str, Type[StoreAdapter]] = {}


def _load_all() -> None:
    for key, module in _PLATFORM_MODULES.items():
        if key in _REGISTRY:
            continue
        try:
            mod = importlib.import_module(module)
        except Exception:
            continue
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, StoreAdapter)
                and obj is not StoreAdapter
                and getattr(obj, "platform", None) is not None
                and obj.platform.value == key
            ):
                _REGISTRY[key] = obj


def get_adapter(platform: object, credentials: Dict[str, Any]) -> StoreAdapter:
    _load_all()
    key = platform.value if isinstance(platform, Platform) else str(platform).lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise StoreError(f"平台未注册或加载失败: {key}")
    return cls(credentials.get(key) or {})


def list_platforms() -> List[Dict[str, Any]]:
    _load_all()
    items: List[Dict[str, Any]] = []
    for p in Platform:
        cls = _REGISTRY.get(p.value)
        if cls is None:
            continue
        items.append(
            {
                "platform": p.value,
                "display_name": p.display_name,
                "availability": getattr(cls, "availability", "ready"),
                "credential_fields": list(getattr(cls, "required_credential_fields", ())),
            }
        )
    return items
