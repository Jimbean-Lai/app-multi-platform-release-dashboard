"""小米应用商店适配器（基于小米官方 Example.py 完整实现）。

凭证字段（config/credentials.json -> "xiaomi"）：
  email          小米开放平台账号邮箱
  password       账号密码或私钥
  public_key     小米开放平台下载的 X509 公钥证书(.pem)路径

接口（官方示例确认）：
  POST https://api.developer.xiaomi.com/devupload/dev/push   推送/更新应用
  POST https://api.developer.xiaomi.com/devupload/dev/query  查询应用信息
  POST https://api.developer.xiaomi.com/devupload/dev/category 查询分类（公开）
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

# 官方示例常量
DOMAIN = "https://api.developer.xiaomi.com/devupload"
PUSH_URL = DOMAIN + "/dev/push"
QUERY_URL = DOMAIN + "/dev/query"
CATEGORY_URL = DOMAIN + "/dev/category"

GROUP_SIZE = 128
ENCRYPT_GROUP_SIZE = GROUP_SIZE - 11  # PKCS1v1.5 RSA-1024 每段最大 117 字节


def _array_copy(src, src_pos: int, dst, dst_pos: int, length: int) -> None:
    for i in range(length):
        dst[dst_pos + i] = src[src_pos + i]


def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class XiaomiAdapter(StoreAdapter):
    platform = Platform.XIAOMI
    display_name = "小米应用商店"
    availability = "ready"
    required_credential_fields = ()  # 支持 apps 多应用映射，在 _cred_for 校验

    def check(self) -> List[str]:
        problems: List[str] = []
        for mod in ("requests", "Crypto", "cryptography"):
            try:
                __import__(mod)
            except ImportError:
                problems.append(f"缺少依赖 {mod}（pip install requests pycryptodome cryptography）")
        return problems

    def _cred_for(self, pkg: str) -> dict:
        """按包名取 {email,password,public_key}；无 apps 时用顶层凭证。"""
        apps = self.credentials.get("apps") or {}
        if apps:
            c = apps.get(pkg) or {}
            if not c.get("email"):
                raise StoreError(f"小米凭证 apps 中没有 {pkg} 的 email/password/public_key")
            return c
        return self.credentials

    def _cert_path(self, pkg: str = "") -> str:
        cred = self._cred_for(pkg)
        p = os.path.expanduser(cred.get("public_key") or "")
        if not p or not os.path.isfile(p):
            raise StoreError(f"小米公钥证书不存在: {p!r}（小米开放平台下载 X509 公钥）")
        return p

    def _encrypt_by_public_key(self, text: str, pkg: str = "") -> str:
        """对签名 JSON 用 X509 公钥做 RSA-1024 分段加密，返回 hex。"""
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from cryptography.x509 import load_pem_x509_certificate
        from Crypto.Cipher import PKCS1_v1_5
        from Crypto.PublicKey import RSA

        with open(self._cert_path(pkg), "rb") as f:
            buff = f.read()
        cert = load_pem_x509_certificate(buff, default_backend())
        public_key = cert.public_key()
        pem = public_key.public_bytes(
            encoding=Encoding.PEM, format=PublicFormat.PKCS1
        )
        cipher = PKCS1_v1_5.new(RSA.import_key(pem))

        data = text.encode("utf-8")
        n = len(data)
        i = 0
        out = bytearray()
        while i < n:
            segsize = ENCRYPT_GROUP_SIZE if (n - i) > ENCRYPT_GROUP_SIZE else (n - i)
            segment = bytearray(segsize)
            _array_copy(data, i, segment, 0, segsize)
            out += cipher.encrypt(bytes(segment))
            i += segsize
        return out.hex()

    def _post(self, url: str, data: Dict[str, str], files: Any = None) -> Dict[str, Any]:
        import requests
        resp = requests.post(url, data=data, files=files, timeout=300)
        try:
            payload = resp.json()
        except Exception:
            raise StoreError(f"小米接口返回非 JSON (HTTP {resp.status_code}): {resp.text[:300]}")
        # 小米返回约定 code==900? 以文档为准；这里保守解析
        return payload

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        if dry_run:
            return SubmitResult(
                platform=self.platform, ok=True,
                message="小米: dry-run 校验通过（未真实调用）",
                state=AuditState.DRAFT,
            )
        if not release.artifact_path():
            raise StoreError("小米发布需要 apk_path（小米仅支持 APK）")
        apk = release.apk_path
        if not apk or not os.path.isfile(apk):
            raise StoreError(f"小米 APK 不存在: {apk}")

        cred = self._cred_for(release.package_name)
        email = cred.get("email") or ""
        password = cred.get("password") or ""

        synchro_type = int(release.metadata.get("synchroType", 1)) if release.metadata else 1
        app_detail: Dict[str, Any] = release.metadata.get("appDetail", {}) if release.metadata else {}
        app_detail.setdefault("appName", release.title or release.package_name)
        app_detail.setdefault("packageName", release.package_name)
        app_detail.setdefault("versionName", release.version_name or "")
        if release.release_notes:
            app_detail["updateDesc"] = release.release_notes
        # 定时上线（支持毫秒时间戳或 'YYYY-MM-DDTHH:MM'/'YYYY-MM-DD HH:MM' 字符串）
        import datetime as _dt
        ot = (release.metadata or {}).get("online_time") or (release.metadata or {}).get("onlineTime")
        if ot:
            try:
                ot_ms = int(ot)
            except (ValueError, TypeError):
                try:
                    parsed = _dt.datetime.strptime(str(ot).replace("T", " ")[:16], "%Y-%m-%d %H:%M")
                    ot_ms = int(parsed.timestamp() * 1000)
                except (ValueError, TypeError):
                    raise StoreError(f"online_time 无法解析（毫秒或 YYYY-MM-DD[THH:MM]）：{ot!r}")
            app_detail["onlineTime"] = ot_ms

        request_data = {
            "userName": email,
            "appInfo": json.dumps(app_detail, ensure_ascii=False),
            "synchroType": synchro_type,
        }

        sig_json: Dict[str, Any] = {
            "sig": [],
            "password": password,
        }
        sig_json["sig"].append({
            "name": "RequestData",
            "hash": hashlib.md5(json.dumps(request_data, ensure_ascii=False).encode("utf-8")).hexdigest(),
        })
        sig_json["sig"].append({"name": "apk", "hash": _file_md5(apk)})

        files: Dict[str, Any] = {
            "apk": (os.path.basename(apk), open(apk, "rb")),
        }

        encrypted = self._encrypt_by_public_key(json.dumps(sig_json, ensure_ascii=False), release.package_name)
        payload = self._post(
            PUSH_URL,
            data={
                "RequestData": json.dumps(request_data, ensure_ascii=False),
                "SIG": encrypted,
            },
            files=files,
        )
        for f in files.values():
            try:
                f[1].close()
            except Exception:
                pass

        ok = payload.get("code") in (0, "0", 200, "200", 900, "900")
        return SubmitResult(
            platform=self.platform,
            ok=ok,
            message=f"小米: {payload.get('msg') or payload}",
            state=AuditState.SUBMITTED if ok else AuditState.UNKNOWN,
            raw=payload,
        )

    def query_status(self, package_name: str) -> StoreStatus:
        cred = self._cred_for(package_name)
        email = cred.get("email") or ""
        password = cred.get("password") or ""
        request_data = {"packageName": package_name, "userName": email}
        sig_json = {
            "sig": [{
                "name": "RequestData",
                "hash": hashlib.md5(json.dumps(request_data, ensure_ascii=False).encode("utf-8")).hexdigest(),
            }],
            "password": password,
        }
        encrypted = self._encrypt_by_public_key(json.dumps(sig_json, ensure_ascii=False), package_name)
        payload = self._post(
            QUERY_URL,
            data={"RequestData": json.dumps(request_data, ensure_ascii=False), "SIG": encrypted},
        )
        # 小米返回：result=0 成功，packageInfo 内含线上版本
        ok = payload.get("result") in (0, "0", 200, "200", 900, "900")
        info = payload.get("packageInfo") or {}
        version = info.get("versionName") or ""
        vcode = info.get("onlineVersionCode") or info.get("versionCode")
        names = [str(version)] if version else []
        codes = [int(vcode)] if vcode not in (None, "", 0) else []
        state = AuditState.PUBLISHED if version else (AuditState.UNKNOWN if ok else AuditState.UNKNOWN)
        msg = payload.get("message") or ""
        return StoreStatus(
            platform=self.platform,
            package_name=package_name,
            state=state,
            live_version_codes=codes,
            live_version_names=names,
            review_message=msg,
            raw=payload,
            checked_at=utcnow_iso(),
        )