"""凭证与 release 清单的加载。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .base import StoreError
from .models import Release


def _read_json(path: str) -> Any:
    p = Path(path).expanduser()
    if not p.is_file():
        raise StoreError(f"文件不存在: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoreError(f"JSON 解析失败 ({p}): {e}")


def load_credentials(path: str) -> Dict[str, Any]:
    """读取形如 {"google": {...}, "huawei": {...}} 的凭证文件。"""
    data = _read_json(path)
    if not isinstance(data, dict):
        raise StoreError("凭证文件必须是 JSON 对象: {平台名: 凭证字段...}")
    return data


def load_release(path: str) -> Release:
    """读取 release 清单（JSON 或 YAML），并把相对安装包路径解析为绝对路径。"""
    p = Path(path).expanduser()
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise StoreError("读取 YAML 需安装 PyYAML: pip install pyyaml")
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    else:
        data = _read_json(path)
    if not isinstance(data, dict):
        raise StoreError("release 清单必须是 JSON/YAML 对象")

    known = set(Release.__dataclass_fields__)
    payload = {k: v for k, v in data.items() if k in known}
    try:
        release = Release(**payload)
    except (TypeError, ValueError) as e:
        raise StoreError(f"release 清单字段不完整或类型错误: {e}")

    if not release.package_name or not release.version_name or not release.version_code:
        raise StoreError("release 清单必须包含 package_name / version_name / version_code")

    # 相对路径以清单所在目录为基准
    base = p.resolve().parent
    for attr in ("apk_path", "aab_path"):
        cur = getattr(release, attr)
        if cur and not os.path.isabs(cur):
            setattr(release, attr, (base / cur).as_posix())
    return release
