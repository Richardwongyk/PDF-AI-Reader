"""
PDF AI Reader — 程序主入口。

创建 QApplication，初始化所有服务（ServiceContainer + 懒加载），
构建 MainWindow，启动事件循环。
"""

import logging
import os
import shutil
import sys
import argparse
import json
import socket
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

# 必须在任何 chromadb 导入之前禁用 telemetry，避免 posthog 报错
os.environ["ANONYMIZED_TELEMETRY"] = "False"
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL + 1)
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

# Windows 上关闭 Chromium 沙盒
import platform
if platform.system() == "Windows":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--no-sandbox --disable-gpu-sandbox"

from PySide6.QtCore import QIODevice, QLockFile, Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtWebEngineCore import QWebEngineProfile

from src.core.models import AppConfig, TaskType
from src.core.model_providers import normalize_litellm_model
from src.core.service_container import ServiceContainer
from src.data.config_manager import ConfigManager


REQUIRED_PYTHON = (3, 14, 4)
_OLLAMA_REACHABILITY_CACHE: dict[str, tuple[float, bool]] = {}
_OLLAMA_REACHABILITY_TTL_SEC = 15.0
_OLLAMA_PROBE_TIMEOUT_SEC = 0.12
_APP_LOG_MAX_BYTES = 2 * 1024 * 1024
_APP_LOG_BACKUP_COUNT = 3
_LOG_RETENTION_SEC = 3 * 24 * 60 * 60
_RUNTIME_RETENTION_SEC = 3 * 24 * 60 * 60
_INSTANCE_LOCK_STALE_MS = 30 * 1000
_CONSOLE_LOG_ENV = "PDF_AI_READER_CONSOLE_LOG"
_SINGLE_INSTANCE_SERVER = "pdf-ai-reader-main-window"


def _is_configured_api_key(value: str | None) -> bool:
    """Return True only for a real-looking configured API key."""
    if not value:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    placeholders = ("在此填入", "your_api_key", "your-api-key", "api key")
    return not any(p in stripped.lower() for p in placeholders)


def _ollama_host_reachable(host: str) -> bool:
    """Fast TCP reachability check before constructing Ollama clients."""
    endpoint = _ollama_endpoint(host)
    if endpoint is None:
        return True
    cache_key = f"{endpoint[0]}:{endpoint[1]}"
    now = time.monotonic()
    cached = _OLLAMA_REACHABILITY_CACHE.get(cache_key)
    if cached and now - cached[0] < _OLLAMA_REACHABILITY_TTL_SEC:
        return cached[1]
    try:
        with socket.create_connection(endpoint, timeout=_OLLAMA_PROBE_TIMEOUT_SEC):
            reachable = True
    except OSError:
        reachable = False
    _OLLAMA_REACHABILITY_CACHE[cache_key] = (now, reachable)
    return reachable


def _ollama_endpoint(host: str) -> tuple[str, int] | None:
    parsed = urlparse(str(host or "http://localhost:11434"))
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    hostname = parsed.hostname or parsed.path or "localhost"
    if hostname.lower() == "localhost":
        hostname = "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 11434)
    if not hostname:
        return None
    return (hostname, int(port))


def _prune_old_logs(log_dir: Path, now: float | None = None) -> None:
    cutoff = (now if now is not None else time.time()) - _LOG_RETENTION_SEC
    for path in log_dir.glob("*.log*"):
        if path.name.startswith("keep_awake"):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _prune_old_runtime_dirs(runtime_root: Path, now: float | None = None) -> None:
    cutoff = (now if now is not None else time.time()) - _RUNTIME_RETENTION_SEC
    if not runtime_root.exists():
        return
    for path in runtime_root.glob("process-*"):
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def _process_runtime_dir(runtime_root: Path | None = None) -> Path:
    root = runtime_root or Path("data") / "runtime"
    return root / f"process-{os.getpid()}"


def _acquire_primary_instance_lock(runtime_root: Path) -> tuple[QLockFile | None, bool]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(runtime_root / "primary-instance.lock"))
    lock.setStaleLockTime(_INSTANCE_LOCK_STALE_MS)
    if lock.tryLock(0):
        return lock, False
    return None, True


def _runtime_lock_path(runtime_root: Path) -> Path:
    return runtime_root / "primary-instance.lock"


def _lock_owner_pid(lock_path: Path) -> int | None:
    try:
        first_line = lock_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        pid = int(first_line.strip())
    except (OSError, IndexError, ValueError):
        return None
    return pid if pid > 0 else None


