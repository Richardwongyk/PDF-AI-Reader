"""
配置管理器 —— YAML 配置文件读写与热加载。

启动时加载 config.yaml，提供配置读取接口，支持运行时修改并自动保存。
修改后发射 config_changed 信号通知所有订阅模块。
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QObject, Signal

from src.core.models import AppConfig, ModelConfig, RAGConfig, RoutingConfig, UIConfig

_logger = logging.getLogger(__name__)


class ConfigManager(QObject):
    """应用配置管理器。

    负责：
    - 启动时加载 config.yaml（若不存在则创建默认配置）
    - 提供配置读取接口（返回只读副本）
    - 支持运行时部分更新并自动保存
    - 修改后发射 config_changed 信号
    - 从 .env 文件加载 API Keys
    """

    config_changed = Signal(AppConfig)
    # 发射时机：配置被 update() 修改并保存后

    def __init__(self, config_path: str | None = None) -> None:
        """初始化配置管理器。

        Args:
            config_path: config.yaml 文件路径。默认为当前工作目录下的 config.yaml。
        """
        super().__init__()
        if config_path is None:
            config_path = str(Path.cwd() / "config.yaml")
        self._path: str = config_path
        self._config: AppConfig = self.load()

        # 从 .env 文件加载 API Keys
        self._load_env()

    def load(self) -> AppConfig:
        """从 YAML 文件加载配置，使用 Pydantic 进行 Schema 严格校验。

        若文件不存在，创建默认配置并写入文件。
        若文件存在但 YAML 解析失败或 Schema 校验失败，
        自动备份损坏的配置文件并生成全新默认配置，
        确保软件永远不会因配置损坏而无法启动。

        Returns:
            AppConfig 实例。
        """
        if not os.path.exists(self._path):
            _logger.info("配置文件不存在，创建默认配置: %s", self._path)
            config = AppConfig()
            self._save_to_file(config)
            return config

        yaml_ok = True
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError) as e:
            _logger.warning("配置文件 YAML 解析失败: %s", e)
            yaml_ok = False

        if yaml_ok:
            try:
                from pydantic import ValidationError
                config = AppConfig.model_validate(raw)
                _logger.info("配置文件校验通过: %s", self._path)
                return config
            except ValidationError as e:
                _logger.error("配置文件 Schema 校验失败: %s", e)

        # YAML 解析失败 或 Pydantic 校验失败 → 备份并重建
        self._backup_corrupt_config()
        config = AppConfig()
        self._save_to_file(config)
        _logger.info("已重建默认配置: %s", self._path)
        return config

    def _backup_corrupt_config(self) -> None:
        """备份损坏的配置文件，避免数据丢失。"""
        import shutil, time
        backup_path = self._path + f".corrupt.{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(self._path, backup_path)
            _logger.warning("已备份损坏的配置到: %s", backup_path)
        except OSError:
            pass

    def save(self) -> None:
        """将当前配置写回 YAML 文件。"""
        self._save_to_file(self._config)

    def _save_to_file(self, config: AppConfig) -> None:
        """内部方法：将 AppConfig 序列化写入 YAML 文件。

        Args:
            config: 要保存的配置对象。
        """
        data: dict[str, Any] = {
            "model": config.model.model_dump(),
            "rag": config.rag.model_dump(),
            "routing": config.routing.model_dump(),
            "ui": config.ui.model_dump(),
            "api_keys": config.api_keys,
        }
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def get(self) -> AppConfig:
        """获取当前配置的深拷贝副本。

        Returns:
            AppConfig 实例（修改不影响内部状态）。
        """
        return self._config.model_copy(deep=True)

    def update(self, partial: dict[str, Any]) -> None:
        """部分更新配置（深度合并）。

        例如 update({"ui": {"theme": "dark"}}) 仅修改主题，
        其余配置保持不变。修改后自动 save() 并发射 config_changed。

        Args:
            partial: 需要更新的配置字段字典（嵌套结构）。
        """
        full = self._config.model_dump()

        def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> None:
            """递归合并 overrides 到 base。"""
            for key, value in overrides.items():
                if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                    _deep_merge(base[key], value)
                else:
                    base[key] = value

        _deep_merge(full, partial)

        # 重新构建 AppConfig
        self._config = AppConfig(
            model=ModelConfig(**full["model"]),
            rag=RAGConfig(**full.get("rag", {})),
            routing=RoutingConfig(**full["routing"]),
            ui=UIConfig(**full["ui"]),
            api_keys=full.get("api_keys", {}),
        )

        self.save()
        _logger.info("配置已更新并保存")
        self.config_changed.emit(self._config.model_copy(deep=True))

    def get_api_key(self, provider: str) -> str | None:
        """获取指定服务商的 API Key。

        查找顺序：self._config.api_keys → 环境变量。

        Args:
            provider: 服务商标识（如 "openai", "deepseek"）。

        Returns:
            API Key 字符串或 None。
        """
        # 先从配置中的 api_keys 查找
        if provider in self._config.api_keys:
            return self._config.api_keys[provider]

        # 再从环境变量查找
        env_key = f"{provider.upper()}_API_KEY"
        return os.environ.get(env_key)

    def _load_env(self) -> None:
        """从 .env 文件加载环境变量。

        使用 python-dotenv 库（如果可用）加载 .env 文件中的 API Key。
        已存在于系统环境变量中的值不会被 .env 覆盖。
        """
        try:
            from dotenv import load_dotenv
            env_path = Path.cwd() / ".env"
            if env_path.exists():
                load_dotenv(env_path, override=False)
                _logger.info(".env 文件已加载")
        except ImportError:
            pass  # python-dotenv 未安装时静默跳过
