# -*- coding: utf-8 -*-
"""APK 元数据解析：从安装包读取 package / versionName / versionCode / label。

依赖 androguard（纯 Python，.venv 已装 4.1.4）；解析失败返回可读错误。
用于看板「选择安装包后自动回填版本信息 + 校验包名一致性」。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from .base import StoreError


def parse_apk(path: str) -> Dict[str, Any]:
    """解析 APK 返回 {package_name, version_name, version_code, label}。

    使用 androguard 解析 AndroidManifest；aab/apks 暂不支持（AAB 需 bundletool）。
    文件不存在/损坏/非 APK 时抛 StoreError。
    """
    if not path or not os.path.isfile(path):
        raise StoreError(f"APK 文件不存在: {path!r}")
    ext = os.path.splitext(path)[1].lower()
    if ext != ".apk":
        raise StoreError(f"暂只支持解析 .apk（当前: {ext or '无扩展名'}）；AAB 需 bundletool 另接")

    try:
        # androguard 使用 loguru 打印 DEBUG，静音
        import logging
        logging.getLogger("androguard").setLevel(logging.ERROR)
        try:
            from loguru import logger as loguru_logger
            loguru_logger.disable("androguard")
        except ImportError:
            pass
        from androguard.core.apk import APK
    except ImportError:
        raise StoreError("缺少 androguard 依赖，请安装: pip install androguard")

    try:
        apk = APK(path)
        package = apk.get_package() or ""
        vname = apk.get_androidversion_name() or ""
        vcode_raw = apk.get_androidversion_code()
        try:
            vcode = int(vcode_raw) if vcode_raw not in (None, "") else None
        except (TypeError, ValueError):
            vcode = None
        label = apk.get_app_name() or ""
    except Exception as e:
        raise StoreError(f"APK 解析失败: {e}")

    if not package:
        raise StoreError("APK 解析未取到 package（文件可能损坏或不是合法 APK）")
    return {
        "package_name": package,
        "version_name": vname or "",
        "version_code": vcode,
        "label": label or package,
    }