def _process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    if pid == os.getpid():
        return True
    if platform.system() == "Windows":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:
            return False
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _send_to_primary_instance(open_pdf: str | None) -> bool:
    deadline = time.monotonic() + 8.0
    payload = json.dumps({"open_pdf": open_pdf or ""}, ensure_ascii=False).encode("utf-8")
    while True:
        socket = QLocalSocket()
        socket.connectToServer(_SINGLE_INSTANCE_SERVER, QIODevice.OpenModeFlag.WriteOnly)
        if socket.waitForConnected(500):
            socket.write(payload)
            socket.flush()
            ok = socket.waitForBytesWritten(800)
            socket.disconnectFromServer()
            if ok:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def _start_single_instance_server(window: object) -> QLocalServer | None:
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_SERVER):
        QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
        if not server.listen(_SINGLE_INSTANCE_SERVER):
            logging.warning("单实例 IPC 服务启动失败: %s", server.errorString())
            return None

    def _activate_main_window() -> None:
        window.showNormal()
        window.raise_()
        window.activateWindow()
        app = QApplication.instance()
        if app is not None:
            app.setActiveWindow(window)

    def _on_new_connection() -> None:
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            if client is None:
                continue
            if client.waitForReadyRead(800):
                raw = bytes(client.readAll()).decode("utf-8", errors="ignore")
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {}
                open_pdf = str(payload.get("open_pdf", "") or "").strip()
                if open_pdf:
                    QTimer.singleShot(
                        0,
                        lambda path=open_pdf: window._open_pdf_file(path, restore_session=False),
                    )
            client.disconnectFromServer()
            client.deleteLater()
        QTimer.singleShot(0, _activate_main_window)

    server.newConnection.connect(_on_new_connection)
    return server


def _configure_webengine_profile(profile: QWebEngineProfile, runtime_dir: Path) -> None:
    web_dir = runtime_dir / "qtwebengine"
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setCachePath(str(web_dir / "cache"))
    profile.setPersistentStoragePath(str(web_dir / "storage"))
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )


