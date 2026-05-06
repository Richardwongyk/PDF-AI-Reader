"""
配置管理器 —— YAML 配置文件读写与热加载。

启动时加载 config.yaml，提供配置读取接口，支持运行时修改并自动保存。
修改后发射 config_changed 信号通知所有订阅模块。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, Signal

from src.core.models import AppConfig, ModelConfig, RoutingConfig, UIConfig


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
        """从 YAML 文件加载配置。

        若文件不存在，创建默认配置并写入文件。
        若文件存在但格式有误，使用默认配置覆盖。

        Returns:
            AppConfig 实例。
        """
        if not os.path.exists(self._path):
            config = AppConfig()
            self._save_to_file(config)
            return config

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw: dict = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError):
            raw = {}

        # 深度合并 raw dict 到默认 AppConfig
        config = AppConfig()

        if "model" in raw and isinstance(raw["model"], dict):
            model_data = config.model.model_dump()
            model_data.update(raw["model"])
            config.model = ModelConfig(**model_data)

        if "routing" in raw and isinstance(raw["routing"], dict):
            routing_data = config.routing.model_dump()
            routing_data.update(raw["routing"])
            config.routing = RoutingConfig(**routing_data)

        if "ui" in raw and isinstance(raw["ui"], dict):
            ui_data = config.ui.model_dump()
            ui_data.update(raw["ui"])
            config.ui = UIConfig(**ui_data)

        if "api_keys" in raw and isinstance(raw["api_keys"], dict):
            config.api_keys.update(raw["api_keys"])

        return config

    def save(self) -> None:
        """将当前配置写回 YAML 文件。"""
        self._save_to_file(self._config)

    def _save_to_file(self, config: AppConfig) -> None:
        """内部方法：将 AppConfig 序列化写入 YAML 文件。

        Args:
            config: 要保存的配置对象。
        """
        data: dict = {
            "model": config.model.model_dump(),
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

    def update(self, partial: dict) -> None:
        """部分更新配置（深度合并）。

        例如 update({"ui": {"theme": "dark"}}) 仅修改主题，
        其余配置保持不变。修改后自动 save() 并发射 config_changed。

        Args:
            partial: 需要更新的配置字段字典（嵌套结构）。
        """
        full = self._config.model_dump()

        def _deep_merge(base: dict, overrides: dict) -> None:
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
            routing=RoutingConfig(**full["routing"]),
            ui=UIConfig(**full["ui"]),
            api_keys=full.get("api_keys", {}),
        )

        self.save()
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
        except ImportError:
            pass  # python-dotenv 未安装时静默跳过
