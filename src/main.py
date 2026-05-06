"""
PDF AI Reader —— 程序主入口。

创建 QApplication，初始化所有服务，构建 MainWindow，启动事件循环。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# 必须在任何 chromadb 导入之前禁用 telemetry，避免 posthog 报错
os.environ["ANONYMIZED_TELEMETRY"] = "False"
# ChromaDB 的 posthog 版本不兼容，直接屏蔽其日志
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL + 1)
# LiteLLM 的模型成本表网络超时也屏蔽（有本地回退）
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

# 【关键修复】解决 QtWebEngineProcess.exe 崩溃 (STATUS_BREAKPOINT)
# Windows 上需要关闭 Chromium 沙盒；macOS/Linux 上不禁用（否则安全警告或启动失败）
import platform
if platform.system() == "Windows":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu-sandbox"

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtWebEngineCore import QWebEngineProfile

from src.core.models import AppConfig
from src.core.pdf_engine import DocumentEngine
from src.core.ai_engine import (
    AIEngine,
    BaseLLMClient,
    HybridModelRouter,
    LiteLLMClient,
    MockLLMClient,
    OllamaClient,
    QAService,
    TranslationService,
)
from src.core.knowledge_engine import EmbeddingService, KnowledgeEngine
from src.core.glossary_manager import GlossaryManager
from src.core.navigator import Navigator
from src.core.service_registry import CoreServiceRegistry
from src.data.chroma_repo import ChromaRepo
from src.data.config_manager import ConfigManager
from src.ui.main_window import MainWindow


def setup_logging() -> None:
    """配置全局日志。

    输出到 logs/app.log 和控制台。
    """
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


def setup_exception_handler() -> None:
    """设置全局未捕获异常钩子。

    将异常写入日志，并弹出用户友好的错误提示。
    """
    import traceback

    def _handler(exc_type, exc_value, exc_tb):
        logging.critical(
            "未捕获的异常:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        # 尝试弹出错误对话框
        try:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("意外错误")
            msg.setText("应用遇到意外问题，已记录错误日志。\n请重启应用。")
            msg.setDetailedText(
                f"错误类型: {exc_type.__name__}\n"
                f"错误信息: {exc_value}\n\n"
                f"详细日志已保存到: logs/app.log"
            )
            msg.exec()
        except Exception:
            pass

    sys.excepthook = _handler


def build_services() -> CoreServiceRegistry:
    """构建所有核心服务并注册到服务定位器。

    初始化顺序：
    1. 配置管理器（最先，其他服务依赖配置）
    2. 数据层（ChromaRepo、GlossaryRepo）
    3. 核心引擎（DocumentEngine、AIEngine、KnowledgeEngine）
    4. 辅助服务（GlossaryManager、Navigator）

    Returns:
        包含所有已注册服务的 CoreServiceRegistry。
    """
    registry = CoreServiceRegistry()

    # --- 1. 配置管理器 ---
    config_path = Path(__file__).parent.parent / "config.yaml"
    config_manager = ConfigManager(str(config_path))
    registry.register("config_manager", config_manager)

    config: AppConfig = config_manager.get()

    # --- 2. 数据层 ---
    data_dir = Path(__file__).parent.parent / "data"
    knowledge_dir = data_dir / "knowledge_bases"
    glossary_dir = data_dir / "glossary"

    chroma_repo = ChromaRepo(str(knowledge_dir))
    registry.register("chroma_repo", chroma_repo)

    # --- 3. AI 客户端 ---
    # 主客户端：有 API Key→LiteLLM云端，无→Mock模拟
    primary_client: BaseLLMClient
    default_cloud_provider = config.model.cloud
    cloud_api_key = config_manager.get_api_key(default_cloud_provider)
    if cloud_api_key:
        logging.info("使用云端模型: %s", default_cloud_provider)
        primary_client = LiteLLMClient(model=default_cloud_provider, api_key=cloud_api_key)
    else:
        logging.info("未配置云端API Key，使用模拟客户端（测试模式）")
        primary_client = MockLLMClient()

    # 模型路由器（Mock 作为最终回退，保证不崩溃）
    fallback_client = MockLLMClient()
    router = HybridModelRouter(primary_client, fallback_client, config)

    # --- 4. 核心引擎 ---
    # 文档引擎
    document_engine = DocumentEngine(config)
    registry.register("document_engine", document_engine)

    # 嵌入服务：本地 Ollama (bge-m3) 优先，不可用时使用模拟向量
    embed_client: BaseLLMClient
    try:
        embed_client = OllamaClient(
            model=config.model.embed_local,
            host=config.model.ollama_host,
        )
        if embed_client.check_availability():
            logging.info("嵌入服务使用本地模型: %s", config.model.embed_local)
        else:
            raise RuntimeError("Ollama 服务未连接")
    except Exception:
        logging.info("本地嵌入模型不可用，使用模拟向量（语义检索精度降低）")
        embed_client = MockLLMClient()
    embed_service = EmbeddingService(embed_client)
    knowledge_engine = KnowledgeEngine(embed_service, chroma_repo)
    registry.register("knowledge_engine", knowledge_engine)

    # 术语表管理器
    glossary_manager = GlossaryManager(str(glossary_dir))
    registry.register("glossary_manager", glossary_manager)

    # 翻译服务
    translation_service = TranslationService(
        router,
        glossary_entries=glossary_manager.get_entries(["math", "cs_ml", "physics"]),
    )

    # 问答服务
    qa_service = QAService(router)

    # AI 引擎
    ai_engine = AIEngine(router, translation_service, qa_service, config)
    registry.register("ai_engine", ai_engine)

    # --- 5. 辅助服务 ---
    navigator = Navigator()
    registry.register("navigator", navigator)

    logging.info("所有核心服务初始化完成。已注册: %s", registry.registered_services)
    return registry


def main() -> int:
    """应用主入口。

    Returns:
        退出码（0 = 正常）。
    """
    setup_logging()
    setup_exception_handler()

    logging.info("PDF AI Reader 启动中...")

    # 创建 Qt 应用
    app = QApplication(sys.argv)
    app.setApplicationName("PDF AI Reader")
    app.setOrganizationName("PDF AI Reader")
    app.setApplicationVersion("1.0.0")

    # 初始化 WebEngine 默认配置（避免白屏）
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)

    try:
        # 构建服务
        services = build_services()

        # 创建主窗口
        window = MainWindow(services)
        window.show()

        logging.info("主窗口已显示。")
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
