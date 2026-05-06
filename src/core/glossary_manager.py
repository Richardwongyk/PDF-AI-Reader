"""
术语表管理器 —— 专业术语库的加载、查询与 Prompt 注入。

负责从 JSON 文件加载内置学科术语包、导入用户自定义术语表、
提供术语的增删改查、生成翻译 Prompt 中使用的术语映射字符串。
"""

from __future__ import annotations

from src.core.models import GlossaryEntry
from src.data.glossary_repo import GlossaryRepo


class GlossaryManager:
    """术语表管理器。

    负责：
    - 从 JSON 文件加载内置学科术语包
    - 从用户自定义文件导入术语
    - 术语的增删改查
    - 生成翻译 Prompt 中使用的术语映射字符串
    - 一词多义冲突解决
    """

    def __init__(self, glossary_dir: str) -> None:
        """初始化术语表管理器。

        Args:
            glossary_dir: 术语表 JSON 文件所在目录路径。
        """
        self._dir = glossary_dir
        self._repo = GlossaryRepo(glossary_dir)
        # 内存中的术语表: {domain: [GlossaryEntry, ...]}
        self._terms: dict[str, list[GlossaryEntry]] = {}
        self._load_builtin_glossaries()

    def _load_builtin_glossaries(self) -> None:
        """加载内置学科术语包到内存。"""
        self._terms = self._repo.load_all()

    def reload(self) -> None:
        """重新加载所有术语表（用于文件变更后刷新）。"""
        self._load_builtin_glossaries()

    @property
    def domains(self) -> list[str]:
        """返回已加载的学科领域列表。"""
        return list(self._terms.keys())

    def import_user_glossary(self, filepath: str) -> int:
        """导入用户自定义术语表文件。

        支持 JSON (.json) 和 CSV (.csv) 格式。
        导入的术语存入 "imported" 领域。

        Args:
            filepath: 文件路径。

        Returns:
            成功导入的术语条数。
        """
        entries = self._repo.import_from_file(filepath)
        if "imported" not in self._terms:
            self._terms["imported"] = []
        self._terms["imported"].extend(entries)
        return len(entries)

    def add_term(
        self, en: str, zh: str, domain: str, force: bool = False
    ) -> GlossaryEntry:
        """添加一条术语。

        Args:
            en: 英文术语。
            zh: 中文翻译。
            domain: 所属学科。
            force: 是否强制使用此翻译。

        Returns:
            新创建的 GlossaryEntry 对象。
        """
        entry = GlossaryEntry(en=en, zh=zh, domain=domain, force=force)
        if domain not in self._terms:
            self._terms[domain] = []
        self._terms[domain].append(entry)
        return entry

    def remove_term(self, en: str, domain: str) -> bool:
        """删除一条术语。

        Args:
            en: 英文术语。
            domain: 所属学科。

        Returns:
            是否成功删除。
        """
        if domain not in self._terms:
            return False
        for i, entry in enumerate(self._terms[domain]):
            if entry.en.lower() == en.lower():
                del self._terms[domain][i]
                return True
        return False

    def get_translation_mapping(self, domains: list[str]) -> dict[str, str]:
        """获取指定领域的 {英文: 中文} 翻译映射。

        若一词在多个选定领域中有不同翻译，
        按领域列表顺序选择第一个匹配的。
        强制标志（force=True）的条目优先。

        Args:
            domains: 领域列表（如 ["math", "cs_ml"]）。

        Returns:
            英文术语到中文翻译的映射字典。
        """
        mapping: dict[str, str] = {}
        for domain in domains:
            if domain not in self._terms:
                continue
            for entry in self._terms[domain]:
                key = entry.en.lower()
                # force=True 的条目会覆盖之前的映射
                if entry.force or key not in mapping:
                    mapping[key] = entry.zh
                # 也添加别名的映射
                for alias in entry.aliases:
                    alias_key = alias.lower()
                    if entry.force or alias_key not in mapping:
                        mapping[alias_key] = entry.zh
        return mapping

    def format_for_prompt(self, domains: list[str]) -> str:
        """将术语映射格式化为 Prompt 可注入的字符串。

        输出格式：
        - manifold -> 流形
        - gradient -> 梯度

        Args:
            domains: 领域列表。

        Returns:
            格式化的术语表字符串。
        """
        mapping = self.get_translation_mapping(domains)
        if not mapping:
            return ""
        lines: list[str] = []
        for en, zh in sorted(mapping.items()):
            lines.append(f"- {en} -> {zh}")
        return "\n".join(lines)

    def search_terms(self, keyword: str) -> list[GlossaryEntry]:
        """搜索术语（中英文模糊匹配）。

        Args:
            keyword: 搜索关键词。

        Returns:
            匹配的术语条目列表。
        """
        results: list[GlossaryEntry] = []
        kw = keyword.lower()
        for entries in self._terms.values():
            for entry in entries:
                if kw in entry.en.lower() or kw in entry.zh:
                    results.append(entry)
        return results

    def resolve_conflict(self, en: str, preferred_domain: str) -> str:
        """解决一词多义冲突。

        例如 "kernel" 在数学中译作"核"，在 CS 中译作"内核"。
        优先返回首选领域的翻译。

        Args:
            en: 英文术语。
            preferred_domain: 首选领域。

        Returns:
            对应领域的翻译。若未找到则返回英文原文。
        """
        mapping = self.get_translation_mapping([preferred_domain])
        return mapping.get(en.lower(), en)

    def get_entries(self, domains: list[str] | None = None) -> list[GlossaryEntry]:
        """获取指定领域的所有术语条目。

        Args:
            domains: 领域列表。为 None 时返回全部。

        Returns:
            GlossaryEntry 列表。
        """
        if domains is None:
            domains = list(self._terms.keys())
        result: list[GlossaryEntry] = []
        for domain in domains:
            if domain in self._terms:
                result.extend(self._terms[domain])
        return result

    def save(self) -> None:
        """将所有术语表保存到 JSON 文件。"""
        for domain, entries in self._terms.items():
            self._repo.save_domain(domain, entries)
