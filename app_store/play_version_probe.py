#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract current published version from Google Play page (What's New).
Usage: python3 play_version_probe.py <package_name>
Output: version string (e.g. 4.19.0) or empty
"""
import re
import sys

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36")


def extract_version(html: str) -> str:
    """Use string-find to locate [["x.y.z"]] structures and validate format."""
    seen = []
    i = 0
    while True:
        i = html.find('[[["', i)
        if i < 0:
            break
        j = html.find('"]]', i + 4)
        if j > 0 and j - i < 25:
            ver = html[i + 4:j]
            if re.fullmatch(r"\d+\.\d+\.\d+", ver):
                seen.append(ver)
        i += 4
    # 过滤明显非应用版本（1.x/2.x/0.x 库版本），返回第一个像样的
    for v in seen:
        if not v.startswith(("1.", "2.", "0.")):
            return v
    # 退而求其次返回第一个（或为空）
    return seen[0] if seen else ""


def main() -> None:
    pkg = sys.argv[1] if len(sys.argv) > 1 else ""
    if not pkg:
        print("")
        return
    try:
        import requests
        r = requests.get(
            "https://play.google.com/store/apps/details?id=" + pkg + "&hl=zh_CN",
            headers={"User-Agent": UA}, timeout=15,
        )
        print(extract_version(r.text))
    except Exception:
        print("")


if __name__ == "__main__":
    main()
