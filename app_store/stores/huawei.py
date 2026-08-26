# -*- coding: utf-8 -*-
"""华为 AppGallery 适配器（Publishing API v2，Android）。

发布流程：
1. appid-list -> 获取 appId
2. upload-url/for-obs -> 获取 OBS 上传 URL + objectId
3. PUT 上传 APK 到 OBS
4. app-submit -> 提交发布（支持 releaseTime 定时）
5. app-info -> 查询已上架版本
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
from typing import Any, Dict, List

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

_DOMAIN = "https://connect-api.cloud.huawei.com"


class HuaweiAdapter(StoreAdapter):
    platform = Platform.HUAWEI
    display_name = "华为 AppGallery"
    availability = "ready"
    required_credential_fields = ()

    def __init__(self, credentials: Dict[str, Any]) -> None:
        super().__init__(credentials)
        self._apps = self.credentials.get("apps") or {}
        self._cid = self.credentials.get("client_id") or ""
        self._csec = self.credentials.get("client_secret") or ""

    def _cred_for(self, pkg: str) -> dict:
        if self._apps:
            c = self._apps.get(pkg) or {}
            if not c.get("client_id"):
                raise StoreError(f"华为凭证 apps 中没有 {pkg}")
            return c
        return {"client_id": self._cid, "client_secret": self._csec}

    def _token(self, pkg: str) -> str:
        import requests
        cred = self._cred_for(pkg)
        r = requests.post(
            f"{_DOMAIN}/api/oauth2/v1/token",
            json={
                "grant_type": "client_credentials",
                "client_id": cred.get("client_id"),
                "client_secret": cred.get("client_secret"),
            },
            timeout=30,
        )
        d = r.json()
        tok = d.get("access_token")
        if not tok:
            raise StoreError(f"华为 OAuth 失败: {d}")
        return tok

    def _headers(self, pkg: str) -> dict:
        return {
            "client_id": self._cred_for(pkg)["client_id"],
            "Authorization": "Bearer " + self._token(pkg),
        }

    def _get(self, path: str, params: dict, pkg: str) -> dict:
        import requests
        r = requests.get(_DOMAIN + path, params=params, headers=self._headers(pkg), timeout=30)
        d = r.json()
        if d.get("ret", {}).get("code") != 0:
            raise StoreError("华为 " + path + ": " + str(d))
        return d

    def _post(self, path: str, pkg: str, query: dict = None, body: dict = None) -> dict:
        import requests
        url = _DOMAIN + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        r = requests.post(url, json=body, headers=self._headers(pkg), timeout=60)
        d = r.json()
        if d.get("ret", {}).get("code") != 0:
            raise StoreError("华为 " + path + ": " + str(d))
        return d

    # ---------- 查询 ----------
    def query_status(self, package_name: str, harmony_package: str = "") -> StoreStatus:
        """查询华为应用状态。

        harmony_package 非空时（如凯迪仕 Android 应用传入 com.kaadas.lock），
        同时查询 Android（v2）与 HarmonyOS（v3）版本并合并到同一状态卡片。
        """
        pk = package_name
        cred = self._cred_for(pk)
        app_kind = cred.get("app_kind", "android")

        # HarmonyOS 应用：直接用 v3 查询
        if app_kind == "harmony":
            return self._query_harmony(pk, cred.get("app_id", ""))

        # Android：v2 接口（appid-list → app-info）
        d = self._get("/api/publish/v2/appid-list", {"packageName": pk}, pk)
        appids = d.get("appids") or []
        app_id = None
        for a in appids:
            if a.get("value"):
                app_id = a["value"]
                break
        if not app_id:
            raise StoreError(f"华为未找到 {pk} 的 appId（需先在 AGC 创建应用）")

        s = self._query_android(pk, app_id)

        # 该应用还关联了 Harmony 包名 → 一并查询并合并展示
        if harmony_package:
            try:
                hcred = self._apps.get(harmony_package) or {}
                h_app_id = hcred.get("app_id") or ""
                if h_app_id:
                    hs = self._query_harmony(harmony_package, h_app_id)
                    android_names = [str(n) + "（Android）" for n in s.live_version_names]
                    hnames = [str(n) + "（Harmony）" for n in hs.live_version_names]
                    s.live_version_names = android_names + hnames
                    s.live_version_codes = list(s.live_version_codes) + list(hs.live_version_codes)
                    s.review_message = (s.review_message + "；" if s.review_message else "") + hs.review_message
                    s.raw = {"android": s.raw, "harmony": hs.raw}
                else:
                    s.review_message = (s.review_message + "；" if s.review_message else "") + f"Harmony({harmony_package}) 凭证缺 app_id"
            except StoreError as e:
                s.review_message = (s.review_message + "；" if s.review_message else "") + f"Harmony 查询失败: {e}"
        return s

    def _query_android(self, pkg: str, app_id: str) -> StoreStatus:
        """Android：v2 app-info 查询已上架版本。"""
        dd = self._get("/api/publish/v2/app-info", {"appId": app_id, "lang": "zh-CN"}, pkg)
        ai = dd.get("appInfo") or {}
        version = ai.get("onShelfVersionNumber") or ai.get("versionNumber") or ""
        vcode = ai.get("onShelfVersionCode") or ai.get("versionCode") or 0
        release_state = ai.get("releaseState")
        names = [str(version)] if version else []
        codes = [int(vcode)] if vcode else []
        state = AuditState.PUBLISHED if names else AuditState.UNKNOWN
        return StoreStatus(
            self.platform, pkg, state,
            live_version_names=names, live_version_codes=codes,
            review_message=f"releaseState={release_state}",
            raw=dd, checked_at=utcnow_iso(),
        )

    def _query_harmony(self, pkg: str, app_id: str) -> StoreStatus:
        """HarmonyOS：v3 app-info 直接按 appId 查询。"""
        dd = self._get("/api/publish/v3/app-info", {"appId": app_id}, pkg)
        ai = dd.get("appInfo") or {}
        version = ai.get("onShelfVersionNumber") or ai.get("versionNumber") or ""
        vcode = ai.get("onShelfVersionCode") or ai.get("versionCode") or 0
        release_state = ai.get("releaseState")
        names = [str(version)] if version else []
        codes = [int(vcode)] if vcode else []
        state = AuditState.PUBLISHED if names else AuditState.UNKNOWN
        return StoreStatus(
            self.platform, pkg, state,
            live_version_names=names, live_version_codes=codes,
            review_message=f"releaseState={release_state} (Harmony)",
            raw=dd, checked_at=utcnow_iso(),
        )

    # ---------- 发布 ----------
    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        # HarmonyOS 应用当前仅支持查询版本（发布需在 AGC 控制台手动操作）
        cred = self._cred_for(release.package_name)
        if cred.get("app_kind", "android") == "harmony":
            raise StoreError("华为 Harmony 仅支持查询版本；发布请在 AGC 控制台操作")
        if dry_run:
            return SubmitResult(self.platform, True, "华为: dry-run 通过", state=AuditState.DRAFT)

        pkg = release.package_name
        apk = release.apk_path
        if not apk or not os.path.isfile(apk):
            raise StoreError(f"华为 APK 不存在: {apk}")

        # 1) appid-list -> appId
        d = self._get("/api/publish/v2/appid-list", {"packageName": pkg}, pkg)
        appids = d.get("appids") or []
        app_id = None
        for a in appids:
            if a.get("value"):
                app_id = a["value"]
                break
        if not app_id:
            raise StoreError(f"华为未找到 {pkg} 的 appId")

        # 2) upload-url/for-obs
        file_size = os.path.getsize(apk)
        h = hashlib.sha256()
        with open(apk, "rb") as f:
            for c in iter(lambda: f.read(1 << 16), b""):
                h.update(c)
        sha256 = h.hexdigest()
        up = self._get(
            "/api/publish/v2/upload-url/for-obs",
            {
                "appId": app_id,
                "fileName": os.path.basename(apk),
                "sha256": sha256,
                "contentLength": file_size,
                "releaseType": 1,
            },
            pkg,
        )
        url_info = up.get("urlInfo") or {}
        obs_url = url_info.get("uploadUrl") or ""
        object_id = url_info.get("objectId") or ""
        obs_headers = url_info.get("headerInfo") or {}
        if isinstance(obs_headers, str):
            obs_headers = json.loads(obs_headers) if obs_headers else {}

        # 3) PUT 上传到 OBS
        import requests
        with open(apk, "rb") as f:
            r_obs = requests.put(obs_url, data=f, headers=obs_headers, timeout=600)
        if r_obs.status_code not in (200, 201):
            raise StoreError(f"华为 OBS 上传失败: {r_obs.status_code} {r_obs.text[:200]}")

        # 4) 提交发布（支持定时）
        meta = release.metadata or {}
        submit_query = {"appId": app_id, "releaseType": 1}
        ot = meta.get("online_time") or release.metadata.get("online_time")
        if ot:
            import datetime as _dt
            try:
                ot_int = int(ot)
            except (ValueError, TypeError):
                try:
                    dt = _dt.datetime.strptime(str(ot).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
                    ot_int = int(dt.timestamp() * 1000)
                except (ValueError, TypeError):
                    raise StoreError(f"online_time 格式错误: {ot!r}")
            submit_query["releaseTime"] = _dt.datetime.fromtimestamp(ot_int / 1000).strftime(
                "%Y-%m-%dT%H:%M:%S+0800"
            )

        payload = self._post("/api/publish/v2/app-submit", pkg, query=submit_query)
        return SubmitResult(
            self.platform,
            True,
            f"华为: {payload.get('ret', {}).get('msg', '提交成功')}",
            remote_reference=object_id,
            state=AuditState.SUBMITTED,
            raw=payload,
        )