def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    _prune_old_logs(log_dir)
    log_path = log_dir / f"app-{os.getpid()}.log"
    handlers: list[logging.Handler] = [
        RotatingFileHandler(
            log_path,
            maxBytes=_APP_LOG_MAX_BYTES,
            backupCount=_APP_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    ]
    if os.getenv(_CONSOLE_LOG_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def build_services(test_mode: bool = False, secondary_instance: bool = False) -> ServiceContainer:
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
    from src.core.math_ocr import MathOCR
    ocr_backend_kwargs = {}
    if config.model.formula_ocr_model and config.model.formula_ocr_backend not in {"pix2text", "pix2text-mfr"}:
        ocr_backend_kwargs["model_name"] = config.model.formula_ocr_model
    MathOCR.set_default_backend_config(config.model.formula_ocr_backend, **ocr_backend_kwargs)
    logging.info("公式 OCR 后端: %s", config.model.formula_ocr_backend)
    if test_mode:
        config.routing.translation = "cloud_only"
        config.routing.qa = "cloud_only"
        config.routing.summarization = "cloud_only"
    if secondary_instance and config.rag.backend in {"legacy_chroma", "llamaindex_chroma"}:
        logging.info(
            "检测到已有 PDF AI Reader 主实例，当前实例将知识库后端从 %s 降级为 sqlite_fts，"
            "避免多个进程同时访问 ChromaDB 持久化目录。",
            config.rag.backend,
        )
        config.rag.backend = "sqlite_fts"

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

    def _build_graph_index_flow():
        from src.app.graph_index_flow import GraphIndexFlow
        return GraphIndexFlow(
            enabled=bool(config.rag.enable_graph_index),
            batch_budget=max(1, int(config.rag.candidate_pool)),
        )

    container.register_singleton("graph_index_flow", _build_graph_index_flow)

    # ── 4. 重量级基础设施 (LAZY SINGLETON — 延迟到首次使用) ──

    def _build_chroma_repo():
        knowledge_dir = data_dir / "knowledge_bases"
        from src.data.chroma_repo import ChromaRepo
        return ChromaRepo(str(knowledge_dir))

    container.register_singleton("chroma_repo", _build_chroma_repo)

    def _build_embed_client():
        """延迟创建嵌入模型客户端（Ollama 优先，哈希嵌入兜底）。"""
        from src.core.ai_engine import HashingEmbeddingClient, OllamaClient
        if test_mode:
            logging.info("测试模式：嵌入服务使用确定性哈希嵌入，不探测 Ollama")
            return HashingEmbeddingClient()
        if not _ollama_host_reachable(config.model.ollama_host):
            logging.info("Ollama 服务不可达，嵌入服务直接使用确定性哈希嵌入")
            return HashingEmbeddingClient()
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
            logging.info("本地嵌入模型不可用，使用确定性哈希嵌入（关键词检索兜底）")
            return HashingEmbeddingClient()

    container.register_singleton("embed_client", _build_embed_client)

    def _build_embedding_service():
        from src.core.ai_engine import BaseLLMClient
        from src.core.knowledge_engine import EmbeddingService
        return EmbeddingService(cast(BaseLLMClient, container.get("embed_client")))

    container.register_singleton("embedding_service", _build_embedding_service)

    def _build_knowledge_engine():
        from src.core.knowledge_engine import KnowledgeEngine
        from src.core.knowledge_engine import EmbeddingService
        effective_config = config.model_copy(deep=True)
        embed_client = container.get("embed_client")
        if (
            effective_config.rag.backend == "legacy_chroma"
            and type(embed_client).__name__ == "HashingEmbeddingClient"
        ):
            logging.info("使用哈希嵌入兜底，知识库后端自动切换为 sqlite_fts")
            effective_config.rag.backend = "sqlite_fts"
        chroma_repo = None
        if effective_config.rag.backend in {"legacy_chroma", "llamaindex_chroma"}:
            chroma_repo = container.get("chroma_repo")
        return KnowledgeEngine(
            cast(EmbeddingService, container.get("embedding_service")),
            chroma_repo,
            effective_config,
            sqlite_fts_dir=str(data_dir / "knowledge_bases_fts"),
        )

    container.register_singleton("knowledge_engine", _build_knowledge_engine)

    def _build_ai_engine():
        """延迟构建 AI 引擎（~3s LiteLLM 初始化推迟到首次翻译/问答时）。"""
        from src.core.ai_engine import (
            AIEngine, BaseLLMClient, HybridModelRouter,
            LiteLLMClient, MockLLMClient, OllamaClient, QAService, TranslationService,
        )

        local_client: BaseLLMClient | None = None
        local_routes = {
            config.routing.translation,
            config.routing.qa,
            config.routing.summarization,
        }
        uses_local_generation = bool(local_routes & {"local_first", "local_only"})
        if not uses_local_generation:
            logging.info("生成任务默认使用云端路由，跳过本地 Ollama 生成模型探测")
        elif test_mode:
            logging.info("测试模式：跳过本地生成模型探测，不连接 Ollama")
        elif not _ollama_host_reachable(config.model.ollama_host):
            logging.info("Ollama 服务不可达，跳过本地生成模型探测")
        else:
            try:
                candidate = OllamaClient(
                    model=config.model.local,
                    host=config.model.ollama_host,
                )
                if candidate.check_availability():
                    logging.info("本地生成模型可用: %s", config.model.local)
                    local_client = candidate
                else:
                    logging.info("本地生成模型不可用或未下载: %s", config.model.local)
            except Exception:
                logging.info("本地生成模型初始化失败，将按配置使用云端或降级客户端", exc_info=True)

        cloud_client: BaseLLMClient | None = None
        reasoning_client: BaseLLMClient | None = None
        cloud_provider_raw = config.model.cloud_translation or config.model.cloud
        reasoning_provider_raw = config.model.cloud_reasoning or cloud_provider_raw
        cloud_provider = normalize_litellm_model(cloud_provider_raw)
        reasoning_provider = normalize_litellm_model(reasoning_provider_raw)
        if cloud_provider != cloud_provider_raw:
            logging.info("云端翻译模型已规范化: %s → %s", cloud_provider_raw, cloud_provider)
        if reasoning_provider != reasoning_provider_raw:
            logging.info("云端全文理解模型已规范化: %s → %s", reasoning_provider_raw, reasoning_provider)
        cloud_api_key = (
            config_manager.get_api_key(cloud_provider)
            or config_manager.get_api_key(cloud_provider_raw)
            or config_manager.get_api_key(config.model.cloud)
        )
        reasoning_api_key = (
            config_manager.get_api_key(reasoning_provider)
            or config_manager.get_api_key(reasoning_provider_raw)
            or cloud_api_key
        )
        if test_mode:
            logging.info("测试模式：生成模型强制使用 Mock 客户端，不调用云端 API")
        elif _is_configured_api_key(cloud_api_key):
            logging.info("使用云端翻译模型: %s", cloud_provider)
            cloud_client = LiteLLMClient(model=cloud_provider, api_key=cloud_api_key or "")
        else:
            logging.info("未配置云端 API Key，真实生成将降级为模拟客户端（测试模式）")
        if not test_mode and _is_configured_api_key(reasoning_api_key):
            logging.info("使用云端全文理解模型: %s", reasoning_provider)
            reasoning_client = LiteLLMClient(
                model=reasoning_provider,
                api_key=reasoning_api_key or "",
            )

        fallback_client: BaseLLMClient = MockLLMClient()
        router = HybridModelRouter(
            local_client,
            cloud_client,
            fallback_client,
            config,
            reasoning_client=reasoning_client,
        )

        translation_service = TranslationService(
            router,
            glossary_entries=glossary_manager.get_entries(["math", "cs_ml", "physics"]),
        )
        qa_service = QAService(router)
        return AIEngine(router, translation_service, qa_service, config)

    container.register_singleton("ai_engine", _build_ai_engine)

    def _build_formula_semantic_review():
        """Build cloud formula review service; scheduling remains explicit/bounded."""
        from src.app.formula_semantic_review import FormulaSemanticReviewService
        from src.app.formula_index_store import FormulaIndexStore

        ai_engine = cast(object, container.get("ai_engine"))
        router = getattr(ai_engine, "router")
        client = router.route(TaskType.QA)
        return FormulaSemanticReviewService(
            FormulaIndexStore(),
            client,
            batch_size=2 if not test_mode else 1,
        )

    container.register_singleton("formula_semantic_review", _build_formula_semantic_review)

    # ── 5. 工厂模式 (per-document instances) ──

    def _build_document_engine():
        from src.core.pdf_engine import DocumentEngine
        from src.infra.page_cache import PageCache
        page_cache = cast(PageCache, container.get("page_cache"))
        return DocumentEngine(config, page_cache=page_cache)

    container.register_factory("document_engine", _build_document_engine)

    logging.info("所有核心服务已注册 (懒加载单例: chroma_repo, embed_client, knowledge_engine, ai_engine)。"
                 "已注册: %s", container.registered_services)

    elapsed = __import__('time').perf_counter() - t_start
    logging.info("build_services: 完成 (%.2fs, %d 个服务注册)", elapsed, len(container.registered_services))
    return container


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PDF AI Reader")
    parser.add_argument("--open", dest="open_pdf", help="启动后自动打开指定 PDF。")
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="跳过首次启动弹窗和云端预热，供闭环 UI 自动化测试使用。",
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args(sys.argv[1:])
    setup_logging()

    if sys.version_info[:3] != REQUIRED_PYTHON:
        required = ".".join(map(str, REQUIRED_PYTHON))
        current = ".".join(map(str, sys.version_info[:3]))
        message = (
            f"本项目要求 Python {required}，当前解释器为 Python {current}。\n"
            "请使用 run_py314.bat 或 conda 环境 pdf_ai_reader_314 启动。"
        )
        logging.critical("Python 版本不匹配: %s", message)
        sys.stderr.write(message + "\n")
        return 1

    # 全局异常处理（四级严重度）
    from src.core.error_handler import ErrorHandler
    _error_handler = ErrorHandler()
    _error_handler.setup_global_exception_hook()

    logging.info("PDF AI Reader 启动中...")
    t_start = __import__('time').perf_counter()

    app = QApplication(sys.argv)
    app.setApplicationName("PDF AI Reader")
    app.setApplicationVersion("1.0.0")
    app.setQuitOnLastWindowClosed(True)
    app.lastWindowClosed.connect(lambda: logging.info("QApplication lastWindowClosed"))
    app.aboutToQuit.connect(lambda: logging.info("QApplication aboutToQuit"))
    logging.info("QApplication 创建完成 (%.2fs)", __import__('time').perf_counter() - t_start)

    runtime_root = Path("data") / "runtime"
    _prune_old_runtime_dirs(runtime_root)
    runtime_dir = _process_runtime_dir(runtime_root)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    instance_lock, secondary_instance = _acquire_primary_instance_lock(runtime_root)
    app._pdf_ai_reader_instance_lock = instance_lock
    app._pdf_ai_reader_runtime_dir = runtime_dir
    logging.info(
        "多进程实例角色: %s, runtime_dir=%s",
        "secondary" if secondary_instance else "primary",
        runtime_dir,
    )
    if secondary_instance:
        open_pdf = str(Path(args.open_pdf).resolve()) if args.open_pdf else ""
        if _send_to_primary_instance(open_pdf):
            logging.info("已把启动请求转交给现有主窗口，当前 secondary 实例退出")
            return 0
        lock_path = _runtime_lock_path(runtime_root)
        owner_pid = _lock_owner_pid(lock_path)
        if _process_exists(owner_pid):
            logging.warning(
                "检测到已有 PDF AI Reader 进程 pid=%s，但暂时无法联系主窗口；"
                "为保持单窗口标签模式，当前 secondary 实例退出。",
                owner_pid,
            )
            return 0
        logging.warning(
            "检测到过期实例锁且 owner pid=%s 不存在，清理锁后恢复启动主窗口。",
            owner_pid,
        )
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            logging.warning("过期实例锁清理失败: %s", lock_path, exc_info=True)
            return 1
        QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
        instance_lock, secondary_instance = _acquire_primary_instance_lock(runtime_root)
        app._pdf_ai_reader_instance_lock = instance_lock
        if secondary_instance:
            logging.warning("清理过期锁后仍无法取得主实例锁，当前 secondary 实例退出")
            return 0

    profile = QWebEngineProfile.defaultProfile()
    _configure_webengine_profile(profile, runtime_dir)

    try:
        t_build = __import__('time').perf_counter()
        services = build_services(
            test_mode=args.test_mode,
            secondary_instance=secondary_instance,
        )

        # 将全局异常处理的父窗口设为主窗口（用于错误对话框）
        from src.ui.main_window import MainWindow
        t_ui = __import__('time').perf_counter()
        window = MainWindow(services)
        app._main_window = window  # keep an explicit reference for the Qt object lifetime
        if args.test_mode:
            window._check_first_launch = lambda: None
            window._prewarm_webview_pool = lambda: None
        logging.info("MainWindow 创建完成 (%.2fs)", __import__('time').perf_counter() - t_ui)
        _error_handler.set_parent_widget(window)

        window.show()
        app._pdf_ai_reader_single_instance_server = _start_single_instance_server(window)
        def _activate_main_window() -> None:
            window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            window.showNormal()
            window.raise_()
            window.activateWindow()
            app.setActiveWindow(window)
            try:
                logging.info("MainWindow activated: winId=0x%X visible=%s", int(window.winId()), window.isVisible())
            except Exception:
                logging.info("MainWindow activated")

        QTimer.singleShot(250, _activate_main_window)
        QTimer.singleShot(1500, _activate_main_window)
        QTimer.singleShot(
            2700,
            lambda: (
                window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False),
                window.showNormal(),
            ),
        )
        if args.test_mode:
            from src.app.test_command_bridge import TestCommandBridge
            command_file = Path("test_artifacts/e2e/commands.jsonl")
            event_file = Path("test_artifacts/e2e/events.jsonl")
            if command_file.exists():
                command_file.unlink()
            if event_file.exists():
                event_file.unlink()
            window._test_command_bridge = TestCommandBridge(window, command_file, event_file)
        if args.open_pdf:
            pdf_path = Path(args.open_pdf).resolve()
            if not pdf_path.exists():
                raise FileNotFoundError(f"PDF not found: {pdf_path}")
            QTimer.singleShot(
                0,
                lambda path=str(pdf_path): window._open_pdf_file(path, restore_session=False),
            )
        elif not args.test_mode:
            QTimer.singleShot(0, window.restore_last_reading_session)
        total = __import__('time').perf_counter() - t_start
        logging.info("主窗口已显示。总启动时间: %.2fs", total)

        # 后台预热云端 LLM 连接（首次调用 ~47s，预热后降至 ~2s）
        if not args.test_mode:
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
    finally:
        services = locals().get("services")
        if services is not None:
            services.shutdown()
        instance_lock = locals().get("instance_lock")
        if instance_lock is not None:
            try:
                instance_lock.unlock()
            except Exception:
                logging.debug("实例锁释放失败", exc_info=True)


if __name__ == "__main__":
    sys.exit(main())
