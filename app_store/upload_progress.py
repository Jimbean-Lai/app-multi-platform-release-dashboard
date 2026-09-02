# -*- coding: utf-8 -*-
"""上传进度工具：为各平台 requests 上传提供实时字节进度回调。

背景：requests 对 data=<文件对象> 会分块流式读（16KB/次），可直接包装文件；
      但对 files= 的 multipart 会 read(-1) 一次读光内存，必须用
      requests_toolbelt.MultipartEncoderMonitor 实现分块流式上传。
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple


class ProgressFile:
    """包装文件对象：read 时按块回调 cb(sent_bytes, total_bytes)。

    用于 requests data=<file> 流式上传（如华为 OBS PUT）。
    """

    def __init__(self, path: str, total: int, cb: Optional[Callable[[int, int], None]],
                 report_bytes: int = 512 * 1024) -> None:
        self._f = open(path, "rb")
        self._total = total
        self._cb = cb
        self._sent = 0
        self._last = -1
        self._report = report_bytes

    def read(self, n: int = -1) -> bytes:
        data = self._f.read(n)
        if data:
            self._sent += len(data)
            cur = self._sent // self._report
            if cur > self._last:
                self._last = cur
                if self._cb:
                    self._cb(self._sent, self._total)
        return data

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._f.seek(offset, whence)

    def tell(self) -> int:
        return self._f.tell()

    def close(self) -> None:
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        self.close()


def make_multipart_monitor(fields: List[Tuple[str, Any]], file_size: int,
                           cb: Optional[Callable[[int, int], None]]):
    """构造 MultipartEncoderMonitor 用于 multipart 文件上传。

    fields: [(字段名, 值)]，其中文件项为 (name, fileobj, mime) 三元组。
    file_size: 实际文件字节数（用于显示 total，抵消 multipart 边界头开销）。
    返回可作 requests.post 的 data 传入；无 cb 时退化为普通 MultipartEncoder。
    """
    from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

    encoder = MultipartEncoder(fields=fields)

    if not cb:
        return encoder

    def _on_progress(monitor) -> None:
        # monitor.bytes_read 含 multipart 边界头（比文件多几百字节），对显示 clamp 到 file_size
        sent = min(monitor.bytes_read, file_size)
        cb(sent, file_size)

    return MultipartEncoderMonitor(encoder, _on_progress)
