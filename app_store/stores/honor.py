# -*- coding: utf-8 -*-
"""荣耀应用市场适配器（基于官方 API 传包服务指引实现）。

凭证：client_id / client_secret（管理中心>开放能力>凭证）
"""
# flake8: noqa
import json, os, time
from typing import Any, Dict, List, Optional

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

_IAM_URL = "https://iam.developer.honor.com/auth/token"
_OPENAPI = "https://appmarket-openapi-drcn.cloud.honor.com/openapi/v1/publish"


class HonorAdapter(StoreAdapter):
    platform = Platform.HONOR
    display_name = "荣耀应用市场"
    availability = "ready"
    required_credential_fields = ()

    def __init__(self, credentials: Dict[str, Any]) -> None:
        super().__init__(credentials)
        self._apps = self.credentials.get("apps") or {}
        self._cid = self.credentials.get("client_id") or ""
        self._csecret = self.credentials.get("client_secret") or ""

    def check(self) -> List[str]:
        try:
            import requests
            return []
        except ImportError:
            return ["缺少 requests"]

    def _cred_for(self, pkg: str) -> tuple:
        if self._apps:
            c = self._apps.get(pkg) or {}
            if not c.get("client_id"):
                raise StoreError(f"荣耀凭证 apps 中没有 {pkg}")
            return c.get("client_id", ""), c.get("client_secret", "")
        return self._cid, self._csecret

    def _token(self, pkg: str) -> str:
        import requests as req
        cid, sec = self._cred_for(pkg)
        resp = req.post(_IAM_URL, data={
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": sec,
        }, timeout=30)
        d = resp.json()
        tok = d.get("access_token")
        if not tok:
            raise StoreError(f"荣耀获取 token 失败: {d}")
        return tok

    def _get(self, pkg: str, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import requests as req
        tok = self._token(pkg)
        url = _OPENAPI + path
        resp = req.get(url, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=60)
        d = resp.json()
        if d.get("code") != 0:
            raise StoreError(f"荣耀 {path}: {d.get('msg', d)}")
        return d

    def _post(self, pkg: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        import requests as req
        tok = self._token(pkg)
        url = _OPENAPI + path
        resp = req.post(url, json=body, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, timeout=120)
        d = resp.json()
        if d.get("code") != 0:
            raise StoreError(f"荣耀 {path}: {d.get('msg', d)}")
        return d

    def _get_app_id(self, pkg: str) -> int:
        d = self._get(pkg, "/get-app-id", {"pkgName": pkg})
        apps = d.get("data") or []
        for a in apps:
            if a.get("packageName") == pkg:
                return int(a["appId"])
        raise StoreError(f"荣耀未找到 {pkg} 的 appId（需先在平台创建并绑定包名）")

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        if dry_run:
            return SubmitResult(self.platform, True, "荣耀: dry-run 通过", state=AuditState.DRAFT)

        apk = release.apk_path
        if not apk or not os.path.isfile(apk):
            raise StoreError(f"荣耀 APK 不存在: {apk}")

        app_id = self._get_app_id(release.package_name)

        # 1) 获取文件上传 URL
        up = self._post(release.package_name, "/get-file-upload-url", {"appId": app_id, "fileList": [{"fileName": os.path.basename(apk)}]})
        # 响应结构按文档：data 里有上传路径与 objectId（具体字段需核对；此处尝试常见字段）
        uploads = up.get("data") or []
        if isinstance(uploads, dict):
            uploads = uploads.get("fileList") or uploads.get("list") or []
        if not uploads:
            raise StoreError(f"荣耀未返回上传配置: {up}")

        first = uploads[0] if isinstance(uploads, list) else uploads
        upload_url = first.get("uploadUrl") or first.get("url") or ""
        object_id = first.get("objectId") or first.get("objectID") or ""
        if not upload_url:
            raise StoreError(f"荣耀上传配置缺 uploadUrl: {first}")

        # 2) 上传文件（multipart，PUT/POST 视文档；默认 POST）
        import requests as req
        with open(apk, "rb") as f:
            up_resp = req.post(upload_url, files={"file": (os.path.basename(apk), f)}, timeout=600)

        # 3) 更新文件信息（绑定 objectId 到版本）
        self._post(release.package_name, "/update-file-info", {
            "appId": app_id,
            "fileList": [{"objectId": object_id, "fileType": 1, "versionCode": int(release.version_code or 0)}],
        })

        # 4) 更新应用信息（发布类型/定时）
        meta = release.metadata or {}
        publish_body: Dict[str, Any] = {
            "appId": app_id,
            "publishType": 1,
        }
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
            publish_body["publishType"] = 2
            publish_body["scheduledTime"] = _dt.datetime.fromtimestamp(ot_int / 1000).strftime("%Y-%m-%dT%H:%M:%S+0800")
        self._post(release.package_name, "/update-app-info", publish_body)

        # 5) 提交审核
        audit = self._post(release.package_name, "/submit-audit", {"appId": app_id})
        release_id = audit.get("data", {}).get("releaseId", "") if isinstance(audit.get("data"), dict) else ""
        return SubmitResult(self.platform, True, f"荣耀: 提交审核成功",
                            remote_reference=str(release_id), state=AuditState.SUBMITTED, raw=audit)

    def query_status(self, package_name: str) -> StoreStatus:
        app_id = self._get_app_id(package_name)
        d = self._get(package_name, "/get-app-current-release", {"appId": app_id})
        data = d.get("data") or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        version = data.get("versionName") or ""
        vcode = data.get("versionCode") or ""
        audit = data.get("auditResult")
        audit_msg = data.get("auditMessage") or ""
        release_id = data.get("releaseId") or ""

        # 0审核中 1通过 2不通过 3其他 4编辑未提交
        state = AuditState.UNKNOWN
        if audit == 1:
            state = AuditState.PUBLISHED
        elif audit == 0:
            state = AuditState.REVIEWING
        elif audit == 4:
            state = AuditState.DRAFT
        elif audit == 2:
            state = AuditState.REJECTED
        msgs = []
        if audit_msg: msgs.append(audit_msg)
        if release_id: msgs.append(f"releaseId: {release_id}")
        return StoreStatus(
            self.platform, package_name, state,
            live_version_names=[version] if version else [],
            live_version_codes=[int(vcode)] if str(vcode).isdigit() else [],
            review_message="；".join(msgs),
            raw=d, checked_at=utcnow_iso(),
        )
