
# -*- coding: utf-8 -*-
"""APK/AAB 元数据解析：从安装包读取 package / versionName / versionCode / label。

- APK：使用 androguard（纯 Python）
- AAB：使用 zipfile + 最小 protobuf 解码（无需额外依赖），只提取 package
"""
from __future__ import annotations

import os
import zipfile
from typing import Any, Dict

from .base import StoreError


def parse_apk(path: str) -> Dict[str, Any]:
    """解析 APK 返回 {package_name, version_name, version_code, label}。"""
    if not path or not os.path.isfile(path):
        raise StoreError(f"APK 文件不存在: {path!r}")
    try:
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
        vcode = int(vcode_raw) if vcode_raw not in (None, "") else None
        label = apk.get_app_name() or ""
    except Exception as e:
        raise StoreError(f"APK 解析失败: {e}")
    if not package:
        raise StoreError("APK 解析未取到 package（文件可能损坏或不是合法 APK）")
    return {"package_name": package, "version_name": vname or "", "version_code": vcode, "label": label or package}


def _varint(buf: bytes, i: int):
    """Decode protobuf varint from buf[i:]; return (value, new_i)."""
    v = 0; s = 0
    while i < len(buf):
        b = buf[i]; i += 1
        v |= (b & 0x7f) << s
        s += 7
        if not (b & 0x80):
            return v, i
    raise ValueError("truncated varint")


def parse_aab(path: str) -> Dict[str, Any]:
    """解析 AAB（Android App Bundle）提取 package（第一个 string 字段=package）。

    不依赖 bundletool；仅读取 base/manifest/AndroidManifest.xml（protobuf）。
    返回 {package_name, version_name: "", version_code: None, label: ""}。
    """
    if not path or not os.path.isfile(path):
        raise StoreError(f"AAB 文件不存在: {path!r}")
    ext = os.path.splitext(path)[1].lower()
    if ext != ".aab":
        raise StoreError(f"暂只支持解析 .aab（当前: {ext or '无扩展名'}）")
    try:
        with zipfile.ZipFile(path) as z:
            mani = z.read("base/manifest/AndroidManifest.xml")
    except KeyError:
        raise StoreError("AAB 中找不到 base/manifest/AndroidManifest.xml（不是合法 AAB）")
    except Exception as e:
        raise StoreError(f"AAB 读取失败: {e}")

    # 最小 protobuf 解析：扫描所有字段，提取 field 1（string）= package
    package = ""
    i = 0
    try:
        while i < len(mani):
            tag, i = _varint(mani, i)
            field = tag >> 3
            wt = tag & 7
            if wt == 0:  # varint
                _, i = _varint(mani, i)
            elif wt == 1:  # 64-bit
                i += 8
            elif wt == 2:  # length-delimited (string / bytes / embedded message)
                ln, i = _varint(mani, i)
                if field == 1:
                    package = mani[i:i + ln].decode("utf-8", errors="replace")
                    break
                i += ln
            elif wt == 5:  # 32-bit
                i += 4
            elif wt in (3, 4):  # start/end group (proto2)
                depth = 1 if wt == 3 else -1
                while depth:
                    stag, i = _varint(mani, i)
                    swt = stag & 7
                    if swt == 3: depth += 1
                    elif swt == 4: depth -= 1
                    else:
                        if swt == 0: _, i = _varint(mani, i)
                        elif swt == 1: i += 8
                        elif swt == 2: ln, i = _varint(mani, i); i += ln
                        elif swt == 5: i += 4
            else:
                break  # unknown wire type
    except Exception:
        pass

    if not package:
        raise StoreError("AAB 解析未取到 package（protobuf manifest 不含 package 字段）")
    return {"package_name": package, "version_name": "", "version_code": None, "label": ""}


def parse_build(path: str) -> Dict[str, Any]:
    """统一入口：按扩展名自动选择 APK / AAB 解析。"""
    if not path or not os.path.isfile(path):
        raise StoreError(f"文件不存在: {path!r}")
    ext = os.path.splitext(path)[1].lower()
    if ext == ".apk":
        return parse_apk(path)
    elif ext == ".aab":
        return parse_aab(path)
    else:
        raise StoreError(f"不支持的格式: {ext}（仅支持 .apk 或 .aab）")
