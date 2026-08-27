"""Web 可视化看板：应用分类 / 一键发布 / 状态查询。

纯标准库实现（http.server），不依赖 Flask/FastAPI。
启动：appstore web --port 8090
API：
  GET  /                      看板页面
  GET  /api/apps              应用目录（分类+应用）
  GET  /api/platforms         平台注册表
  GET  /api/validate          校验凭证（返回 ok 列表）
  POST /api/publish           {app_id, platform|all, dry_run}
  POST /api/status            {app_id, platform?}
"""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List
from urllib.parse import urlparse

from .apk_meta import parse_apk
from .base import StoreError
from .catalog import get_catalog
from .config import load_credentials
from .models import Platform
from .registry import get_adapter, list_platforms

PLATFORM_VALUES = {p.value for p in Platform}

PUBLISH_PLATFORMS = {p for p in PLATFORM_VALUES if p != "apple"}

_lock = threading.Lock()

# ---- 任务持久化 ----
_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "tasks_history.json")
_HISTORY_MAX = 50

import os as _os

_TEMPLATE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "templates")


def _load_index_html() -> str:
    """优先读外部模板文件；缺失回退内嵌。"""
    p = _os.path.join(_TEMPLATE_DIR, "index.html")
    try:
        return open(p, encoding="utf-8").read()
    except OSError:
        return INDEX_HTML_FALLBACK


INDEX_HTML_FALLBACK = r"""<html><body><h1>模板文件缺失</h1><p>请创建 app_store/templates/index.html</p></body></html>
"""

INDEX_HTML = _load_index_html()


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except Exception:
        return {}




# ---- 任务系统（异步发布 + 进度）----
import threading as _t
import uuid as _u
import time as _tm

_task_lock = _t.Lock()
_TASKS: dict = {}
_PENDING_PATHS: dict = {}


def _history_path():
    p = _os.path.abspath(_HISTORY_FILE)
    _os.makedirs(_os.path.dirname(p), exist_ok=True)
    return p


