"""
PDF AI Reader — 程序主入口。

创建 QApplication，初始化所有服务（ServiceContainer + 懒加载），
构建 MainWindow，启动事件循环。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 必须在任何 chromadb 导入之前禁用 telemetry，避免 posthog 报错
os.environ["ANONYMIZED_TELEMETRY"] = "False"
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL + 1)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

# Windows 上关闭 Chromium 沙盒
import platform
if platform.system() == "Windows":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu-sandbox"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtWebEngineCore import QWebEngineProfile

from src.core.models import AppConfig
from src.core.service_container import ServiceContainer
from src.data.config_manager import ConfigManager


def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def build_services() -> ServiceContainer:
    """构建所有核心服务并注册到 ServiceContainer。

    初始化顺序：
    1. 配置管理器（最先，其他服务依赖配置）
    2. 基础设施层（PageCache 等轻量服务）
    3. 领域层（纯业务逻辑）
    4. 重量级基础设施（ChromaRepo、AI 客户端 — 懒加载单例）
    5. 工厂模式（DocumentEngine、KnowledgeEngine — 每次打开文档创建）

    Returns:
        包含所有已注册服务的 ServiceContainer。
    """
    t_start = __import__('time').perf_counter()
    logging.info("build_services: 开始构建服务容器...")
    container = ServiceContainer()

    # ── 1. 配置管理器 (eager — 最轻量，其他服务依赖) ──
    config_path = Path(__file__).parent.parent / "config.yaml"
    config_manager = ConfigManager(str(config_path))
    container.register_instance("config_manager", config_manager)
    config: AppConfig = config_manager.get()

    # ── 2. 基础设施层 (eager — 轻量) ──
    from src.infra.page_cache import PageCache
    container.register_singleton("page_cache", lambda: PageCache(max_cache_size_mb=1024.0))

    from src.infra.ai_cache import AICache
    container.register_singleton("ai_cache", lambda: AICache("data/ai_cache.db"))

    # ── 3. 领域层 (eager — 纯业务逻辑，无 IO) ──
    data_dir = Path(__file__).parent.parent / "data"
    glossary_dir = data_dir / "glossary"

    from src.core.glossary_manager import GlossaryManager
    glossary_manager = GlossaryManager(str(glossary_dir))
    container.register_instance("glossary_manager", glossary_manager)

    from src.core.navigator import Navigator
    navigator = Navigator()
    container.register_instance("navigator", navigator)

    # ── 4. 重量级基础设施 (LAZY SINGLETON — 延迟到首次使用) ──

    def _build_chroma_repo():
        knowledge_dir = data_dir / "knowledge_bases"
        from src.data.chroma_repo import ChromaRepo
        return ChromaRepo(str(knowledge_dir))

    container.register_singleton("chroma_repo", _build_chroma_repo)

    def _build_embed_client():
        """延迟创建嵌入模型客户端（Ollama 优先，Mock 回退）。"""
        from src.core.ai_engine import BaseLLMClient, MockLLMClient, OllamaClient
        try:
            client = OllamaClient(
                model=config.model.embed_local,
                host=config.model.ollama_host,
            )
            if client.check_availability():
                logging.info("嵌入服务使用本地模型: %s", config.model.embed_local)
                return client
            raise RuntimeError("Ollama 服务未连接")
        except Exception:
            logging.info("本地嵌入模型不可用，使用模拟向量（语义检索精度降低）")
            return MockLLMClient()

    container.register_singleton("embed_client", _build_embed_client)

    def _build_embedding_service():
        from src.core.knowledge_engine import EmbeddingService
        return EmbeddingService(container.get("embed_client"))

    container.register_singleton("embedding_service", _build_embedding_service)

    def _build_knowledge_engine():
        from src.core.knowledge_engine import KnowledgeEngine
        return KnowledgeEngine(
            container.get("embedding_service"),
            container.get("chroma_repo"),
        )

    container.register_singleton("knowledge_engine", _build_knowledge_engine)

    def _build_ai_engine():
        """延迟构建 AI 引擎（~3s LiteLLM 初始化推迟到首次翻译/问答时）。"""
        from src.core.ai_engine import (
            AIEngine, BaseLLMClient, HybridModelRouter,
            LiteLLMClient, MockLLMClient, QAService, TranslationService,
        )

        primary_client: BaseLLMClient
        cloud_provider = config.model.cloud
        cloud_api_key = config_manager.get_api_key(cloud_provider)
        if cloud_api_key:
            logging.info("使用云端模型: %s", cloud_provider)
            primary_client = LiteLLMClient(model=cloud_provider, api_key=cloud_api_key)
        else:
            logging.info("未配置云端API Key，使用模拟客户端（测试模式）")
            primary_client = MockLLMClient()

        fallback_client = MockLLMClient()
        router = HybridModelRouter(primary_client, fallback_client, config)

        translation_service = TranslationService(
            router,
            glossary_entries=glossary_manager.get_entries(["math", "cs_ml", "physics"]),
        )
        qa_service = QAService(router)
        return AIEngine(router, translation_service, qa_service, config)

    container.register_singleton("ai_engine", _build_ai_engine)

    # ── 5. 工厂模式 (per-document instances) ──

    def _build_document_engine():
        from src.core.pdf_engine import DocumentEngine
        page_cache = container.get("page_cache")
        return DocumentEngine(config, page_cache=page_cache)

    container.register_factory("document_engine", _build_document_engine)

    logging.info("所有核心服务已注册 (懒加载单例: chroma_repo, embed_client, knowledge_engine, ai_engine)。"
                 "已注册: %s", container.registered_services)

    elapsed = __import__('time').perf_counter() - t_start
    logging.info("build_services: 完成 (%.2fs, %d 个服务注册)", elapsed, len(container.registered_services))
    return container


def main() -> int:
    setup_logging()

    # 全局异常处理（四级严重度）
    from src.core.error_handler import ErrorHandler
    _error_handler = ErrorHandler()
    _error_handler.setup_global_exception_hook()

    logging.info("PDF AI Reader 启动中...")
    t_start = __import__('time').perf_counter()

    app = QApplication(sys.argv)
    app.setApplicationName("PDF AI Reader")
    app.setApplicationVersion("1.0.0")
    logging.info("QApplication 创建完成 (%.2fs)", __import__('time').perf_counter() - t_start)

    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)

    try:
        t_build = __import__('time').perf_counter()
        services = build_services()

        # 将全局异常处理的父窗口设为主窗口（用于错误对话框）
        from src.ui.main_window import MainWindow
        t_ui = __import__('time').perf_counter()
        window = MainWindow(services)
        logging.info("MainWindow 创建完成 (%.2fs)", __import__('time').perf_counter() - t_ui)
        _error_handler.set_parent_widget(window)

        window.show()
        total = __import__('time').perf_counter() - t_start
        logging.info("主窗口已显示。总启动时间: %.2fs", total)

        # 后台预热云端 LLM 连接（首次调用 ~47s，预热后降至 ~2s）
        ai_engine = services.get("ai_engine")
        QTimer.singleShot(1000, ai_engine.warmup_cloud)

        return app.exec()

    except Exception as e:
        logging.critical("启动失败: %s", e, exc_info=True)
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生错误:\n\n{e}\n\n请检查 logs/app.log 获取详细信息。",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
