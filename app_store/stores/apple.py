"""App Store（苹果）适配器：只查询线上已上架版本。

凭证：
  "apple": {
    "country": "cn",
    "app_id": "com.philips.easykey.lock"
  }

查询接口：iTunes Lookup API（公开、无需鉴权）：
  https://itunes.apple.com/lookup?id={app_id}&country={country}&entity=software
  返回 results[0].version（但如果 app 是 iPad-only 可能需要 entity=software/iPadSoftware）
"""
from __future__ import annotations

import json, urllib.parse, urllib.request
from typing import Any, Dict, List

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

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

    def query_status(self, package_name: str) -> StoreStatus:
        country = self._country()
        param = {"country": country, "entity": "software"}
        # 查找优先级：apps[package_name] > default app_id > bundleId(package_name)
        apps_map = self.credentials.get("apps") or {}
        default_id = self.credentials.get("app_id") or ""
        appid_ = self._app_id(package_name)
        sid = appid_.strip()
        if sid.isdigit():
            param["id"] = sid
        else:
            param["bundleId"] = sid
        url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(param)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise StoreError(f"Apple iTunes Lookup 查询失败: {e}")

        if data.get("resultCount") != 1 or not data.get("results"):
            # 若 app_id 是数字 ID，iTunes 也会返回；resultCount=0 则查不到
            raise StoreError(f"Apple 查询无结果: 包 {package_name!r} id {appid_!r}")
        r0 = data["results"][0]
        version = r0.get("version") or ""
        # 注意 App Store 不提供"审核中"版本，只提供当前线上版本。
        state = AuditState.PUBLISHED if version else AuditState.UNKNOWN
        return StoreStatus(
            platform=self.platform,
            package_name=package_name,
            state=state,
            live_version_codes=[],
            live_version_names=[version] if version else [],
            review_message="",
            raw=data,
            checked_at=utcnow_iso(),
        )