def _load_history():
    """启动时把 config/tasks_history.json 里的历史任务加载进 _TASKS（供前端展示）。"""
    try:
        with open(_history_path(), "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception:
        return
    if not isinstance(items, list):
        return
    with _task_lock:
        for it in items:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            tid = it["id"]
            if tid not in _TASKS:
                _TASKS[tid] = it


def _save_history():
    """把最近的稳定任务（done/error/已取消）写盘，最多保留 _HISTORY_MAX 条。"""
    try:
        with _task_lock:
            stable = [
                v for v in _TASKS.values()
                if v.get("status") in ("done", "error", "killed")
            ]
            stable.sort(key=lambda x: x.get("finished_at", ""), reverse=True)
            items = stable[:_HISTORY_MAX]
        tmp = _history_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        _os.replace(tmp, _history_path())
    except Exception:
        pass


def _new_task(app_id, platform, dry_run, apk_path="", aab_path=""):
    tid = _u.uuid4().hex[:12]
    with _task_lock:
        _TASKS[tid] = {
            "id": tid, "app_id": app_id, "platform": platform, "dry_run": dry_run,
            "status": "running", "progress": 0, "stage": "准备中",
            "steps": [], "results": None, "errors": None,
        }
        _PENDING_PATHS[tid] = {"apk": apk_path, "aab": aab_path}
    return tid


def _step(tid, msg, level="info"):
    ts = _tm.strftime("%H:%M:%S")
    with _task_lock:
        if tid in _TASKS:
            _TASKS[tid]["steps"].append(f"[{ts}] {msg}")


def _update(tid, **kw):
    with _task_lock:
        if tid in _TASKS:
            _TASKS[tid].update(kw)
            if kw.get("status") in ("done", "error", "killed"):
                if not _TASKS[tid].get("finished_at"):
                    _TASKS[tid]["finished_at"] = _tm.strftime("%Y-%m-%d %H:%M:%S")
    # _save_history 有自己的锁，在外部调用避免死锁
    if kw.get("status") in ("done", "error", "killed"):
        _save_history()


def _publish_worker(tid: str):
    from .catalog import get_catalog
    from .config import load_credentials
    from .registry import get_adapter
    from .models import Platform

    with _task_lock:
        t = _TASKS.get(tid)
        if not t:
            return
        app_id, platform, dry_run = t["app_id"], t["platform"], t["dry_run"]
        params = dict(_PENDING_PATHS.get(tid, {}))
    try:
        catalog = get_catalog(Handler.catalog_path)
        creds = load_credentials(Handler.credentials_path)
        release = catalog.to_release(
            app_id=app_id,
            version_name=params.get("version_name", ""),
            version_code=params.get("version_code"),
            platform="google" if platform == "google" else "cn",
            release_notes=params.get("release_notes", ""),
            track=params.get("track", ""),
            apk_path=params.get("apk", ""),
            aab_path=params.get("aab", ""),
            online_time=params.get("online_time"),
        )
        release.metadata["auto_review"] = bool(params.get("auto_review"))
        _step(tid, f"包名: {release.package_name} v{release.version_name or '?'}")
        _update(tid, progress=8, stage="读取配置")
        if platform == "all":
            targets = [k for k in creds if k in PUBLISH_PLATFORMS]
            display = "全部已配平台"
        elif "," in platform:
            parts = [p.strip() for p in platform.split(",") if p.strip() in PUBLISH_PLATFORMS]
            targets = [p for p in parts if p in creds]
            display = ", ".join(targets)
        else:
            targets = [platform] if platform in PUBLISH_PLATFORMS else [p for p in [platform] if p in creds]
            display = platform
        _step(tid, f"目标平台: {display}")
        results, errors = [], []
        for i, key in enumerate(targets):
            _update(tid, stage=f"发布 {key} ({(i+1)}/{len(targets)})", progress=15)
            _step(tid, f"→ {key}: 开始")
            try:
                adapter = get_adapter(key, creds)
                problems = adapter.check()
                if problems:
                    raise StoreError("；".join(problems))
                _step(tid, f"  {key}: 凭证校验通过")
                res = adapter.publish(release, dry_run=dry_run)
                ok = res.ok
                results.append({"platform": key, "ok": ok, "message": res.message, "remote_reference": res.remote_reference, "state": res.state.value})
                _step(tid, f"  {key}: {'完成' if ok else '失败'} - {res.message}", "ok" if ok else "error")
            except StoreError as e:
                errors.append({"platform": key, "error": str(e)})
                _step(tid, f"  {key}: 错误 {e}", "error")
            prog = min(95, 15 + int(75 * (i + 1) / len(targets)))
            # 增量写入 results/errors，让前端每平台完成时立即看到 ✓/✗
            _update(tid, progress=prog, results=list(results), errors=list(errors))
        _update(tid, status="done", progress=100, stage="全部完成" if not errors else "有错误",
                results=results, errors=errors)
        if errors:
            _step(tid, f"共 {len(errors)} 个错误", "error")
        else:
            _step(tid, "发布流程全部完成")
    except Exception as e:
        _step(tid, f"异常: {e}", "error")
        _update(tid, status="error", stage="失败", progress=100)

class Handler(BaseHTTPRequestHandler):
    server_version = "AppStoreBoard/0.1"
    credentials_path = "config/credentials.json"
    catalog_path = "apps/catalog.json"

    def log_message(self, fmt, *args):  # 静默访问日志
        pass

    def _catalog(self):
        return get_catalog(self.catalog_path)

    # ---- GET ----
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/":
                return _html_response(self, INDEX_HTML)
            if path == "/api/apps":
                catalog = self._catalog()
                payload = {
                    "categories": [{"name": c} for c in catalog.categories()],
                    "apps": [catalog.status_payload(a["id"]) for a in catalog.all_apps()],
                }
                return _json_response(self, payload)
            if path == "/api/platforms":
                return _json_response(self, list_platforms())
            if path == "/api/config":
                return _json_response(self, {"credentials": self.credentials_path, "catalog": self.catalog_path})
            if path == "/api/validate":
                return self._api_validate()
            if path == "/api/files":
                return self._api_files()
            if path.startswith("/api/tasks/"):
                tid = path.split("/")[-1]
                with _task_lock:
                    task = _TASKS.get(tid, {"error": "not found"})
                return _json_response(self, task)
            if path == "/api/tasks":
                with _task_lock:
                    running = {k: v for k, v in _TASKS.items() if v.get("status") in ("running",)}
                    history = [
                        v for k, v in _TASKS.items() if v.get("status") in ("done", "error", "killed")
                    ]
                history.sort(key=lambda x: x.get("finished_at", ""), reverse=True)
                return _json_response(self, {"running": list(running.values()), "history": history[:_HISTORY_MAX]})
            return _json_response(self, {"error": "not found"}, 404)
        except Exception as e:
            return _json_response(self, {"error": str(e)}, 500)

    # ---- POST ----
    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = _read_body(self)
        try:
            if path == "/api/publish":
                return self._api_publish(body)
            if path == "/api/status":
                return self._api_status(body)
            if path == "/api/apps/update":
                return self._api_update_app(body)
            if path == "/api/apk/meta":
                return self._api_apk_meta(body)
            return _json_response(self, {"error": "not found"}, 404)
        except StoreError as e:
            return _json_response(self, {"ok": False, "error": str(e)}, 400)
        except Exception as e:
            return _json_response(self, {"ok": False, "error": str(e)}, 500)

    # ---- 实现 ----
    def _api_validate(self):
        creds = load_credentials(self.credentials_path)
        rows = []
        for key in creds:
            if key not in PLATFORM_VALUES:
                continue
            try:
                adapter = get_adapter(key, creds)
                problems = adapter.check()
                rows.append({"platform": key, "ok": not problems, "message": "；".join(problems) if problems else ""})
            except StoreError as e:
                rows.append({"platform": key, "ok": False, "message": str(e)})
        return _json_response(self, {"validated": rows})


    def _api_files(self):
        """返回本机可选的 AAB/APK 列表，供前端选择。"""
        catalog = self._catalog()
        return _json_response(self, catalog.detect_local_builds())

    def _api_update_app(self, body: Dict[str, Any]):
        app_id = body.get("app_id") or ""
        if not app_id:
            raise StoreError("缺少 app_id")
        fields = {k: v for k, v in body.items() if k != "app_id"}
        catalog = self._catalog()
        catalog.update_app(app_id, fields)
        return _json_response(self, {"ok": True, "app": catalog.status_payload(app_id)})

    def _api_apk_meta(self, body: Dict[str, Any]):
        """解析 APK 元数据（package/versionName/versionCode/label）。"""
        path = body.get("path") or ""
        if not path:
            raise StoreError("缺少 path")
        info = parse_apk(path)
        return _json_response(self, {"ok": True, **info})

    def _api_publish(self, body: Dict[str, Any]):
        app_id = body.get("app_id") or ""
        if not app_id:
            raise StoreError("缺少 app_id")
        # 仅查询应用不允许发布
        app = self._catalog().get_app(app_id)
        if app.get("query_only"):
            raise StoreError("应用 {id} 标记为仅查询（query_only），不支持发布操作".format(id=app_id))
        platform = (body.get("platform") or "all").lower()
        dry_run = bool(body.get("dry_run"))
        version_name = body.get("version_name") or ""
        version_code = body.get("version_code")
        release_notes = body.get("release_notes") or ""
        track = body.get("track") or ""
        apk_path = body.get("apk_path") or ""
        aab_path = body.get("aab_path") or ""
        online_time = body.get("online_time") or None
        auto_review = bool(body.get("auto_review"))

        # 创建异步任务
        tid = _new_task(app_id, platform, dry_run, apk_path=apk_path, aab_path=aab_path)
        with _task_lock:
            _PENDING_PATHS[tid].update({
                "version_name": version_name, "version_code": version_code,
                "release_notes": release_notes, "track": track,
                "online_time": online_time,
                "auto_review": auto_review,
            })
        _step(tid, "任务已创建，后台发布中...")

        t = _t.Thread(target=_publish_worker, args=(tid,), daemon=True)
        t.start()
        _step(tid, "后台线程启动")

        return _json_response(self, {"ok": True, "task_id": tid})

    def _api_status(self, body: Dict[str, Any]):
        app_id = body.get("app_id") or ""
        if not app_id:
            raise StoreError("缺少 app_id")
        catalog = self._catalog()
        app = catalog.get_app(app_id)
        package = app.get("package_name") or ""
        if not package:
            raise StoreError(f"应用 {app_id} 尚未配置 package_name")
        creds = load_credentials(self.credentials_path)
        platform = (body.get("platform") or "all").lower()
        if platform == "all":
            targets = [k for k in creds if k in PLATFORM_VALUES]
        else:
            # 支持逗号分隔多选（与发布一致）
            parts = [p.strip() for p in platform.split(",") if p.strip()]
            targets = [p for p in parts if p in creds] or [platform]

        # 华为：若应用配置了 huawei_harmony_package，查询时同时查 Android + Harmony
        huawei_harmony_pkg = app.get("huawei_harmony_package") or ""

        statuses, errors = [], []
        for key in targets:
            try:
                adapter = get_adapter(key, creds)
                if key == "huawei" and huawei_harmony_pkg:
                    s = adapter.query_status(package, huawei_harmony_pkg)
                else:
                    s = adapter.query_status(package)
                statuses.append({
                    "platform": key, "state": s.state.value,
                    "live_version_codes": s.live_version_codes,
                    "live_version_names": s.live_version_names,
                    "draft_version_names": s.draft_version_names,
                    "reviewing_version_names": s.reviewing_version_names,
                    "beta_version_names": list(getattr(s, "beta_version_names", [])),
                    "alpha_version_names": list(getattr(s, "alpha_version_names", [])),
                    "internal_version_names": list(getattr(s, "internal_version_names", [])),
                    "review_message": s.review_message,
                    "checked_at": s.checked_at,
                })
            except StoreError as e:
                errors.append({"platform": key, "error": str(e)})
        return _json_response(self, {"ok": not errors or bool(statuses), "app_id": app_id, "package": package, "statuses": statuses, "errors": errors})


def run_server(
    host: str = "127.0.0.1",
    port: int = 8090,
    credentials_path: str = "config/credentials.json",
    catalog_path: str = "apps/catalog.json",
    open_browser: bool = False,
) -> int:
    Handler.credentials_path = credentials_path
    Handler.catalog_path = catalog_path
    _load_history()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"🖥  AppStore 发布看板已启动: {url}")
    print(f"   凭证: {credentials_path}")
    print(f"   目录: {catalog_path}")
    print("   按 Ctrl+C 停止")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(run_server())
