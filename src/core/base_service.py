"""
服务基类 —— 所有 Core 层服务的抽象基类。

提供服务生命周期管理、线程支持、配置注入和日志记录的统一模板。
"""

import abc
import logging

from PySide6.QtCore import QObject


class _BaseServiceMeta(type(QObject), abc.ABCMeta):
    """组合 QObject 和 ABC 的元类，解决元类冲突。"""
    pass


class BaseService(QObject, metaclass=_BaseServiceMeta):
    """Core 层服务的抽象基类。

    所有业务逻辑服务（DocumentEngine、KnowledgeEngine、AIEngine 等）
    均应继承此类，获得统一的：
    - 日志记录器
    - 配置访问
    - 线程管理基类（QObject 提供 moveToThread 支持）
    """

    def __init__(self, parent: QObject | None = None) -> None:
        """初始化服务基类。

        Args:
            parent: Qt 父对象，用于生命周期管理。
        """
        super().__init__(parent)
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def logger(self) -> logging.Logger:
        """获取服务专属的日志记录器。"""
        return self._logger
