# -*- coding: utf-8 -*-
"""APK/AAB 元数据解析：从安装包读取 package / versionName / versionCode / label。

- APK：使用 androguard（纯 Python）
- AAB：使用 zipfile + 递归解析 base/manifest/AndroidManifest.xml（protobuf XmlNode 树），
  无需 bundletool / Java，只提取 package（以及 versionName/versionCode 若存在）

AAB 的 AndroidManifest.xml 是 protobuf 序列化的 XmlNode 树：
  XmlNode    { string name=1;  repeated XmlAttribute attribute=2; repeated XmlNode child=3 }
  XmlAttribute { string name=1; string/uint32 value=2 ... }
"""
from __future__ import annotations

import os
import zipfile
from typing import Any, Dict, List, Optional, Tuple

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
        try:
            vcode = int(vcode_raw) if vcode_raw not in (None, "") else None
        except (TypeError, ValueError):
            vcode = None
        label = apk.get_app_name() or ""
    except Exception as e:
        raise StoreError(f"APK 解析失败: {e}")
    if not package:
        raise StoreError("APK 解析未取到 package（文件可能损坏或不是合法 APK）")
    return {"package_name": package, "version_name": vname or "", "version_code": vcode, "label": label or package}


# ---------- AAB protobuf 最小解码 ----------

def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    v = 0
    s = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        v |= (b & 0x7F) << s
        if not (b & 0x80):
            return v, i
        s += 7
        if s >= 64:
            break
    return v, i


def _decode_string(buf: bytes, i: int) -> Tuple[Optional[str], int]:
    """field value bytes -> utf-8 string；失败返回 None。"""
    try:
        return buf[i:].decode("utf-8", errors="replace").split("\x00")[0], len(buf)
    except Exception:
        return None, i


class _XmlAttr:
    __slots__ = ("name", "value")

    def __init__(self) -> None:
        self.name = ""
        self.value = ""


class _XmlNode:
    __slots__ = ("name", "attrs", "children")

    def __init__(self) -> None:
        self.name = ""
        self.attrs: List[_XmlAttr] = []
        self.children: List["_XmlNode"] = []


def _parse_node(buf: bytes, i: int, end: int) -> Tuple[_XmlNode, int]:
    """递归解析一个 XmlNode message（字段：1=name string, 2=attribute, 3=child）。"""
    node = _XmlNode()
    while i < end:
        tag, ni = _varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            _, i = _varint(buf, ni)
        elif wt == 1:
            i = ni + 8
        elif wt == 2:
            ln, li = _varint(buf, ni)
            sbuf = li
            ev = sbuf + ln
            if ev > end:
                break
            if field == 1:
                node.name = buf[sbuf:ev].decode("utf-8", errors="replace")
            elif field == 2:
                attr, _ = _parse_node(buf, sbuf, ev)
                a = _XmlAttr()
                a.name = attr.name
                # value 存于 attr.child（attribute 的 value 在 field 2 的嵌套里）
                for c in attr.children:
                    pass
                # 从属性节点的原始字段提取：attr 自身是 XmlAttribute {1:name, 2:value}
                # 上面 _parse_node 会把 2 当作 child 放进 children；这里手工再取
                a.value = attr.name  # placeholder
                node.attrs.append(a)
            elif field == 3:
                child, _ = _parse_node(buf, sbuf, ev)
                node.children.append(child)
            i = ev
        elif wt == 5:
            i = ni + 4
        else:
            break
    return node, i


def _parse_node2(buf: bytes, i: int, end: int) -> Tuple[_XmlNode, int]:
    """AAPT2 XmlNode 结构：field 1=Source(跳) 2=namespaceUri(string) 3=name(string) 4=Attribute(msg) 5=Child(msg)。"""
    node = _XmlNode()
    while i < end:
        tag, ni = _varint(buf, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            _, i = _varint(buf, ni)
        elif wt == 1:
            i = ni + 8
        elif wt == 2:
            ln, li = _varint(buf, ni)
            sbuf = li
            ev = sbuf + ln
            if ev > end:
                break
            if field == 3:
                node.name = buf[sbuf:ev].decode("utf-8", errors="replace")
            elif field == 4:
                a = _XmlAttr()
                ci = sbuf
                while ci < ev:
                    atag, ai = _varint(buf, ci)
                    af = atag >> 3
                    awt = atag & 7
                    if awt == 2:
                        aln, ali = _varint(buf, ai)
                        asv = ali
                        aev = asv + aln
                        if aev > ev:
                            break
                        if af == 2:
                            a.name = buf[asv:aev].decode("utf-8", errors="replace")
                        elif af == 3:
                            a.value = buf[asv:aev].decode("utf-8", errors="replace")
                        ci = aev
                    elif awt == 0:
                        _, ci = _varint(buf, ai)
                    elif awt == 1:
                        ci = ai + 8
                    else:
                        break
                node.attrs.append(a)
            elif field == 5:
                child, _ = _parse_node2(buf, sbuf, ev)
                node.children.append(child)
            i = ev
        elif wt == 5:
            i = ni + 4
        else:
            break
    return node, i

def _walk(node: _XmlNode, collected: Dict[str, str]) -> None:
    """把 XmlNode 树里的 name=value 属性收集进 dict（package/versionCode/versionName…）。"""
    for a in node.attrs:
        if a.name and a.value != "":
            collected.setdefault(a.name, a.value)
    for c in node.children:
        _walk(c, collected)


def parse_aab(path: str) -> Dict[str, Any]:
    """解析 AAB 提取 package（以及 versionVersion/VersionCode，若 manifest 顶层有）。

    不需 bundletool；读 base/manifest/AndroidManifest.xml 递归解 protobuf 属性。
    返回 {package_name, version_name, version_code, label}。
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

    try:
        # 顶层是包装消息：field 1 embedded 才是真实的 XmlNode
        tag, i = _varint(mani, 0)
        if (tag >> 3) == 1:
            ln, i = _varint(mani, i)
            root, _ = _parse_node2(mani, i, i + ln)
        else:
            root, _ = _parse_node2(mani, 0, len(mani))
    except Exception as e:
        raise StoreError(f"AAB manifest 解析失败: {e}")

    attrs: Dict[str, str] = {}
    _walk(root, attrs)
    package = attrs.get("package") or ""
    if not package:
        raise StoreError("AAB 解析未取到 package（manifest 没有 package 属性）")
    vname = attrs.get("versionName") or ""
    vcode_raw = attrs.get("versionCode") or ""
    try:
        vcode = int(vcode_raw) if vcode_raw else None
    except ValueError:
        vcode = None
    return {"package_name": package, "version_name": vname, "version_code": vcode, "label": ""}


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
