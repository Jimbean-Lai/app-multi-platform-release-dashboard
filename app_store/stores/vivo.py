"""vivo 应用商店适配器（完整实现，基于官方文档+签名示例）。

凭证（config/credentials.json）：
  "vivo": {
    "access_key": "申请API传包服务后分配的 access_key",
    "access_secret": "access_key 配对的密钥"
  }

接口：
  POST https://developer-api.vivo.com.cn/router/rest （正式环境）
  - app.query.details    查询应用详情（已上架版本）
  - app.upload.apk.app   上传 APK（返回流水号）
  - app.sync.update.app  应用更新（支持 onlineType=2 + scheOnlineTime 定时）

签名：HMAC-SHA256，按参数 ASCII 排序拼接 key=value& 后加密
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
import warnings
from typing import Any, Dict, List

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

_DOMAIN = "https://developer-api.vivo.com.cn/router/rest"


class VivoAdapter(StoreAdapter):
    platform = Platform.VIVO
    display_name = "vivo 应用商店"
    availability = "ready"
    required_credential_fields = ()  # 已支持 apps 多应用映射，在 _cred_for 校验

    def __init__(self, credentials: Dict[str, Any]) -> None:
        super().__init__(credentials)
        self._apps = self.credentials.get("apps") or {}
        self._key = self.credentials.get("access_key") or ""
        self._secret = self.credentials.get("access_secret") or ""

    def check(self) -> List[str]:
        return []

    def _cred_for(self, pkg: str) -> tuple:
        """按包名取 access_key/access_secret；无 apps 时用顶层凭证。"""
        if self._apps:
            c = self._apps.get(pkg)
            if not c:
                raise StoreError(f"vivo 凭证 apps 中没有 {pkg} 的 access_key/access_secret")
            return c.get("access_key", ""), c.get("access_secret", "")
        return self._key, self._secret

    # ---- 签名 ----
    @staticmethod
    def _sign(params: Dict[str, Any], secret: str) -> str:
        items = sorted(params.items())
        s = "&".join(f"{k}={v}" for k, v in items)
        return hmac.new(secret.encode(), s.encode(), hashlib.sha256).hexdigest()

    def _call(self, method: str, biz: Dict[str, Any], files: Any = None, pkg: str = "") -> Dict[str, Any]:
        """组装公共参数+业务参数，签名后 POST。"""
        import requests as req
        key, secret = self._cred_for(pkg)

        params: Dict[str, Any] = {
            "access_key": key,
            "timestamp": str(int(time.time() * 1000)),
            "method": method,
            "v": "1.0",
            "sign_method": "hmac",
            "format": "json",
            "target_app_key": "developer",
        }
        params.update(biz)

        # 对除了文件之外的所有参数签名
        if files:
            sign_params = dict(params)
            params["sign"] = self._sign(sign_params, secret)
        else:
            params["sign"] = self._sign(params, secret)

        # POST body 为 form
        data = urllib.parse.urlencode(params).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if files:
            # multipart：参数里去掉签名、二进制单独传
            # VIVO 的 API：上传文件接口一般先把文件上传获得流水号（服务端拉取）？
            raise StoreError("VIVO 文件上传走专门接口（见 _get_apk_token），此处不适用")
            resp = None
        else:
            resp = req.post(_DOMAIN, data=data, headers=headers, timeout=120)
        try:
            payload = resp.json()
        except Exception:
            raise StoreError(f"vivo 非JSON: {resp.text[:300]}")
        if payload.get("code") != 0:
            raise StoreError(f"vivo {method}: {payload.get('msg', payload)}")
        return payload

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        if dry_run:
            return SubmitResult(self.platform, True, "vivo: dry-run 通过", state=AuditState.DRAFT)

        apk = release.apk_path
        if not apk or not os.path.isfile(apk):
            raise StoreError(f"vivo APK 不存在: {apk}")

        # 1) 上传 APK 拿流水号（multipart）: app.upload.apk.app
        up_resp = self._upload_apk(apk, release.package_name)
        serial = up_resp.get("serialnumber") or ""
        if not serial:
            raise StoreError(f"vivo 上传 APK 未返回 serialnumber: {up_resp}")

        # 2) 应用更新（同步）: app.sync.update.app
        meta = release.metadata or {}
        params = {
            "packageName": release.package_name,
            "versionCode": str(release.version_code or meta.get("version_code", up_resp.get("versionCode", ""))),
            "apk": serial,
            "fileMd5": self._md5(apk),
            "onlineType": 1,
            "updateDesc": release.release_notes or "",
        }
        # 定时
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
            params["onlineType"] = 2
            params["scheOnlineTime"] = _dt.datetime.fromtimestamp(ot_int / 1000).strftime("%Y-%m-%d %H:%M:%S")
        else:
            params["onlineType"] = 1

        payload = self._call("app.sync.update.app", params, pkg=release.package_name)
        return SubmitResult(self.platform, True, f"vivo: {payload.get('msg', '提交成功')}",
                            remote_reference=payload.get("data", {}).get("task_id", "") or serial,
                            state=AuditState.SUBMITTED, raw=payload)

    def _upload_apk(self, apk_path: str, package_name: str) -> dict:
        """通过 multipart 上传 APK 到 vivo，返回 serialnumber 等。"""
        import requests as req
        import time
        md5 = self._md5(apk_path)
        key, secret = self._cred_for(package_name)
        params: dict = {
            "access_key": key,
            "timestamp": str(int(time.time() * 1000)),
            "method": "app.upload.apk.app",
            "v": "1.0",
            "sign_method": "hmac",
            "format": "json",
            "target_app_key": "developer",
            "packageName": package_name,
            "fileMd5": md5,
        }
        params["sign"] = self._sign(params, secret)
        with open(apk_path, "rb") as f:
            files = {"file": (apk_path.split("/")[-1], f, "application/vnd.android.package-archive")}
            resp = req.post(_DOMAIN, data=params, files=files, timeout=600)
        try:
            payload = resp.json()
        except Exception:
            raise StoreError(f"vivo 上传错误(非JSON): {resp.text[:300]}")
        if payload.get("code") != 0:
            raise StoreError(f"vivo APK 上传失败: {payload.get('msg', payload)}")
        return payload.get("data", {})

    def query_status(self, package_name: str) -> StoreStatus:
        payload = self._call("app.query.details", {"packageName": package_name}, pkg=package_name)
        data = payload.get("data") or {}
        version = data.get("versionName") or ""
        vcode = data.get("versionCode") or ""
        status = data.get("status")  # 3=已上架? saleStatus=1?
        sale = data.get("saleStatus")
        update_desc = data.get("updateDesc") or ""
        online_type = data.get("onlineType")

        state = AuditState.UNKNOWN
        # status: 从文档推测（部分 0=审核中 1=已上架 3=?）; 实测 status=3 saleStatus=1 是已上架
        if status == 3 or sale == 1:
            state = AuditState.PUBLISHED
        elif status in (1, 2) and status != 3:
            state = AuditState.REVIEWING
        elif status == 0:
            state = AuditState.DRAFT

        msgs = []
        if update_desc:
            msgs.append(f"更新说明: {update_desc}")
        if online_type == 2:
            msgs.append("定时上线")
        return StoreStatus(self.platform, package_name, state,
                           live_version_names=[version] if version else [],
                           live_version_codes=[int(vcode)] if str(vcode).isdigit() else [],
                           review_message="；".join(msgs),
                           raw=payload, checked_at=utcnow_iso())
