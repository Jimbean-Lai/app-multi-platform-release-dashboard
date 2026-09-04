"""App Store（苹果）适配器：查询线上已上架版本。

版本号来源：抓取 App Store 网页（apps.apple.com）解析当前版本。
原因：iTunes Lookup API 的 version 字段经 CDN 缓存，返回不稳定
（实测同 URL 不同请求返回 4.18.2 / 419.0 / 4.19.1 各异），而
apps.apple.com 网页 "primarySubtitle":"版本 X.Y.Z" 唯一标记当前版本，可靠。

查询流程：
1. iTunes Lookup 确认 app 存在 + 取 trackViewUrl（网页地址）
2. 抓取 trackViewUrl 网页，正则提取 "primarySubtitle":"版本 X.Y.Z"
3. 网页解析失败时回退 iTunes Lookup 的 version 字段
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class AppleAdapter(StoreAdapter):
    platform = Platform.APPLE
    display_name = "Apple App Store"
    availability = "ready"
    required_credential_fields = ("country",)  # app_id 或 apps 至少一个存在

    def _country(self):
        return self.credentials.get("country") or "cn"

    def _app_id(self, package_name: str = ""):
        apps = self.credentials.get("apps") or {}
        if package_name:
            aid = apps.get(package_name) or ""
            if aid: return str(aid)
        aid = self.credentials.get("app_id") or ""
        if aid: return str(aid)
        if package_name:
            return package_name  # fallback 用包名当 bundleId
        raise StoreError("苹果商店缺少 app_id 或 apps 映射")

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        raise StoreError("App Store（苹果）不是发布目标；只能查询版本。使用 Google/华为/OPPO/vivo/荣耀/小米 发布")

    # ---- 网络工具 ----
    def _http_get(self, url: str, headers: Dict[str, str] = None, timeout: int = 30) -> str:
        hdrs = {"User-Agent": _BROWSER_UA}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def _lookup(self, package_name: str) -> Dict[str, Any]:
        """iTunes Lookup：确认 app 存在，返回 results[0]（含 trackViewUrl/version）。"""
        country = self._country()
        param = {"country": country, "entity": "software"}
        appid_ = self._app_id(package_name)
        sid = appid_.strip()
        if sid.isdigit():
            param["id"] = sid
        else:
            param["bundleId"] = sid
        url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(param)
        try:
            data = json.loads(self._http_get(url, headers={"User-Agent": ""}))
        except Exception as e:
            raise StoreError(f"Apple iTunes Lookup 查询失败: {e}")
        if data.get("resultCount") != 1 or not data.get("results"):
            raise StoreError(f"Apple 查询无结果: 包 {package_name!r} id {appid_!r}")
        return data["results"][0]

    def _parse_page_version(self, html: str) -> str:
        """从 App Store 网页提取当前版本。

        "primarySubtitle":"版本 X.Y.Z" 唯一标记当前版本（What's New 顶部）；
        页面里 "primarySubtitle":"X.Y.Z"（无"版本 "前缀）是历史版本列表，忽略。
        """
        m = re.search(r'"primarySubtitle":"版本\s*([0-9]+(?:\.[0-9]+){1,3})"', html)
        return m.group(1) if m else ""

    def query_status(self, package_name: str) -> StoreStatus:
        r0 = self._lookup(package_name)
        version = ""
        page_used = False
        # 1) 优先抓网页解析当前版本（权威）
        track_url = r0.get("trackViewUrl") or ""
        if track_url:
            try:
                html = self._http_get(track_url)
                v = self._parse_page_version(html)
                if v:
                    version = v
                    page_used = True
            except Exception:
                pass
        # 2) 网页失败时回退 iTunes version 字段
        if not version:
            version = r0.get("version") or ""

        state = AuditState.PUBLISHED if version else AuditState.UNKNOWN
        msg = "来自 App Store 网页" if page_used else ("来自 iTunes Lookup" if version else "")
        return StoreStatus(
            platform=self.platform,
            package_name=package_name,
            state=state,
            live_version_codes=[],
            live_version_names=[version] if version else [],
            review_message=msg,
            raw={"lookup": r0, "page_used": page_used},
            checked_at=utcnow_iso(),
        )
