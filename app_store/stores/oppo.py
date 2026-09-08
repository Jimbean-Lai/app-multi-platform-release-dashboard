# -*- coding: utf-8 -*-
"""OPPO 软件商店适配器（完整实现）。

凭证字段：client_id / client_secret
接口：GET /developer/v1/token(获取token)
      GET /resource/v1/upload/get-upload-url(获取上传配置)
      POST upload_url(上传文件)
      POST /resource/v1/app/upd(发布版本，支持online_type=1/2定时)
      GET /resource/v1/app/info(查询)
"""
# flake8: noqa
import hashlib, hmac, json, os, time, datetime as _dt
from typing import Any, Dict, List, Optional

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso
from ..upload_progress import ProgressFile, make_multipart_monitor

_DOMAIN = "https://oop-openapi-cn.heytapmobi.com"


class OPPOAdapter(StoreAdapter):
    platform = Platform.OPPO
    display_name = "OPPO 软件商店"
    availability = "ready"
    required_credential_fields = ()  # 已在 __init__ / _cred_for 中校验

    def __init__(self, credentials: Dict[str, Any]) -> None:
        super().__init__(credentials)
        self._domain = self.credentials.get("domain", _DOMAIN).rstrip("/")
        self._client_id = self.credentials.get("client_id") or ""
        self._client_secret = self.credentials.get("client_secret") or ""
        self._token: str = ""
        self._token_for: str = ""

    def check(self) -> List[str]:
        try:
            import requests
            return []
        except ImportError:
            return ["缺少 requests 依赖"]

    @staticmethod
    def _sign(secret: str, data: Dict[str, Any]) -> str:
        items = sorted(data.items())
        sign_str = "&".join(f"{k}={v}" for k, v in items)
        return hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

    def _refresh_token(self) -> None:
        import requests as req
        resp = req.get(f"{self._domain}/developer/v1/token", params={
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }, timeout=30)
        d = resp.json()
        if d.get("errno") != 0 or not d.get("data", {}).get("access_token"):
            raise StoreError(f"OPPO 获取 token 失败: {d}")
        self._token = d["data"]["access_token"]

    def _cred_for(self, pkg: str) -> tuple:
        """按包名取 client_id/client_secret；无 apps 时用顶层凭证。"""
        apps = self.credentials.get("apps") or {}
        if apps:
            c = apps.get(pkg)
            if not c:
                raise StoreError(f"OPPO 凭证 apps 中没有 {pkg} 的 client_id/client_secret")
            return c.get("client_id", ""), c.get("client_secret", "")
        return self._client_id, self._client_secret

    def _get_token(self, cid: str, csecret: str) -> str:
        import requests as req
        resp = req.get(f"{self._domain}/developer/v1/token", params={
            "client_id": cid, "client_secret": csecret, "grant_type": "client_credentials",
        }, timeout=30)
        d = resp.json()
        if d.get("errno") != 0 or not d.get("data", {}).get("access_token"):
            raise StoreError(f"OPPO 获取 token 失败: {d}")
        return d["data"]["access_token"]

    def _request(self, method: str, path: str, data: Dict[str, Any] = None, files: Any = None, pkg: str = "") -> Dict[str, Any]:
        import requests as req
        cid, csecret = self._cred_for(pkg)
        if not self._token or self._token_for != cid:
            self._token = self._get_token(cid, csecret)
            self._token_for = cid
        params = dict(data or {})
        # 清洗 None 值（requests form 编码对 None 与签名串不一致会导致签名失败）
        params = {k: (v if v is not None else "") for k, v in params.items()}
        params["access_token"] = self._token
        params["timestamp"] = int(time.time())
        params["api_sign"] = self._sign(csecret, params)
        if method.upper() == "GET":
            resp = req.get(self._domain + path, params=params, timeout=60)
        else:
            resp = req.post(self._domain + path, data=params, files=files, timeout=120)
        try:
            payload = resp.json()
        except Exception:
            raise StoreError(f"OPPO 非JSON: {resp.text[:300]}")
        if payload.get("errno") != 0:
            raise StoreError(f"OPPO {path}: {payload.get('errmsg', payload)}")
        return payload

    def _upload_file(self, file_path: str, cb=None, pkg: str = "") -> str:
        import requests as req
        cfg = self._request("GET", "/resource/v1/upload/get-upload-url", pkg=pkg)
        upload_url = cfg["data"]["upload_url"]
        upload_sign = cfg["data"]["sign"]
        file_size = os.path.getsize(file_path)
        if cb:
            with open(file_path, "rb") as f:
                fields = [
                    ("sign", upload_sign),
                    ("type", "apk"),
                    ("file", (os.path.basename(file_path), f, "application/octet-stream")),
                ]
                body = make_multipart_monitor(fields, file_size, cb)
                resp = req.post(upload_url, data=body, headers={"Content-Type": body.content_type}, timeout=600)
        else:
            with open(file_path, "rb") as f:
                resp = req.post(upload_url, data={"sign": upload_sign, "type": "apk"}, files={"file": (os.path.basename(file_path), f)}, timeout=600)
        r = resp.json()
        if r.get("errno") != 0:
            raise StoreError(f"OPPO 上传错误: {r}")
        return r["data"]["url"] if isinstance(r.get("data"), dict) else r["data"]

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        scb = (release.metadata or {}).get("_step_cb")
        if dry_run:
            return SubmitResult(self.platform, True, "OPPO: dry-run 通过", state=AuditState.DRAFT)
        apk = release.apk_path
        if not apk or not os.path.isfile(apk):
            raise StoreError(f"OPPO APK 不存在: {apk}")

        if scb: scb("获取 OPPO 上传地址…")
        # 读取 OPPO 现网资料（更新时自动复用，避免重复填）
        existing = self._request("GET", "/resource/v1/app/info", data={"pkg_name": release.package_name}, pkg=release.package_name).get("data") or {}
        meta = release.metadata or {}
        md5 = self._file_md5(apk)
        if scb: scb("上传 APK 到 OPPO…")
        pc = (release.metadata or {}).get("_progress_cb")
        apk_url = self._upload_file(apk, cb=pc, pkg=release.package_name)

        # 以 OPPO 现网资料为基底（更新已有应用：分类/版权/商务联系人/简介等
        # 全部沿用现网值，避免每次提交缺字段），再覆盖版本相关字段
        params = dict(existing)
        # 移除只读/管理字段（不允许回传）
        for _ro in ("app_id", "dev_id", "version_id", "app_key", "sign", "apk_full_url",
                    "apk_md5", "apk_size", "app_create_time", "create_time", "update_time",
                    "audit_status", "audit_status_name", "change_state", "old_audit_status",
                    "state", "type", "level", "reserve_state", "transfer_state",
                    "refuse_reason", "refuse_reason_with_sugg", "business_refuse_reason",
                    "online_info_offline_apply", "freeze_advice", "freeze_file",
                    "freeze_reason", "freeze_reason_with_sugg", "offline_info", "offline_time",
                    "online_time", "sche_online_time", "pic_url_material",
                    "landscape_pic_url_material", "video_url_material", "special_file_url",
                    "electronic_cert_url", "icp_url", "cover_url", "header_md5", "icon_md5",
            # 适配/设备/版本类字段由新 APK 决定，不应从旧版本继承
            "adaptive_equipment", "adaptive_type", "version_device", "version_type",
            "version_name", "resolution", "app_real_type"):
            params.pop(_ro, None)

        # 覆盖版本相关字段
        params.update({
            "pkg_name": release.package_name,
            "version_code": str(release.version_code or meta.get("version_code") or existing.get("version_code", "")),
            "version_name": release.version_name or meta.get("version_name") or existing.get("version_name", ""),
            "apk_url": json.dumps([{"url": apk_url, "md5": md5, "cpu_code": meta.get("cpu_code", 0)}], ensure_ascii=False),
            "app_name": release.title or meta.get("appName") or existing.get("app_name", release.package_name),
            "second_category_id": meta.get("second_category_id") or existing.get("second_category_id", 0),
            "third_category_id": meta.get("third_category_id") or existing.get("third_category_id", 0),
            "summary": meta.get("summary") or existing.get("summary", ""),
            "detail_desc": meta.get("detail_desc") or existing.get("detail_desc", ""),
            "update_desc": release.release_notes or existing.get("update_desc", ""),
            "privacy_source_url": meta.get("privacy_source_url") or existing.get("privacy_source_url", ""),
            "icon_url": (self._upload_file(meta["icon"]) if meta.get("icon") else existing.get("icon_url", "")),
            "pic_url": (self._upload_images(meta["screenshots"]) if meta.get("screenshots") else existing.get("pic_url", "")),
            "test_desc": meta.get("test_desc") or existing.get("test_desc", ""),
            # 商务联系人（优先 meta 配置，其次复用现网资料）
            "business_username": meta.get("business_username") or existing.get("business_username", ""),
            "business_email": meta.get("business_email") or existing.get("business_email", ""),
            "business_mobile": meta.get("business_mobile") or existing.get("business_mobile", ""),
            "business_qq": meta.get("business_qq") or existing.get("business_qq", ""),
            "business_wx": meta.get("business_wx") or existing.get("business_wx", ""),
            "business_address": meta.get("business_address") or existing.get("business_address", ""),
            "copyright_url": meta.get("copyright_url") or existing.get("copyright_url", ""),
        })

        # 清洗 None → 空串（现网资料部分字段为 null，None 会导致签名串
        # 出现 business_qq=None 而与 requests 实际发送值不一致 → 签名校验失败）
        params = {k: (v if v is not None else "") for k, v in params.items()}

        if scb: scb("提交资料到 OPPO…")
        # 定时发布
        import datetime as _dt
        ot = meta.get("online_time") or meta.get("onlineTime") or (release.metadata or {}).get("online_time")
        if ot:
            params["online_type"] = 2
            try:
                ot_int = int(ot)
            except (ValueError, TypeError):
                try:
                    dt = _dt.datetime.strptime(str(ot).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
                    ot_int = int(dt.timestamp() * 1000)
                except (ValueError, TypeError):
                    raise StoreError(f"online_time 格式错误: {ot!r}")
            params["sche_online_time"] = _dt.datetime.fromtimestamp(ot_int / 1000).strftime("%Y-%m-%d %H:%M:%S")
        else:
            params["online_type"] = 1

        payload = self._request("POST", "/resource/v1/app/upd", data=params, pkg=release.package_name)
        return SubmitResult(self.platform, True, f"OPPO: {payload.get('errmsg', '提交成功')}",
                            remote_reference=str(payload.get("data", {}).get("task_id", "")),
                            state=AuditState.SUBMITTED, raw=payload)

    def query_status(self, package_name: str) -> StoreStatus:
        payload = self._request("GET", "/resource/v1/app/info", data={"pkg_name": package_name}, pkg=package_name)
        data = payload.get("data") or {}
        version = data.get("version_name") or ""
        vcode = data.get("version_code") or 0
        codes = [int(vcode)] if vcode else []

        audit_status = data.get("audit_status")
        audit_name = data.get("audit_status_name", "")
        change_state = data.get("change_state")
        update_desc = data.get("update_desc", "")
        online_type = data.get("online_type")
        sche_time = data.get("sche_online_time", "")

        # 状态映射（OPPO 审核状态：111=上线）
        def _to_int(v):
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        audit_i = _to_int(audit_status)
        change_i = _to_int(change_state)
        state = AuditState.UNKNOWN
        if audit_i == 111 or change_i == 111:
            state = AuditState.PUBLISHED
        elif audit_i is not None and 0 < audit_i < 111:
            state = AuditState.REVIEWING
        elif audit_i == 0 or (change_i is not None and change_i != 111):
            state = AuditState.DRAFT

        msgs = []
        if audit_name:
            msgs.append(f"状态: {audit_name}")
        if update_desc:
            msgs.append(f"更新说明: {update_desc}")
        if online_type == 2 and sche_time:
            msgs.append(f"定时上线: {sche_time}")

        return StoreStatus(self.platform, package_name, state,
                           live_version_names=[str(version)] if version else [],
                           live_version_codes=codes,
                           review_message="；".join(msgs),
                           raw=payload, checked_at=utcnow_iso())

    @staticmethod
    def _file_md5(path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for c in iter(lambda: f.read(65536), b""):
                h.update(c)
        return h.hexdigest()

    def _upload_images(self, paths: List[str]) -> str:
        return ",".join(self._upload_file(p) for p in paths)
