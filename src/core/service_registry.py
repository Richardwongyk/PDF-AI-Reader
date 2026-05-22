"""
服务注册中心 —— 管理所有 Core 层服务的单例定位器。

UI 层通过此注册中心获取所有 Core 服务实例，
而不是直接 import 具体服务类，从而实现松耦合。
"""

import logging
from typing import Any

_logger = logging.getLogger(__name__)


class CoreServiceRegistry:
    """核心服务定位器。

    在应用启动时由 main.py 初始化并注入所有服务实例。
    UI 层通过此注册中心获取所需服务。

    用法：
        registry = CoreServiceRegistry()
        registry.register("document_engine", engine)
        ...
        engine = registry.get("document_engine")
    """

    def __init__(self) -> None:
        """初始化空的注册中心。"""
        self._services: dict[str, Any] = {}

    def register(self, name: str, service: Any) -> None:
        """注册一个服务实例。

        Args:
            name: 服务名称（约定使用 snake_case，如 "document_engine"）。
            service: 服务实例。
        """
        _logger.debug("注册服务: %s → %s", name, type(service).__name__)
        self._services[name] = service

    def get(self, name: str) -> Any:
        """获取已注册的服务实例。

        Args:
            name: 服务名称。

        Returns:
            服务实例。

        Raises:
            KeyError: 服务未注册时。
        """
        if name not in self._services:
            _logger.error("服务未注册: %s (可用: %s)", name, list(self._services.keys()))
            raise KeyError(f"服务 '{name}' 未注册。可用服务: {list(self._services.keys())}")
        return self._services[name]

    def unregister(self, name: str) -> None:
        """注销一个服务。

        Args:
            name: 服务名称。
        """
        if name in self._services:
            _logger.debug("注销服务: %s", name)
        self._services.pop(name, None)

    @property
    def registered_services(self) -> list[str]:
        """返回所有已注册的服务名称列表。"""
        return list(self._services.keys())
