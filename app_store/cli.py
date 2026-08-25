"""appstore 命令行入口。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .base import StoreError
from .catalog import get_catalog
from .config import load_credentials, load_release
from .models import Platform, SubmitResult, StoreStatus
from .registry import get_adapter, list_platforms

PLATFORM_VALUES = {p.value for p in Platform}

DEFAULT_CREDENTIALS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "credentials.json")


def _json_out(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _publish_row(result: SubmitResult) -> Dict[str, Any]:
    return {
        "platform": result.platform.value,
        "ok": result.ok,
        "message": result.message,
        "remote_reference": result.remote_reference,
        "state": result.state.value,
    }


def _status_row(status: StoreStatus) -> Dict[str, Any]:
    return {
        "platform": status.platform.value,
        "package_name": status.package_name,
        "state": status.state.value,
        "live_version_codes": sorted(status.live_version_codes),
        "live_version_names": status.live_version_names,
        "review_message": status.review_message,
        "checked_at": status.checked_at,
    }


def cmd_apps(args: argparse.Namespace) -> int:
    catalog = get_catalog(args.catalog)
    rows: List[Dict[str, Any]] = []
    for app in catalog.all_apps():
        build = app.get("latest_build") or ""
        rows.append(
            {
                "id": app["id"],
                "name": app.get("name", ""),
                "category": app.get("category", ""),
                "package_name": app.get("package_name") or "",
                "latest_build": build,
                "version_name": app.get("version_name") or "",
                "build_exists": bool(build),
            }
        )
    if args.json:
        _json_out(rows)
    else:
        print(f"{'ID':<22}{'名称':<22}{'分类':<12}{'包名':<32}安装包")
        for r in rows:
            print(f"{r['id']:<22}{r['name']:<22}{r['category']:<12}{(r['package_name'] or '未填') :<32}{r['latest_build'] or '-'}")
    return 0


import datetime as _dt


def _parse_online_time(value):
    """Convert timestamp(ms) or 'YYYY-MM-DD HH:MM'/'YYYY-MM-DDTHH:MM' to ms."""
    if not value:
        return None
    import datetime as _dt
    try:
        return int(value)
    except (ValueError, TypeError):
        pass
    s = str(value).replace("T", " ")[:16]
    try:
        dt = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M")
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        raise StoreError("online_time format error: " + repr(value))
def _resolve_release(args: argparse.Namespace) -> Any:
    """解析 release：优先 --app（从目录取），其次 --release 文件；都给了则合并(文件覆盖目录)。"""
    catalog = get_catalog(args.catalog) if hasattr(args, "catalog") else get_catalog()
    release = None
    platform = getattr(args, "platform", None) or "google"
    online_time = _parse_online_time(getattr(args, "online_time", None)) if hasattr(args, "online_time") else None
    if getattr(args, "app", None):
        release = catalog.to_release(
            app_id=args.app,
            version_name=getattr(args, "version_name", "") or "",
            version_code=getattr(args, "version_code", None),
            platform=platform,
            release_notes=getattr(args, "release_notes", "") or "",
            track=getattr(args, "track", "") or "",
            apk_path=getattr(args, "apk_path", "") or "",
            aab_path=getattr(args, "aab_path", "") or "",
            online_time=online_time,
        )
    if getattr(args, "release", None):
        file_release = load_release(args.release)
        if release is None:
            release = file_release
        else:
            for f in release.__dataclass_fields__:
                v = getattr(file_release, f)
                if v not in (None, "", 0):
                    setattr(release, f, v)
    if release is None:
        raise StoreError("请提供 --app <目录ID> 或 --release <清单文件>（可用 appstore apps 查看应用ID）")
    # 版本/说明/轨道/包 的显式覆盖
    if getattr(args, "version_name", None):
        release.version_name = args.version_name
    if getattr(args, "version_code", None) is not None:
        release.version_code = args.version_code
    if getattr(args, "release_notes", None):
        release.release_notes = args.release_notes
    if getattr(args, "track", None):
        release.track = args.track
    if getattr(args, "apk_path", None):
        release.apk_path = release.apk_path  # 已在 to_release 里处理
    if not release.package_name:
        raise StoreError("release 缺少 package_name（目录中未配置或清单未填写）")
    if not release.aab_path and not release.apk_path:
        raise StoreError("release 缺少安装包（请配置目录 aab_build/apk_build 或用 --apk-path/--aab-path 指定）")
    return release

def cmd_publish(args: argparse.Namespace) -> int:
    if not args.all and not args.platform:
        raise StoreError("publish 需要 --platform 或 --all")
    creds = load_credentials(args.credentials)
    release = _resolve_release(args)
    if args.track:
        release.track = args.track

    if args.all:
        targets = [k for k in creds if k in PLATFORM_VALUES]
        if not targets:
            raise StoreError("credentials 文件中没有可识别的平台配置（--all）")
    else:
        targets = [args.platform]

    results: List[SubmitResult] = []
    errors: List[Dict[str, str]] = []
    for key in targets:
        try:
            adapter = get_adapter(key, creds)
            problems = adapter.check()
            if problems:
                raise StoreError("；".join(problems))
            results.append(adapter.publish(release, dry_run=args.dry_run))
        except StoreError as e:
            errors.append({"platform": key, "error": str(e)})

    if args.json:
        _json_out({"release": _release_payload(release), "results": [_publish_row(r) for r in results], "errors": errors})
    else:
        print(f"应用: {release.package_name}  v{release.version_name or '?'} (code {release.version_code})  包: {release.artifact_path()}")
        for r in results:
            print(r.summary)
        for e in errors:
            print(f"[{e['platform']}] 错误: {e['error']}", file=sys.stderr)

    if errors and not results:
        return 1
    return 2 if errors else 0


def _release_payload(release) -> Dict[str, Any]:
    return {
        "package_name": release.package_name,
        "version_name": release.version_name,
        "version_code": release.version_code,
        "artifact": release.artifact_path(),
        "track": release.track,
    }


def cmd_status(args: argparse.Namespace) -> int:
    creds = load_credentials(args.credentials)
    package = getattr(args, "package", "") or ""
    if not package and getattr(args, "app", None):
        catalog = get_catalog(args.catalog)
        app = catalog.get_app(args.app)
        package = app.get("package_name") or ""
        if not package:
            raise StoreError(f"应用 {args.app} 尚未配置 package_name")
    if not package:
        raise StoreError("请提供 --package 或 --app")

    if args.platform:
        targets = [args.platform]
    else:
        targets = [k for k in creds if k in PLATFORM_VALUES]
        if not targets:
            raise StoreError("credentials 文件中没有可识别的平台配置")

    results: List[StoreStatus] = []
    errors: List[Dict[str, str]] = []
    for key in targets:
        try:
            adapter = get_adapter(key, creds)
            results.append(adapter.query_status(package))
        except StoreError as e:
            errors.append({"platform": key, "error": str(e)})

    if args.json:
        _json_out({"package": package, "results": [_status_row(r) for r in results], "errors": errors})
    else:
        for r in results:
            print(r.summary)
        for e in errors:
            print(f"[{e['platform']}] 错误: {e['error']}", file=sys.stderr)

    if errors and not results:
        return 1
    return 2 if errors else 0


def cmd_platforms(args: argparse.Namespace) -> int:
    rows: List[Dict[str, Any]] = []
    for item in list_platforms():
        rows.append(
            {
                "platform": item["platform"],
                "display_name": item["display_name"],
                "availability": item["availability"],
                "credential_fields": item["credential_fields"],
            }
        )
    if args.json:
        _json_out(rows)
    else:
        print(f"{'平台':<10}{'名称':<18}{'状态':<10}必需凭证字段")
        for r in rows:
            status = "可用" if r["availability"] == "ready" else "待接入"
            print(f"{r['platform']:<10}{r['display_name']:<18}{status:<10}{', '.join(r['credential_fields'])}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    creds = load_credentials(args.credentials)
    rows: List[Dict[str, Any]] = []
    for key in creds:
        if key not in PLATFORM_VALUES:
            continue
        try:
            adapter = get_adapter(key, creds)
            problems = adapter.check()
            rows.append({"platform": key, "ok": not problems, "message": "" if not problems else "；".join(problems)})
        except StoreError as e:
            rows.append({"platform": key, "ok": False, "message": str(e)})

    if args.json:
        _json_out({"validated": rows})
    else:
        for r in rows:
            mark = "✔" if r["ok"] else "✘"
            print(f"{mark} {r['platform']}: {r['message'] or 'OK'}")
    return 0 if all(r["ok"] for r in rows) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="appstore",
        description="一键发布 App 到各大应用商店，并查询审核进度与已上架版本号",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--catalog", default="apps/catalog.json", help="应用目录 JSON 路径（默认 apps/catalog.json）")
    sub = parser.add_subparsers(dest="command", required=True)

    apps = sub.add_parser("apps", help="列出应用目录（按分类）")
    apps.set_defaults(func=cmd_apps)

    p = sub.add_parser("publish", help="发布/更新应用到指定平台")
    p.add_argument("--app", help="应用目录 ID（appstore apps 查看）")
    p.add_argument("--platform", choices=sorted(PLATFORM_VALUES), help="目标平台")
    p.add_argument("--all", action="store_true", help="逐个发布到 credentials 文件中配置的所有平台")
    p.add_argument("--credentials", default=DEFAULT_CREDENTIALS, help="凭证 JSON 文件路径（默认 config/credentials.json）")
    p.add_argument("--catalog", default="apps/catalog.json", help="应用目录 JSON 路径")
    p.add_argument("--release", help="release 清单(JSON/YAML)路径（可选，与 --app 二选一或合并覆盖）")
    p.add_argument("--dry-run", action="store_true", help="只做校验，不实际提交")
    p.add_argument("--track", help="覆盖清单中的 Google Play 轨道(production/beta/alpha/internal)")
    p.add_argument("--version-name", help="覆盖版本名")
    p.add_argument("--version-code", type=int, help="覆盖版本号(versionCode)")
    p.add_argument("--release-notes", help="本次更新说明（whatsnew）")
    p.add_argument("--apk-path", help="显式指定 APK 路径（覆盖目录配置）")
    p.add_argument("--aab-path", help="显式指定 AAB 路径（覆盖目录配置）")
    p.add_argument("--online-time", help="定时上线（小米）：毫秒时间戳或 'YYYY-MM-DD HH:MM' 本地时间")
    p.set_defaults(func=cmd_publish)

    s = sub.add_parser("status", help="查询审核进度与已上架版本")
    s.add_argument("--app", help="应用目录 ID")
    s.add_argument("--platform", choices=sorted(PLATFORM_VALUES), help="指定平台(不填则查凭证中所有平台)")
    s.add_argument("--package", help="应用包名（不填则用 --app 的包名）")
    s.add_argument("--credentials", default=DEFAULT_CREDENTIALS, help="凭证 JSON 文件路径")
    s.add_argument("--catalog", default="apps/catalog.json", help="应用目录 JSON 路径")
    s.set_defaults(func=cmd_status)

    v = sub.add_parser("validate", help="校验凭证与依赖")
    v.add_argument("--credentials", default=DEFAULT_CREDENTIALS, help="凭证 JSON 文件路径")
    v.set_defaults(func=cmd_validate)

    pl = sub.add_parser("platforms", help="列出已注册平台")
    pl.set_defaults(func=cmd_platforms)

    web = sub.add_parser("web", help="启动 Web 可视化看板")
    web.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    web.add_argument("--port", type=int, default=8090, help="监听端口（默认 8090）")
    web.add_argument("--credentials", default=DEFAULT_CREDENTIALS, help="凭证 JSON 文件路径")
    web.add_argument("--catalog", default="apps/catalog.json", help="应用目录 JSON 路径")
    web.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    web.set_defaults(func=cmd_web)

    return parser


def cmd_web(args: argparse.Namespace) -> int:
    """Web 看板入口（延迟导入避免额外依赖）。"""
    from .web import run_server

    return run_server(host=args.host, port=args.port, credentials_path=args.credentials, catalog_path=args.catalog, open_browser=args.open)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())