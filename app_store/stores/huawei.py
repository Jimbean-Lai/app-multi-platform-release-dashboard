# -*- coding: utf-8 -*-
"""华为 AppGallery 适配器（Publishing API v2，Android）。

发布流程（基于华为 AppGallery Publishing API v2 官方接口行为）：
1. appid-list -> 获取 appId
2. upload-url -> 获取上传地址 + authCode
3. multipart 直传 APK 到 FileServer
4. PUT app-file-info -> 绑定上传文件到版本（fileType=5 APK）
5. PUT app-info -> 更新更新说明（可选）
6. app-submit -> 提交发布（APK 解析中自动轮询重试）
7. app-info -> 查询已上架版本
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
from ..upload_progress import ProgressFile, make_multipart_monitor

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

    def _put(self, path: str, pkg: str, query: dict = None, body: dict = None) -> dict:
        """PUT 请求（app-file-info / app-info 用 PUT）。"""
        import requests
        url = _DOMAIN + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        r = requests.put(url, json=body, headers=self._headers(pkg), timeout=60)
        d = r.json()
        if d.get("ret", {}).get("code") != 0:
            raise StoreError("华为 " + path + ": " + str(d))
        return d

    def _submit_with_retry(self, path: str, pkg: str, query: dict = None,
                           max_attempts: int = 8, interval: int = 30) -> dict:
        """提交发布，APK 解析中(204144660 parsing / 204144727 compiling)时轮询重试。

        华为解析 APK 需 1-2 分钟（大包更久），报解析中错误应重试
        而非直接失败。每次失败都提示去 AGC 后台完成。
        """
        import time as _t
        import requests
        last_payload: dict = {}
        for attempt in range(max_attempts):
            url = _DOMAIN + path
            if query:
                url += "?" + urllib.parse.urlencode(query)
            r = requests.post(url, json={}, headers=self._headers(pkg), timeout=60)
            try:
                d = r.json()
            except Exception:
                raise StoreError(f"华为提交返回非JSON: {r.status_code} {r.text[:200]}")
            last_payload = d
            ret = d.get("ret") or {}
            code = ret.get("code")
            if code == 0:
                return d
            msg = str(ret.get("msg", "") or ret.get("message", "")).lower()
            # 解析中/编译中 → 重试
            parsing = (code in (204144660, 204144727)) and (
                "parsing" in msg or "parse" in msg or "解析" in msg or
                "compil" in msg or "编译" in msg or "try again" in msg
            )
            if not parsing:
                raise StoreError(
                    f"华为 {path}: [{code}] {ret.get('msg', ret)}"
                    f"（APK 已上传成功，若需手动可在 AGC 后台完成：https://developer.huawei.com/consumer/cn/console）"
                )
            if attempt < max_attempts - 1:
                _t.sleep(interval)
        raise StoreError(
            f"华为提交超时（APK 仍在处理，可在 AGC 后台完成：https://developer.huawei.com/consumer/cn/console）"
        )

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
                    # 已上架版本（标注平台）
                    android_names = [str(n) + "（Android）" for n in s.live_version_names]
                    hnames = [str(n) + "（Harmony）" for n in hs.live_version_names]
                    s.live_version_names = android_names + hnames
                    s.live_version_codes = list(s.live_version_codes) + list(hs.live_version_codes)
                    # 审核中/待发布版本（标注平台）
                    s.reviewing_version_names = list(s.reviewing_version_names) + list(hs.reviewing_version_names)
                    # 审核状态文字（两平台都保留，标注平台）
                    notes = []
                    if s.audit_note:
                        notes.append(s.audit_note + "（Android）")
                    if hs.audit_note:
                        notes.append(hs.audit_note + "（Harmony）")
                    s.audit_note = "；".join(notes)
                    # 状态合并：任一审核中→审核中；任一待发布→待发布；均通过才已上架
                    if AuditState.REVIEWING in (s.state, hs.state):
                        s.state = AuditState.REVIEWING
                    elif AuditState.PENDING in (s.state, hs.state):
                        s.state = AuditState.PENDING
                    elif AuditState.REJECTED in (s.state, hs.state):
                        s.state = AuditState.REJECTED
                    s.review_message = (s.review_message + "；" if s.review_message else "") + hs.review_message
                    s.raw = {"android": s.raw, "harmony": hs.raw}
                else:
                    s.review_message = (s.review_message + "；" if s.review_message else "") + f"Harmony({harmony_package}) 凭证缺 app_id"
            except StoreError as e:
                s.review_message = (s.review_message + "；" if s.review_message else "") + f"Harmony 查询失败: {e}"
        return s

    def _query_android(self, pkg: str, app_id: str) -> StoreStatus:
        """Android：v2 app-info 查询版本状态。"""
        dd = self._get("/api/publish/v2/app-info", {"appId": app_id, "lang": "zh-CN"}, pkg)
        ai = dd.get("appInfo") or {}
        live_version = ai.get("onShelfVersionNumber") or ""
        live_vcode = ai.get("onShelfVersionCode") or None
        curr_version = ai.get("versionNumber") or ""
        curr_vcode = ai.get("versionCode") or None
        release_state = ai.get("releaseState")
        # releaseState: 0=已上架 1=审核不通过 2=已下架 3=待上架 4=审核中
        # 5=升级审核中 6=申请下架 7=草稿 8=升级审核不通过 12=预审中
        if release_state in (4, 5, 12):
            state = AuditState.REVIEWING
        elif release_state == 3:
            state = AuditState.PENDING  # 审核通过待上架
        elif release_state == 0:
            state = AuditState.PUBLISHED
        elif release_state in (1, 8):
            state = AuditState.REJECTED
        elif release_state == 7:
            state = AuditState.DRAFT
        else:
            state = AuditState.UNKNOWN
        # 已上架版本
        live_names = [str(live_version)] if live_version else []
        live_codes = [int(live_vcode)] if live_vcode else []
        # 审核中版本
        reviewing_names = []
        if curr_version and curr_version != live_version:
            reviewing_names = [str(curr_version)]
        # 审核状态文字（标准化）
        note = ""
        if release_state in (4, 5, 12):
            note = f"{curr_version} 审核中" if curr_version else "审核中"
        elif release_state in (1, 8):
            note = "审核未通过"
        elif release_state == 3:
            note = f"{curr_version} 审核通过" if curr_version else "审核通过"
        elif release_state == 0:
            note = ""  # 已上架由徽章/已上架版本行表达，不进审核状态
        return StoreStatus(
            self.platform, pkg, state,
            live_version_names=live_names, live_version_codes=live_codes,
            reviewing_version_names=reviewing_names,
            audit_note=note,
            review_message=f"releaseState={release_state}",
            raw=dd, checked_at=utcnow_iso(),
        )

    def _query_harmony(self, pkg: str, app_id: str) -> StoreStatus:
        """HarmonyOS：v3 app-info 直接按 appId 查询。"""
        dd = self._get("/api/publish/v3/app-info", {"appId": app_id}, pkg)
        ai = dd.get("appInfo") or {}
        live_version = ai.get("onShelfVersionNumber") or ""
        curr_version = ai.get("versionNumber") or ""
        vcode = ai.get("onShelfVersionCode") or ai.get("versionCode") or 0
        release_state = ai.get("releaseState")
        live_names = [str(live_version)] if live_version else []
        codes = [int(vcode)] if vcode else []
        if release_state in (4, 5, 12):
            state = AuditState.REVIEWING
        elif release_state == 3:
            state = AuditState.PENDING
        elif release_state == 0:
            state = AuditState.PUBLISHED
        elif release_state in (1, 8):
            state = AuditState.REJECTED
        elif release_state == 7:
            state = AuditState.DRAFT
        else:
            state = AuditState.UNKNOWN
        reviewing_names = [str(curr_version)] if curr_version and curr_version != live_version else []
        note = ""
        if release_state in (4, 5, 12):
            note = f"{curr_version} 审核中" if curr_version else "审核中"
        elif release_state in (1, 8):
            note = "审核未通过"
        elif release_state == 3:
            note = f"{curr_version} 审核通过" if curr_version else "审核通过"
        elif release_state == 0:
            note = ""  # 已上架由徽章/已上架版本行表达，不进审核状态
        return StoreStatus(
            self.platform, pkg, state,
            live_version_names=live_names, live_version_codes=codes,
            reviewing_version_names=reviewing_names,
            audit_note=note,
            review_message=f"releaseState={release_state}",
            raw=dd, checked_at=utcnow_iso(),
        )

    # ---------- 发布 ----------
    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        scb = (release.metadata or {}).get("_step_cb")
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

        if scb: scb("获取华为 appId…")
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

        if scb: scb("获取华为上传地址…")
        # 2) upload-url（GET 返回 uploadUrl+authCode）
        import requests
        file_size = os.path.getsize(apk)
        up = self._get(
            "/api/publish/v2/upload-url",
            {"appId": app_id, "releaseType": 1, "suffix": "apk"},
            pkg,
        )
        upload_url = up.get("uploadUrl") or ""
        auth_code = up.get("authCode") or ""
        if not upload_url:
            raise StoreError(f"华为未返回上传地址: {up}")

        if scb: scb("上传 APK 到华为…")
        # 3) multipart 直传（fields: authCode/fileCount/name/parseType + file）
        pc = (release.metadata or {}).get("_progress_cb")
        apk_name = os.path.basename(apk)
        if pc:
            f = open(apk, "rb")
            try:
                fields = [
                    ("authCode", auth_code),
                    ("fileCount", "1"),
                    ("name", apk_name),
                    ("parseType", "0"),
                ]
                # 有进度回调时用 Monitor 流式
                body = make_multipart_monitor(fields + [("file", (apk_name, f, "application/octet-stream"))], file_size, pc)
                r_up = requests.post(upload_url, data=body, headers={"Content-Type": body.content_type}, timeout=600)
            finally:
                f.close()
        else:
            with open(apk, "rb") as f:
                r_up = requests.post(upload_url, data={"authCode": auth_code, "fileCount": "1", "name": apk_name, "parseType": "0"}, files={"file": (apk_name, f)}, timeout=600)
        up_json = {}
        try:
            up_json = r_up.json()
        except Exception:
            raise StoreError(f"华为上传返回非JSON: {r_up.status_code} {r_up.text[:200]}")
        file_rsp = (up_json.get("result") or {}).get("UploadFileRsp") or {}
        if file_rsp.get("ifSuccess") != 1 and up_json.get("result", {}).get("resultCode") != "0":
            raise StoreError(f"华为上传失败: {r_up.text[:300]}")
        file_list = file_rsp.get("fileInfoList") or []
        if not file_list:
            raise StoreError(f"华为上传未返回文件信息: {r_up.text[:300]}")
        file_dest_url = file_list[0].get("fileDestUlr") or file_list[0].get("fileDestUrl") or ""
        if not file_dest_url:
            raise StoreError(f"华为上传缺 fileDestUrl: {r_up.text[:300]}")

        # 4) 绑定文件到版本（PUT app-file-info, fileType=5 APK）
        if scb: scb("绑定 APK 文件到版本…")
        self._put("/api/publish/v2/app-file-info", pkg, query={"appId": app_id, "releaseType": 1}, body={
            "fileType": 5,
            "files": [{"fileName": apk_name, "fileDestUrl": file_dest_url}],
        })

        # 5) 更新更新说明（可选）
        if release.release_notes:
            self._put("/api/publish/v2/app-info", pkg, query={"appId": app_id, "releaseType": 1}, body={"newFeatures": release.release_notes})

        # 6) 提交发布（支持定时）+ 解析中轮询
        if scb: scb("提交发布到华为 AppGallery…")
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
        payload = self._submit_with_retry("/api/publish/v2/app-submit", pkg, query=submit_query)
        return SubmitResult(
            self.platform,
            True,
            f"华为: {payload.get('ret', {}).get('msg', '提交成功')}",
            remote_reference=str(file_dest_url),
            state=AuditState.SUBMITTED,
            raw=payload,
        )