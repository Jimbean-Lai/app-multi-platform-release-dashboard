"""Google Play 适配器（androidpublisher v3）--- 完整可用。

凭证：service_account_json —— Google Play Console 创建的服务账号 JSON
（文件路径或内联 dict）。要求该账号有对应应用的发布权限。

发布流程（官方 edits API）：
  edits.insert -> bundles.upload(AAB) / apks.upload(APK) -> tracks.update -> edits.commit

上传超时已调大（默认 600s），适合大 AAB。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from ..base import StoreAdapter, StoreError
from ..models import AuditState, Platform, Release, SubmitResult, StoreStatus, utcnow_iso

_ANDROIDPUBLISHER_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_UPLOAD_TIMEOUT = 1800  # 秒，单次 socket 读/写超时（大 AAB 上传，按需在凭证里覆盖）
_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024  # 分块上传每块 8MB（避免一次性整包导致 socket 写超时）
_UPLOAD_MAX_RETRIES = 5  # 上传失败自动重试次数

# 上传 AAB 后是否需要等 processing 完成再 commit（大包 1-3 分钟）
_PROCESSING_POLL_SECONDS = 5
_PROCESSING_MAX_WAIT = 300


class GoogleAdapter(StoreAdapter):
    platform = Platform.GOOGLE
    display_name = "Google Play"
    availability = "ready"
    required_credential_fields = ()  # 支持 apps 多账号映射

    def __init__(self, credentials: Dict[str, Any]) -> None:
        super().__init__(credentials)
        self._upload_timeout = int(self.credentials.get("upload_timeout", _UPLOAD_TIMEOUT))
        self._upload_chunk = int(self.credentials.get("upload_chunk_size", _UPLOAD_CHUNK_SIZE))
        self._upload_retries = int(self.credentials.get("upload_max_retries", _UPLOAD_MAX_RETRIES))
        self._apps = self.credentials.get("apps") or {}

    def _cred_for(self, pkg: str) -> Dict[str, Any]:
        """按包名取 service_account_json；无 apps 时用顶层。"""
        if self._apps:
            c = self._apps.get(pkg)
            if not c:
                raise StoreError(f"Google 凭证 apps 中没有 {pkg} 的 service_account_json")
            if isinstance(c, str):
                return {"service_account_json": c}
            return c
        return self.credentials

    def _load_creds(self, pkg: str = "") -> Dict[str, Any]:
        cred = self._cred_for(pkg) if pkg else self.credentials
        value = cred.get("service_account_json") or ""
        if isinstance(value, dict):
            return value
        p = Path(value).expanduser()
        if not p.is_file():
            raise StoreError(f"Google 服务账号 JSON 不存在: {p}")
        return json.loads(p.read_text(encoding="utf-8"))

    def check(self) -> List[str]:
        problems: List[str] = []
        try:
            import googleapiclient  # noqa: F401
        except ImportError:
            problems.append(
                "缺少 Google API 依赖，请安装: pip install app-store-publisher[google]"
                "（google-api-python-client / google-auth / google-auth-httplib2）"
            )
        return problems

    def _service(self, pkg: str = ""):
        try:
            from google.auth.transport.requests import Request  # noqa: F401
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            import googleapiclient.http
            import socket
        except ImportError as e:
            raise StoreError(
                f"缺少 Google API 依赖: {e}。安装: pip install app-store-publisher[google]"
            )
        creds = self._load_creds(pkg)
        scoped = service_account.Credentials.from_service_account_info(
            creds, scopes=[_ANDROIDPUBLISHER_SCOPE]
        )
        # 超时加大：上传大 AAB/APK 需要
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        # httplib2 0.32 不支持 block_size 参数，仅加大 timeout
        http = httplib2.Http(timeout=self._upload_timeout)
        auth_http = AuthorizedHttp(scoped, http=http)
        return build("androidpublisher", "v3", http=auth_http, cache_discovery=False)

    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        if not release.aab_path and not release.apk_path:
            raise StoreError("发布前请提供 aab_path（Google 推荐）或 apk_path")
        # Google 优先 AAB
        path = Path(release.aab_path) if release.aab_path else Path(release.apk_path)
        if not path.is_file():
            raise StoreError(f"安装包不存在: {path}")
        if release.track not in ("production", "beta", "alpha", "internal"):
            raise StoreError(f"无效轨道: {release.track}")

        service = self._service(release.package_name)
        package = release.package_name
        edit = service.edits().insert(body={}, packageName=package).execute()
        edit_id = edit["id"]
        release_notes = release.whatsnew or release.release_notes or ""

        try:
            is_aab = path.suffix.lower() == ".aab"
            if is_aab:
                uploaded = (
                    service.edits()
                    .bundles()
                    .upload(
                        packageName=package,
                        editId=edit_id,
                        media_body=str(path),
                        media_mime_type="application/octet-stream",
                    )
                    .execute(num_retries=self._upload_retries)
                )
            else:
                uploaded = (
                    service.edits()
                    .apks()
                    .upload(
                        packageName=package,
                        editId=edit_id,
                        media_body=str(path),
                        media_mime_type="application/vnd.android.package-archive",
                    )
                    .execute(num_retries=self._upload_retries)
                )
            version_code = uploaded.get("versionCode") or release.version_code

            # 大 AAB 上传后需等待 processing 完成
            if is_aab and not dry_run:
                self._wait_processing(service, package, edit_id, version_code)

            auto_review = bool((release.metadata or {}).get("auto_review")) or bool(self.credentials.get("auto_review"))
            track_body = {
                "track": release.track,
                "releases": [
                    {
                        "name": f"{version_code} ({release.version_name})" if release.version_name else str(version_code),
                        "versionCodes": [str(version_code)],
                        "status": "draft" if (dry_run or not auto_review) else "completed",
                        "releaseNotes": (
                            [{"language": "zh-CN", "text": release_notes}] if release_notes else []
                        ),
                    }
                ],
            }
            service.edits().tracks().update(
                packageName=package, editId=edit_id, track=release.track, body=track_body
            ).execute()

            if dry_run:
                service.edits().delete(packageName=package, editId=edit_id).execute()
                return SubmitResult(
                    platform=self.platform,
                    ok=True,
                    message=f"dry-run 校验通过（{path.name}）",
                    remote_reference=edit_id,
                    state=AuditState.DRAFT,
                    raw={"edit_id": edit_id, "version_code": version_code, "artifact": str(path)},
                )

            if auto_review:
                service.edits().commit(packageName=package, editId=edit_id).execute()
            else:
                service.edits().commit(
                    packageName=package, editId=edit_id, changesNotSentForReview=True
                ).execute()
            return SubmitResult(
                platform=self.platform,
                ok=True,
                message=(
                    f"已提交到 {release.track} 轨道（{path.name}，versionCode {version_code}）"
                    + ("，已自动送审" if auto_review else "，已保存为草稿（需到 Play Console 手动送审）")
                ),
                remote_reference=edit_id,
                state=AuditState.SUBMITTED,
                raw={"edit_id": edit_id, "version_code": version_code, "is_aab": is_aab},
            )
        except StoreError:
            raise
        except Exception as e:
            try:
                service.edits().delete(packageName=package, editId=edit_id).execute()
            except Exception:
                pass
            raise StoreError(f"Google Play 发布失败: {e}")

    def _wait_processing(self, service, package: str, edit_id: str, version_code: Any) -> None:
        """等待大 AAB processing 完成后再 commit（避免 500）。不阻塞太久。"""
        import time
        waited = 0
        while waited < _PROCESSING_MAX_WAIT:
            try:
                bundles = (
                    service.edits()
                    .bundles()
                    .list(packageName=package, editId=edit_id)
                    .execute()
                )
                for b in bundles.get("bundles", []):
                    if b.get("versionCode") == str(version_code) and b.get("status") in ("ready", ""):
                        return
                time.sleep(_PROCESSING_POLL_SECONDS)
                waited += _PROCESSING_POLL_SECONDS
            except Exception:
                return

    def query_status(self, package_name: str) -> StoreStatus:
        service = self._service(package_name)
        try:
            edit = service.edits().insert(body={}, packageName=package_name).execute()
        except Exception as e:
            raise StoreError(f"Google Play 查询失败（可能是包名/权限问题）: {e}")
        edit_id = edit["id"]
        try:
            resp = (
                service.edits()
                .tracks()
                .list(packageName=package_name, editId=edit_id)
                .execute()
            )
        except Exception as e:
            raise StoreError(f"Google Play 查询失败: {e}")
        finally:
            try:
                service.edits().delete(packageName=package_name, editId=edit_id).execute()
            except Exception:
                pass

        raw_tracks = resp.get("tracks", [])
        live_codes: List[int] = []
        live_names: List[str] = []
        draft_names: List[str] = []
        reviewing_names: List[str] = []
        review_msgs: List[str] = []
        any_in_progress = False
        any_completed = False

        beta_names: List[str] = []
        alpha_names: List[str] = []
        internal_names: List[str] = []

        for track in raw_tracks:
            track_name = track.get("track", "?")
            for rel in track.get("releases", []):
                codes = [c for c in rel.get("versionCodes", [])]
                name = rel.get("name") or ""
                status = rel.get("status", "")
                if status == "inProgress":
                    any_in_progress = True
                    review_msgs.append(f"{track_name}:审核中 ({name})")
                    if name and name not in reviewing_names:
                        reviewing_names.append(name)
                elif status == "draft":
                    review_msgs.append(f"{track_name}:草稿未送审 ({name})")
                    if name and name not in draft_names:
                        draft_names.append(name)
                elif status == "completed":
                    any_completed = True
                    # 正式版只统计 production 轨道
                    if track_name == "production":
                        live_codes.extend(int(c) for c in codes)
                        if name and name not in live_names:
                            live_names.append(name)
                    else:
                        # 内测/测试轨道单独收集
                        if track_name == "beta" and name and name not in beta_names:
                            beta_names.append(name)
                        elif track_name == "alpha" and name and name not in alpha_names:
                            alpha_names.append(name)
                        elif track_name == "internal" and name and name not in internal_names:
                            internal_names.append(name)
                elif status == "halted":
                    review_msgs.append(f"{track_name}:已暂停 ({name})")

        if any_in_progress:
            state = AuditState.REVIEWING
        elif any_completed:
            state = AuditState.PUBLISHED
        elif raw_tracks:
            state = AuditState.DRAFT
        else:
            state = AuditState.UNKNOWN

        extra = {}
        if beta_names:
            extra["beta_version_names"] = beta_names
        if alpha_names:
            extra["alpha_version_names"] = alpha_names
        if internal_names:
            extra["internal_version_names"] = internal_names
        return StoreStatus(
            platform=self.platform,
            package_name=package_name,
            state=state,
            live_version_codes=sorted(live_codes),
            live_version_names=live_names,
            draft_version_names=draft_names,
            reviewing_version_names=reviewing_names,
            review_message="；".join(review_msgs) or ("各轨道当前无发布记录" if not raw_tracks else ""),
            checked_at=utcnow_iso(),
            raw=raw_tracks,
            **extra,
        )
