"""
AI 引擎 —— LLM 客户端抽象、混合模型路由、翻译与问答服务。

BaseLLMClient: 抽象基类
OllamaClient: 本地 Ollama 模型客户端
LiteLLMClient: 云端模型统一客户端（通过 LiteLLM）
HybridModelRouter: 混合模型路由器
TranslationService: 专业论文翻译服务
QAService: 基于知识库的文档问答服务
AIEngine: AI 引擎顶层协调器（信号驱动）
"""

import re
import time
from abc import ABC, abstractmethod
from collections.abc import Generator
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from src.core.base_service import BaseService
from src.core.models import (
    AppConfig,
    DocumentBlock,
    GlossaryEntry,
    TaskType,
)


# =============================================================================
# LLM 客户端抽象
# =============================================================================

class BaseLLMClient(ABC):
    """LLM 客户端的抽象基类。

    所有 LLM 调用（本地 Ollama / 云端 API）统一通过此接口，
    使上层业务逻辑与具体模型实现解耦。
    """

    @abstractmethod
    def generate(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> str:
        """同步生成回复（阻塞调用，仅用于短文本/简单任务）。

        Args:
            messages: 消息列表，格式 [{"role": "system", "content": "..."},
                      {"role": "user", "content": "..."}]
            **kwargs: 传递给模型的额外参数（temperature, max_tokens 等）。

        Returns:
            模型生成的完整文本。
        """
        ...

    @abstractmethod
    def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> Generator[str, None, None]:
        """流式生成回复（用于实时显示场景）。

        Args:
            messages: 同上。
            **kwargs: 同上。

        Yields:
            每次产出一个 token 字符串。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前使用的模型名称。"""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量生成文本嵌入向量。

        Args:
            texts: 文本列表。

        Returns:
            与输入顺序对应的向量列表。
        """
        ...

    @abstractmethod
    def check_availability(self) -> bool:
        """检查模型是否可用。

        Returns:
            True 表示可正常调用。
        """
        ...


class OllamaClient(BaseLLMClient):
    """Ollama 本地模型客户端。

    通过 Ollama REST API (默认 http://localhost:11434) 调用本地模型。
    支持所有 Ollama 管理的模型（Qwen3.5:4b、BGE-M3 等）。
    """

    def __init__(
        self, model: str = "qwen3.5:4b", host: str = "http://localhost:11434"
    ) -> None:
        """初始化 Ollama 客户端。

        Args:
            model: Ollama 模型标签（如 "qwen3.5:4b"）。
            host: Ollama 服务地址。
        """
        self._model = model
        self._host = host

        # 延迟导入，避免 ollama 不可用时影响整个模块加载
        import ollama
        self._client = ollama.Client(host=host)

    def generate(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> str:
        """同步生成（非流式 API）。

        Args:
            messages: 消息列表。
            **kwargs: 可包含 temperature (float), max_tokens (int) 等。

        Returns:
            生成的完整文本。
        """
        response = self._client.chat(
            model=self._model,
            messages=messages,
            stream=False,
            options={
                "temperature": kwargs.get("temperature", 0.1),
                "num_predict": kwargs.get("max_tokens", 2048),
            },
        )
        return response.get("message", {}).get("content", "")

    def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> Generator[str, None, None]:
        """流式生成。

        Args:
            messages: 消息列表。
            **kwargs: 同上。

        Yields:
            每次产出一个 token 字符串。
        """
        stream = self._client.chat(
            model=self._model,
            messages=messages,
            stream=True,
            options={
                "temperature": kwargs.get("temperature", 0.1),
                "num_predict": kwargs.get("max_tokens", 4096),
            },
        )
        for chunk in stream:
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    @property
    def model_name(self) -> str:
        return self._model

    def check_availability(self) -> bool:
        """检查 Ollama 服务和指定模型是否可用。"""
        try:
            models = self._client.list()
            names: list[str] = []
            model_items = models.get("models", []) if hasattr(models, "get") else getattr(models, "models", [])
            for item in model_items:
                if hasattr(item, "get"):
                    name = item.get("name") or item.get("model") or ""
                else:
                    name = getattr(item, "name", "") or getattr(item, "model", "")
                if name:
                    names.append(str(name))
            return any(
                n == self._model or n.startswith(f"{self._model}:")
                for n in names
            )
        except Exception:
            return False

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """使用 Ollama 嵌入模型生成向量。"""
        vectors: list[list[float]] = []
        for text in texts:
            response = self._client.embeddings(model=self._model, prompt=text)
            vectors.append(response.get("embedding", []))
        return vectors


class LiteLLMClient(BaseLLMClient):
    """云端模型统一客户端。

    通过 LiteLLM 库调用 OpenAI、DeepSeek、Qwen、GLM、Kimi 等云端大模型。
    使用同一套 OpenAI-compatible 消息格式。
    """

    def __init__(
        self, model: str, api_key: str, api_base: str | None = None
    ) -> None:
        """初始化云端客户端。

        Args:
            model: LiteLLM 模型标识符（如 "deepseek-chat"、"gpt-4o"）。
            api_key: API 密钥。
            api_base: 可选的自定义 API 端点。
        """
        self._model = model
        self._api_key = api_key
        self._api_base = api_base

    def generate(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> str:
        """同步生成——连接失败重试2次。"""
        import litellm, time
        last_error = None
        for attempt in range(3):
            try:
                response = litellm.completion(
                    model=self._model, messages=messages,
                    api_key=self._api_key, api_base=self._api_base,
                    stream=False,
                    timeout=kwargs.pop("timeout", 120),
                    temperature=kwargs.get("temperature", 0.1),
                    max_tokens=kwargs.get("max_tokens", 4096),
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_error = e
                if attempt < 2:
                    time.sleep(1.5 ** attempt)
        raise last_error  # type: ignore[misc]

    def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> Generator[str, None, None]:
        """流式生成——连接失败自动重试2次。"""
        import litellm
        last_error = None
        for attempt in range(3):
            try:
                response = litellm.completion(
                    model=self._model, messages=messages,
                    api_key=self._api_key, api_base=self._api_base,
                    stream=True,
                    timeout=kwargs.pop("timeout", 120),
                    temperature=kwargs.get("temperature", 0.1),
                    max_tokens=kwargs.get("max_tokens", 4096),
                )
                for chunk in response:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
                return
            except Exception as e:
                last_error = e
                if attempt < 2:
                    import time
                    time.sleep(1.5 ** attempt)  # 1s, 1.5s, 2.25s
        raise last_error  # type: ignore[misc]

    @property
    def model_name(self) -> str:
        return self._model

    def check_availability(self) -> bool:
        """检查 API Key 是否已配置（不发送真实请求）。"""
        return bool(self._api_key)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """使用云端嵌入模型生成向量。"""
        import litellm
        try:
            response = litellm.embedding(
                model=self._model,
                input=texts,
                api_key=self._api_key,
                api_base=self._api_base,
                timeout=60,
            )
            return [d["embedding"] for d in response.data]
        except Exception:
            import logging
            logging.getLogger("LiteLLMClient").warning(
                "云端嵌入失败，模型 %s 可能不支持嵌入", self._model
            )
            raise


# =============================================================================
# HybridModelRouter —— 混合模型路由器
# =============================================================================

class HybridModelRouter:
    """混合模型路由器。

    根据用户配置的路由策略和当前模型可用性，
    为每个任务选择最优的 LLM 客户端。
    """

    def __init__(
        self,
        local_client: BaseLLMClient | None,
        cloud_client: BaseLLMClient | None,
        fallback_client: BaseLLMClient | None,
        config: AppConfig,
        reasoning_client: BaseLLMClient | None = None,
    ) -> None:
        """初始化路由器。

        Args:
            local_client: 本地模型客户端（OllamaClient），未配置时为 None。
            cloud_client: 云端模型客户端（LiteLLMClient），未配置时为 None。
            fallback_client: 最终降级客户端（MockLLMClient），仅用于无真实模型时保底。
            config: 应用配置，包含路由规则。
        """
        self._local = local_client
        self._cloud = cloud_client
        self._reasoning = reasoning_client
        self._fallback = fallback_client
        self._config = config

    @staticmethod
    def _available(client: BaseLLMClient | None) -> bool:
        if client is None:
            return False
        try:
            return client.check_availability()
        except Exception:
            return False

    def route(self, task: TaskType) -> BaseLLMClient:
        """根据任务类型决定使用哪个客户端。

        决策逻辑：
        1. EMBEDDING 任务始终返回本地客户端。
        2. 查看 config.routing 中该任务的策略。
        3. LOCAL_FIRST: 本地可用则返回本地，否则云端。
        4. CLOUD_ONLY: 返回云端。
        5. LOCAL_ONLY: 返回本地。

        Args:
            task: 任务类型。

        Returns:
            选定的 LLM 客户端。

        Raises:
            RuntimeError: 没有可用客户端时。
        """
        # 嵌入任务：本地优先，云端回退。真正的本地嵌入兜底在 EmbeddingService 中处理。
        if task == TaskType.EMBEDDING:
            if self._available(self._local):
                return self._local
            if self._available(self._cloud):
                return self._cloud
            raise RuntimeError("无可用的嵌入模型。请启动 Ollama 或配置云端 API Key。")

        # 获取该任务的策略
        strategy_map: dict[TaskType, str] = {
            TaskType.TRANSLATION: self._config.routing.translation,
            TaskType.QA: self._config.routing.qa,
            TaskType.SUMMARIZATION: self._config.routing.summarization,
            TaskType.FOLLOWUP_QUESTIONS: self._config.routing.qa,
        }
        strategy = strategy_map.get(task, "local_first")
        reasoning_tasks = {TaskType.QA, TaskType.SUMMARIZATION}
        preferred_cloud = self._reasoning if task in reasoning_tasks else self._cloud
        secondary_cloud = self._cloud if preferred_cloud is self._reasoning else self._reasoning

        if strategy == "local_only":
            if self._available(self._local):
                return self._local
            import logging
            logging.getLogger("HybridModelRouter").error(
                "本地模型不可用 (策略=local_only): task=%s", task.value
            )
            raise RuntimeError("本地模型不可用。请启动 Ollama 服务后再试。")

        if strategy == "cloud_only":
            if self._available(preferred_cloud):
                return preferred_cloud
            if self._available(secondary_cloud):
                return secondary_cloud  # type: ignore[return-value]
            import logging
            if self._available(self._fallback):
                logging.getLogger("HybridModelRouter").info(
                    "云端模型不可用，使用降级客户端: task=%s", task.value
                )
                return self._fallback
            logging.getLogger("HybridModelRouter").error(
                "云端模型不可用 (策略=cloud_only): task=%s", task.value
            )
            raise RuntimeError("云端模型不可用。请检查 API Key 配置和网络连接。")

        # local_first（默认）：优先本地，回退云端
        if self._available(self._local):
            return self._local
        if self._available(preferred_cloud):
            import logging
            logging.getLogger("HybridModelRouter").info(
                "本地模型不可用，回退到云端: task=%s", task.value
            )
            return preferred_cloud
        if self._available(secondary_cloud):
            return secondary_cloud  # type: ignore[return-value]
        if self._available(self._fallback):
            return self._fallback
        import logging
        logging.getLogger("HybridModelRouter").error(
            "本地和云端模型均不可用: task=%s", task.value
        )
        raise RuntimeError("本地和云端模型均不可用。")

    @property
    def local_available(self) -> bool:
        """本地模型是否可用。"""
        return self._available(self._local)

    @property
    def cloud_available(self) -> bool:
        """云端模型是否可用。"""
        return self._available(self._cloud)


# =============================================================================
# TranslationService —— 翻译服务
# =============================================================================

class TranslationService:
    """专业论文翻译服务。

    翻译流水线：
    1. 文本预处理（公式占位符保护）
    2. 术语表加载与 Prompt 注入
    3. 风格指令注入
    4. 调用 LLM 进行翻译
    5. 后处理（公式恢复）
    """

    # 默认翻译系统指令模板
    DEFAULT_SYSTEM_PROMPT: str = (
        "你是一位精通{domain}的科技翻译专家。\n"
        "## 任务\n"
        "将以下英文技术文本翻译成中文。\n"
        "## 强制要求\n"
        "1. 保留所有 LaTeX 数学公式不变（包括所有【FORMULA_x】占位符，"
        "它们代表公式，必须原样保留在译文中）。\n"
        "2. 化被动语态为主动语态，将英文长句拆分为符合中文习惯的短句。\n"
        "3. 删减冗余修饰语和过多形容词，确保译文简洁准确。\n"
        "4. 严格使用以下术语表进行翻译（英文→中文）：\n"
        "{glossary_terms}\n"
        "5. 对定理(Theorem)、引理(Lemma)、证明(Proof)等结构，保持逻辑严谨，\n"
        "   条件与结论的精确对应，不加词不减词，不改变逻辑连接词。\n"
        "6. 译文应自然流畅，符合中文学术写作习惯，杜绝翻译腔。\n"
        "7. 【重要】输出中的所有数学公式必须使用 LaTeX 格式：行内公式用 \\(...\\) 包裹，行间公式用 \\[...\\] 包裹。"
    )

    # Few-shot 示例
    FEWSHOT_EXAMPLES: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                "The attention mechanism, which was first proposed by Vaswani et al., "
                "has been widely adopted in various natural language processing tasks."
            ),
        },
        {
            "role": "assistant",
            "content": "Vaswani等人首次提出了注意力机制，此后该机制被广泛用于各类自然语言处理任务。",
        },
    ]

    def __init__(
        self,
        router: HybridModelRouter,
        glossary_entries: list[GlossaryEntry] | None = None,
    ) -> None:
        """初始化翻译服务。

        Args:
            router: 模型路由器。
            glossary_entries: 初始术语条目列表（可选，后续可动态更新）。
        """
        self._router = router
        self._glossary_entries: list[GlossaryEntry] = glossary_entries or []

        # 延迟导入预处理器（避免循环依赖）
        from src.core.pdf_engine import TextPreprocessor
        self._preprocessor = TextPreprocessor()

    def update_glossary(self, entries: list[GlossaryEntry]) -> None:
        """更新当前使用的术语表。

        Args:
            entries: 新的术语条目列表。
        """
        self._glossary_entries = entries

    def translate_block(
        self,
        block: DocumentBlock,
        domain: str = "math",
        stream: bool = True,
    ) -> Generator[str, None, None] | str:
        """翻译单个文档块。

        完整流水线：
        1. 公式保护（占位符替换）
        2. 术语注入
        3. Prompt 构建
        4. 模型调用

        Args:
            block: 待翻译的文档块。
            domain: 学科领域。
            stream: True 返回生成器，False 返回完整字符串。

        Returns:
            流式模式返回 token 生成器；同步模式返回完整译文。
        """
        # 公式保护
        protected_text = self._preprocessor.protect_formulas(block.content)

        # 构建消息
        messages = self._build_messages(protected_text, domain, block.block_type.value)

        # 路由选择
        client = self._router.route(TaskType.TRANSLATION)

        max_tokens = self._estimate_translation_tokens(protected_text)

        if stream:
            return client.generate_stream(
                messages, temperature=0.1, max_tokens=max_tokens, timeout=60
            )
        else:
            raw = client.generate(
                messages, temperature=0.1, max_tokens=max_tokens, timeout=60
            )
            return self._post_process(raw)

    def translate_sentences(
        self, sentences: list[str], domain: str = "math"
    ) -> list[str]:
        """逐句翻译（用于逐句对照模式）。

        Args:
            sentences: 英文句子列表。
            domain: 学科领域。

        Returns:
            与输入顺序对应的中文句子列表。
        """
        results: list[str] = []
        client = self._router.route(TaskType.TRANSLATION)

        for sentence in sentences:
            if not sentence.strip():
                results.append("")
                continue
            protected = self._preprocessor.protect_formulas(sentence)
            messages = self._build_messages(protected, domain, "paragraph")
            try:
                translated = client.generate(messages, temperature=0.1, max_tokens=1024)
                results.append(self._post_process(translated))
            except Exception:
                results.append("[翻译失败]")

        return results

    def translate_word(self, word: str) -> str:
        """取词翻译（用于悬浮气泡）。

        Args:
            word: 英文单词或短语。

        Returns:
            中文翻译。
        """
        client = self._router.route(TaskType.TRANSLATION)
        messages = [
            {
                "role": "system",
                "content": "你是一个英中词典。请将以下英文单词/短语翻译为中文，并给出简洁解释。",
            },
            {"role": "user", "content": word},
        ]
        try:
            return client.generate(messages, temperature=0.0, max_tokens=100)
        except Exception:
            return "[翻译失败]"

    def _build_messages(
        self, text: str, domain: str, block_type: str
    ) -> list[dict[str, str]]:
        """构建完整的消息列表（System + Few-shot + User）。

        Args:
            text: 待翻译文本（公式已保护）。
            domain: 学科领域。
            block_type: 块的类型。

        Returns:
            可直接发送的消息列表。
        """
        # 术语表格式化
        glossary_str = self._format_glossary()

        # 系统指令
        system_content = self.DEFAULT_SYSTEM_PROMPT.format(
            domain=domain,
            glossary_terms=glossary_str if glossary_str else "（无特殊术语要求）",
        )

        # 公式块强化指令：LaTeX 原样保留，只翻译周围文字
        if block_type == "formula":
            system_content += (
                "\n\n【重要】当前段落已通过视觉模型识别为数学公式。"
                "公式内容使用 LaTeX 格式（【FORMULA_x】占位符）标记。"
                "严格保留所有公式占位符及其 LaTeX 代码，不翻译、不修改、不增删任何数学符号。"
                "如果公式旁边有英文文字说明，仅翻译文字部分，公式本身保持原样。"
            )

        # 定理/证明环境额外指令
        if block_type in ("heading",) and any(
            kw in text.lower() for kw in ("theorem", "lemma", "proof", "definition")
        ):
            system_content += (
                "\n\n你正在翻译数学定理或证明，"
                "必须保持条件与结论的精确对应，"
                "严禁增删或改变逻辑连接词。"
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]

        # Few-shot 示例
        messages.extend(self.FEWSHOT_EXAMPLES)

        # 用户文本
        messages.append({"role": "user", "content": text})

        return messages

    @staticmethod
    def _estimate_translation_tokens(text: str) -> int:
        """给翻译任务设置紧凑输出上限，减少云端首 token 和尾部等待时间。"""
        return min(2048, max(192, int(len(text) * 1.8) + 96))

    def _format_glossary(self) -> str:
        """将术语表格式化为 Prompt 可注入的字符串。

        Returns:
            格式: "- manifold -> 流形\n- gradient -> 梯度"
        """
        if not self._glossary_entries:
            return ""
        lines: list[str] = []
        for entry in self._glossary_entries:
            marker = " [强制]" if entry.force else ""
            lines.append(f"- {entry.en} -> {entry.zh}{marker}")
        return "\n".join(lines)

    def _post_process(self, translated: str) -> str:
        """翻译后处理：公式恢复。

        Args:
            translated: 模型返回的译文（含占位符）。

        Returns:
            恢复公式后的最终译文。
        """
        return self._preprocessor.restore_formulas(translated)


# =============================================================================
# QAService —— 问答服务
# =============================================================================

class QAService:
    """文档问答服务。

    结合当前段落上下文和知识库检索结果，
    为用户问题提供基于文档内容的精准回答。
    """

    QA_SYSTEM_PROMPT: str = (
        "你是一名严谨的研究助手，专门帮助读者理解学术论文。\n"
        "## 规则\n"
        "1. 严格依据以下提供的文档片段回答问题。\n"
        '2. 如果答案不在提供的片段中，明确告知用户"根据当前文档内容无法确定"，绝不编造。\n'
        "3. 回答中使用 LaTeX 格式展示数学公式：行内用 \\(...\\)，行间用 \\[...\\]。\n"
        "4. 若涉及推导，逐步展示中间步骤。\n"
        "5. 引用具体的章节、定理编号或页码。"
    )

    def __init__(self, router: HybridModelRouter) -> None:
        """初始化问答服务。

        Args:
            router: 模型路由器。
        """
        self._router = router

    def answer(
        self,
        question: str,
        current_block: DocumentBlock | None = None,
        retrieved_blocks: list[DocumentBlock] | None = None,
        chat_history: list[dict[str, str]] | None = None,
        stream: bool = True,
    ) -> Generator[str, None, None] | str:
        """回答用户问题。

        Args:
            question: 用户问题。
            current_block: 当前选中的段落块（作为首要上下文）。
            retrieved_blocks: 从知识库检索到的相关块列表。
            chat_history: 多轮对话历史。
            stream: True 返回流式生成器。

        Returns:
            流式模式返回 token 生成器，同步模式返回完整回答。
        """
        messages = self._build_qa_messages(
            question, current_block, retrieved_blocks or [], chat_history
        )

        client = self._router.route(TaskType.QA)

        if stream:
            return client.generate_stream(messages, temperature=0.3, max_tokens=8192)
        else:
            return client.generate(messages, temperature=0.3, max_tokens=8192)

    def generate_followup_questions(self, question: str, answer: str) -> list[str]:
        """基于问答对话生成 3 个追问建议。

        Args:
            question: 用户上一个问题。
            answer: AI 的回答。

        Returns:
            包含 3 个追问问题的字符串列表。
        """
        client = self._router.route(TaskType.FOLLOWUP_QUESTIONS)
        messages = [
            {
                "role": "system",
                "content": (
                    "基于以下问答对话，生成3个可能的追问问题。"
                    "以 JSON 数组格式返回，不要包含其他内容。"
                    '格式: ["问题1", "问题2", "问题3"]'
                ),
            },
            {"role": "user", "content": f"问题: {question}\n回答: {answer}"},
        ]
        try:
            raw = client.generate(messages, temperature=0.7, max_tokens=512)
            return self._parse_followup_questions(raw)
        except Exception:
            pass
        return []

    @staticmethod
    def _parse_followup_questions(raw: str) -> list[str]:
        """Parse strict JSON first, then tolerate numbered model output."""
        import json
        import re

        text = (raw or "").strip()
        if not text:
            return []

        def clean(items: list[Any]) -> list[str]:
            questions: list[str] = []
            for item in items:
                if not isinstance(item, str):
                    continue
                value = re.sub(r"\s+", " ", item).strip()
                value = re.sub(r"^[-*•\d.、)）\s]+", "", value).strip("\"' ")
                if value and value not in questions:
                    questions.append(value[:160])
                if len(questions) >= 3:
                    break
            return questions

        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start:end])
                if isinstance(parsed, list):
                    questions = clean(parsed)
                    if questions:
                        return questions
            except json.JSONDecodeError:
                pass

        lines = [line for line in text.splitlines() if line.strip()]
        return clean(lines)

    def _build_qa_messages(
        self,
        question: str,
        current_block: DocumentBlock | None,
        retrieved_blocks: list[DocumentBlock],
        chat_history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        """组装 QA 的完整消息列表。

        Args:
            question: 用户问题。
            current_block: 当前段落块。
            retrieved_blocks: 检索到的相关块。
            chat_history: 多轮对话历史。

        Returns:
            消息列表。
        """
        # 系统指令
        messages: list[dict[str, str]] = [
            {"role": "system", "content": self.QA_SYSTEM_PROMPT},
        ]

        # 对话历史（最多保留最近 6 轮）
        if chat_history:
            messages.extend(chat_history[-12:])  # 6 轮 = 12 条消息

        # 构建上下文
        context_parts: list[str] = []

        if current_block:
            context_parts.append(f"[当前段落 — 第{current_block.page_num + 1}页]\n{current_block.content}")

        for i, rb in enumerate(retrieved_blocks, 1):
            context_parts.append(f"[相关片段{i} — 第{rb.page_num + 1}页]\n{rb.content}")

        if context_parts:
            context_text = "\n\n---\n\n".join(context_parts)
        else:
            context_text = (
                "（当前没有可引用的文档片段。请直接回答："
                "知识库未就绪或未检索到相关片段，无法基于本文档给出可靠答案。）"
            )

        messages.append({
            "role": "user",
            "content": f"## 参考资料\n{context_text}\n\n## 用户问题\n{question}",
        })

        return messages


# =============================================================================
# AIEngine —— AI 引擎顶层协调器
# =============================================================================

class AIEngine(BaseService):
    """AI 引擎顶层协调器。

    作为 UI 层与 AI 子服务之间的唯一桥梁。
    负责线程调度、流式响应的信号发射、错误处理。
    """

    # === 翻译信号 ===
    translation_token = Signal(str, str)     # (token, block_id)
    translation_finished = Signal(str, str)  # (完整译文, block_id)
    translation_error = Signal(str, str)     # (错误信息, block_id)

    # === 问答信号 ===
    answer_token = Signal(str, str)          # (token, split_id)
    answer_finished = Signal(str, str)       # (完整回答, split_id)
    answer_error = Signal(str, str)          # (错误信息, split_id)
    followup_ready = Signal(list, str)       # (追问建议, split_id)

    def __init__(
        self,
        router: HybridModelRouter,
        translation_service: TranslationService,
        qa_service: QAService,
        config: AppConfig,
        parent: QObject | None = None,
    ) -> None:
        """初始化 AI 引擎。

        Args:
            router: 模型路由器。
            translation_service: 翻译服务。
            qa_service: 问答服务。
            config: 应用配置。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._router = router
        self._translation = translation_service
        self._qa = qa_service
        self._config = config
        self._active_threads: list[QThread] = []  # 防止 GC 回收导致信号断开

    @property
    def router(self) -> HybridModelRouter:
        """获取模型路由器。"""
        return self._router

    @property
    def translation_service(self) -> TranslationService:
        """获取翻译服务。"""
        return self._translation

    @property
    def qa_service(self) -> QAService:
        """获取问答服务。"""
        return self._qa

    def request_translation(self, block: DocumentBlock, domain: str = "math") -> None:
        """请求翻译 — 使用 QThread 信号槽，无轮询。"""
        self.logger.info("开始翻译: block=%s len=%d", block.id, len(block.content))

        thread = _TranslationThread(self._translation, block, domain)
        thread.token_generated.connect(
            lambda t, bid=block.id: self.translation_token.emit(t, bid)
        )
        thread.finished_signal.connect(
            lambda t, bid=block.id: self.translation_finished.emit(t, bid)
        )
        thread.error_signal.connect(
            lambda e, bid=block.id: self.translation_error.emit(e, bid)
        )
        thread.finished.connect(
            lambda t=thread: self._active_threads.remove(t) if t in self._active_threads else None
        )
        self._active_threads.append(thread)
        thread.start()

    def request_answer(
        self,
        question: str,
        current_block: DocumentBlock | None = None,
        retrieved_blocks: list[DocumentBlock] | None = None,
        chat_history: list[dict[str, str]] | None = None,
        split_id: str = "",
    ) -> None:
        """请求问答 — 使用 QThread 信号槽，无轮询。"""
        thread = _QAThread(
            self._qa, question, current_block,
            retrieved_blocks or [], chat_history,
        )
        thread.token_generated.connect(
            lambda t, sid=split_id: self.answer_token.emit(t, sid)
        )
        thread.finished_signal.connect(
            lambda t, sid=split_id: self.answer_finished.emit(t, sid)
        )
        thread.followup_signal.connect(
            lambda q, sid=split_id: self.followup_ready.emit(q, sid)
        )
        thread.error_signal.connect(
            lambda e, sid=split_id: self.answer_error.emit(e, sid)
        )
        thread.finished.connect(
            lambda t=thread: self._active_threads.remove(t) if t in self._active_threads else None
        )
        self._active_threads.append(thread)
        thread.start()

    def warmup_cloud(self) -> None:
        """后台预热云端连接（LiteLLM 首次调用需 ~47s 建连+SSL+模型预热）。

        发送一个极小的翻译请求到云端，在后台线程中静默执行。
        错误被忽略——预热失败不影响正常使用。
        """
        try:
            client = self._router.route(TaskType.TRANSLATION)
        except RuntimeError:
            return  # 没有可用云端客户端

        class _WarmupThread(QThread):
            def run(self_):
                try:
                    client.generate([
                        {"role": "user", "content": "hi"}
                    ], temperature=0.0, max_tokens=1)
                except Exception:
                    pass  # 预热失败不影响正常使用

        self.logger.info("开始后台预热云端连接...")
        thread = _WarmupThread()
        thread.finished.connect(
            lambda: self.logger.info("云端连接预热完成")
        )
        self._active_threads.append(thread)
        thread.start()

    def check_local_model_status(self) -> dict[str, bool]:
        """检查本地模型状态。

        Returns:
            包含 ollama_available, qwen_available, bge_available 的字典。
        """
        import ollama as ollama_lib

        status = {"ollama_available": False, "qwen_available": False, "bge_available": False}

        try:
            client = ollama_lib.Client(host=self._config.model.ollama_host)
            models = client.list()
            model_names: list[str] = []
            for m in models.get("models", []):
                name = m.get("name") or m.get("model", "")
                model_names.append(name)

            status["ollama_available"] = True
            status["qwen_available"] = any(
                "qwen" in n.lower() for n in model_names
            )
            status["bge_available"] = any(
                "bge" in n.lower() for n in model_names
            )
        except Exception:
            pass

        return status


# =============================================================================
# HashingEmbeddingClient —— 无模型时的确定性轻量嵌入
# =============================================================================

class HashingEmbeddingClient(BaseLLMClient):
    """确定性词袋哈希嵌入。

    这是没有 Ollama/BGE 或云端 embedding 时的最低可用兜底。它不是语义模型，
    但能保证相同文本生成相同向量，并让关键词、术语和相近词形具有可检索性。
    """

    def __init__(self, dimensions: int = 1024) -> None:
        self._dimensions = dimensions

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise RuntimeError("HashingEmbeddingClient 仅支持嵌入，不支持文本生成。")

    def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> Generator[str, None, None]:
        raise RuntimeError("HashingEmbeddingClient 仅支持嵌入，不支持文本生成。")

    @property
    def model_name(self) -> str:
        return "hashing-embedding"

    def check_availability(self) -> bool:
        return True

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        import hashlib
        import math

        vec = [0.0] * self._dimensions
        tokens = self._tokens(text)
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "little", signed=False)
            index = value % self._dimensions
            sign = 1.0 if (value >> 63) == 0 else -1.0
            weight = 1.0 + min(len(token), 12) / 12.0
            vec[index] += sign * weight

        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    @staticmethod
    def _tokens(text: str) -> list[str]:
        lowered = text.lower()
        base = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
        tokens: list[str] = []
        for token in base:
            tokens.append(token)
            if len(token) >= 6 and re.match(r"^[a-z0-9_]+$", token):
                for n in (3, 4):
                    tokens.extend(token[i:i + n] for i in range(0, len(token) - n + 1))
        for i in range(len(base) - 1):
            tokens.append(base[i] + "_" + base[i + 1])
        return tokens


# =============================================================================
# MockLLMClient —— 测试用模拟客户端（不调用任何 API）
# =============================================================================

class MockLLMClient(BaseLLMClient):
    """模拟 LLM 客户端 —— 用于测试 UI 交互流程。

    不依赖任何外部 API，生成预先构造的模拟回复。
    当用户未配置云端 API Key 时自动使用此客户端。
    """

    def __init__(self) -> None:
        self._model = "mock"

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """模拟生成回复。"""
        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"]
                break
        return self._simulate_response(user_text, messages)

    def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> Generator[str, None, None]:
        """模拟流式生成 —— 逐词产出带短延迟。"""
        full = self.generate(messages, **kwargs)
        # 按词拆分，模拟打字效果
        words = full.split()
        for i, word in enumerate(words):
            suffix = " " if i < len(words) - 1 else ""
            yield word + suffix
            time.sleep(0.03)  # 30ms per word

    @property
    def model_name(self) -> str:
        return "mock"

    def check_availability(self) -> bool:
        return True

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """模拟嵌入：返回随机 1024 维向量（仅用于测试）。"""
        import random
        random.seed(42)
        return [[random.random() for _ in range(1024)] for _ in texts]

    def _simulate_response(
        self, text: str, messages: list[dict[str, str]]
    ) -> str:
        """根据输入内容推断任务类型并生成模拟回复。"""
        system = ""
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
                break

        # 判断是翻译、追问建议还是问答任务
        if "翻译" in system or "将以下英文" in system:
            return self._mock_translate(text)
        if "生成3个可能的追问问题" in system:
            return (
                '["这个结论依赖哪些文档片段？", '
                '"相关公式中的符号分别表示什么？", '
                '"作者后续如何验证这个方法？"]'
            )
        else:
            return self._mock_qa(text)

    def _mock_translate(self, text: str) -> str:
        """模拟翻译：标记原文关键术语，生成中文风格。"""
        # 简单检测是否包含数学公式
        has_formula = bool(re.search(r'【FORMULA_\d+】', text))
        # 识别常见术语并标注
        terms = {
            "attention mechanism": "注意力机制",
            "transformer": "Transformer模型",
            "self-attention": "自注意力",
            "multi-head": "多头",
            "encoder": "编码器",
            "decoder": "解码器",
            "embedding": "嵌入",
            "gradient": "梯度",
            "neural network": "神经网络",
            "softmax": "softmax",
            "residual": "残差",
        }
        # 生成模拟译文
        result = text
        for en, zh in terms.items():
            if en.lower() in text.lower():
                result = result.replace(en, f"{zh}({en})")
        if has_formula:
            return f"【模拟译文·公式已保护】\n{result}"
        return f"【模拟译文】\n{result}\n\n（注：此为测试模式下的模拟翻译。配置云端API Key后，此处将显示真实翻译结果。）"

    def _mock_qa(self, text: str) -> str:
        """模拟问答：根据问题生成模拟回答。"""
        return (
            f"【模拟回答】\n\n"
            f"针对您的问题，以下是基于文档内容的回答：\n\n"
            f"该段落描述了相关的技术概念和方法。具体来说，"
            f"文档中详细介绍了注意力机制的原理、"
            f"多头注意力的计算方式以及位置编码的设计。\n\n"
            f"如果您需要更深入的解释，请尝试：\n"
            f"1. 在更具体的段落上双击提问\n"
            f"2. 使用右键菜单的\"解释此概念\"功能\n"
            f"3. 配置云端API获取真实的AI回答\n\n"
            f"（注：当前为测试模式。）"
        )


# =============================================================================
# 工作线程
# =============================================================================

class _TranslationThread(QThread):
    """翻译线程 —— 直接继承 QThread，信号最多可靠。"""
    token_generated = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)

    def __init__(self, service: TranslationService, block: DocumentBlock, domain: str) -> None:
        super().__init__()
        self._service = service
        self._block = block
        self._domain = domain

    def run(self) -> None:
        try:
            result = self._service.translate_block(self._block, self._domain, stream=True)
            full_raw_text = ""

            # 1. 流式输出：逐字发送给 UI（屏幕暂显示占位符如 【FORMULA_0】）
            for token in result:  # type: ignore[union-attr]
                full_raw_text += token
                self.token_generated.emit(token)

            # 2. 流式输出结束后恢复 LaTeX 公式（【FORMULA_0】→ $$...$$）
            final_text = self._service._post_process(full_raw_text)

            # 3. 将包含真实 LaTeX 的最终文本发给 UI 渲染
            self.finished_signal.emit(final_text)

        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")


class _QAThread(QThread):
    """问答线程 —— 直接继承 QThread。"""
    token_generated = Signal(str)
    finished_signal = Signal(str)
    followup_signal = Signal(list)
    error_signal = Signal(str)

    def __init__(
        self,
        service: QAService,
        question: str,
        current_block: DocumentBlock | None,
        retrieved_blocks: list[DocumentBlock],
        chat_history: list[dict[str, str]] | None,
    ) -> None:
        super().__init__()
        self._service = service
        self._question = question
        self._current_block = current_block
        self._retrieved_blocks = retrieved_blocks
        self._chat_history = chat_history

    def run(self) -> None:
        try:
            result = self._service.answer(
                self._question, self._current_block,
                self._retrieved_blocks, self._chat_history, stream=True,
            )
            full_text = ""
            for token in result:  # type: ignore[union-attr]
                full_text += token
                self.token_generated.emit(token)
            self.finished_signal.emit(full_text)
            try:
                followups = self._service.generate_followup_questions(self._question, full_text)
            except Exception:
                followups = []
            self.followup_signal.emit(followups)
        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")
