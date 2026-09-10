"""适配器基类与统一异常。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from .models import Platform, Release, SubmitResult, StoreStatus


class StoreError(RuntimeError):
    """商店接入层的可预期错误（凭证缺失、接口返回错误、未接入等）。"""


class StoreAdapter(ABC):
    """每个应用商店实现一个子类。

    - publish()：把 Release 发布/更新到商店（dry_run=True 时只校验不提交）
    - query_status()：查询审核进度与已上架版本
    - required_credential_fields：构造时校验的必需凭证字段
    - availability："ready" = 可用；"scaffold" = 骨架（接口待接入）
    """

    platform: Platform
    display_name: str = ""
    required_credential_fields: tuple = ()
    availability: str = "ready"

    def __init__(self, credentials: Dict[str, Any]) -> None:
        missing = [k for k in self.required_credential_fields if not credentials.get(k)]
        if missing:
            raise StoreError(
                f"{self.display_name} 凭证缺失字段: {', '.join(missing)}"
            )
        self.credentials = credentials or {}

    def check(self) -> List[str]:
        """返回环境/依赖问题列表（可容忍的预警），空列表表示就绪。"""
        return []

    @abstractmethod
    def publish(self, release: Release, dry_run: bool = False) -> SubmitResult:
        """发布（dry_run=True 时只做校验，不真实提交）。"""

    @abstractmethod
    def query_status(self, package_name: str) -> StoreStatus:
        """查询审核进度与已上架版本。"""

    def release_now(self, package_name: str) -> dict:
        """审核通过但未到定时上架时间的版本改为立即上架。

        仅官方提供该能力的平台实现（华为 on-shelf-time 接口）；
        其余平台保持默认抛错。
        """
        raise StoreError(f"{self.display_name or self.platform.value} 官方未提供「审核通过后立即上线」接口")
