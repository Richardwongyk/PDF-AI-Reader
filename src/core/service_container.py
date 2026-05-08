"""
Dependency injection container — adapted from PDFCrop (inoueakimitsu/pdfcrop, MIT).

Three lifetime modes:
  - Instance   : pre-created object, permanent (e.g. ConfigManager)
  - Singleton  : lazy-init on first get(), then cached (e.g. ChromaRepo, AI clients)
  - Factory    : new instance every get() (e.g. DocumentEngine per document)

Usage:
    container = ServiceContainer()
    container.register_instance("config", ConfigManager())
    container.register_singleton("chroma", lambda: ChromaRepo(...))
    container.register_factory("doc_engine", lambda: DocumentEngine(...))

    config = container.get("config")
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

_logger = logging.getLogger(__name__)


class ServiceContainer:
    """Lightweight DI container with three lifetime modes."""

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}      # register_instance destinations
        self._factories: dict[str, Callable] = {}  # name → factory callable
        self._singletons: dict[str, Any] = {}      # cache for singleton results

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_instance(self, name: str, instance: Any) -> None:
        """Register an already-constructed instance. Always returns the same object."""
        self._instances[name] = instance
        _logger.info("ServiceContainer: 注册实例 %s → %s", name, type(instance).__name__)

    def register_singleton(self, name: str, factory: Callable[[], Any]) -> None:
        """Register a lazy singleton. Factory is called once, result cached forever."""
        def _singleton_factory() -> Any:
            if name not in self._singletons:
                t0 = time.perf_counter()
                _logger.info("ServiceContainer: 首次创建单例 %s ...", name)
                self._singletons[name] = factory()
                elapsed = time.perf_counter() - t0
                _logger.info("ServiceContainer: 单例 %s 创建完成 (%.2fs)", name, elapsed)
            return self._singletons[name]

        self._factories[name] = _singleton_factory
        _logger.debug("ServiceContainer: 注册单例工厂: %s", name)

    def register_factory(self, name: str, factory: Callable[[], Any]) -> None:
        """Register a transient factory. Every get() creates a new instance."""
        self._factories[name] = factory
        _logger.debug("Registered factory: %s", name)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, name: str) -> Any:
        """Retrieve a service by name.

        Raises ValueError if the name is not registered.
        """
        if name in self._instances:
            return self._instances[name]

        if name in self._factories:
            instance = self._factories[name]()
            # 区分 Factory（每次新建）和 Singleton（首次创建后缓存）
            is_new_singleton = name in self._singletons and instance is self._singletons[name]
            if is_new_singleton:
                _logger.debug("ServiceContainer.get(%s) → 命中单例缓存", name)
            else:
                _logger.debug("ServiceContainer.get(%s) → Factory 新建 %s", name, type(instance).__name__)
            return instance

        available = list(self._instances.keys()) + list(self._factories.keys())
        _logger.error("ServiceContainer.get(%s) → 未注册! 可用: %s", name, available)
        raise ValueError(f"Service '{name}' not registered. Available: {available}")

    def has(self, name: str) -> bool:
        """Check whether a service is registered."""
        return name in self._instances or name in self._factories

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Destroy all singleton services in reverse registration order.

        Each service that has a close() or shutdown() method will have it called.
        """
        for name, service in reversed(list(self._singletons.items())):
            for method in ("close", "shutdown"):
                fn = getattr(service, method, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        _logger.warning("Error during %s.%s()", name, method, exc_info=True)
        self._singletons.clear()
        self._instances.clear()
        self._factories.clear()
        _logger.info("ServiceContainer shutdown complete")

    @property
    def registered_services(self) -> list[str]:
        """Return names of all registered services."""
        return list(self._instances.keys()) + list(self._factories.keys())
