"""
术语表持久化仓库。

负责读写 data/glossary/ 目录下的 JSON 术语表文件。
支持内置学科术语包的加载，以及用户自定义文件的导入。
"""

import csv
import json
import logging
import os
from pathlib import Path

from src.core.models import GlossaryEntry

_logger = logging.getLogger(__name__)


class GlossaryRepo:
    """术语表持久化仓库。

    负责：
    - 从 JSON 文件加载内置学科术语包
    - 从用户自定义 JSON/CSV 文件导入术语
    - 保存术语表到 JSON 文件
    - 支持多领域术语的聚合和查询
    """

    def __init__(self, glossary_dir: str) -> None:
        """初始化术语表仓库。

        Args:
            glossary_dir: 术语表目录路径（如 "./data/glossary"）。
        """
        self._dir: str = glossary_dir
        os.makedirs(self._dir, exist_ok=True)

    def load_all(self) -> dict[str, list[GlossaryEntry]]:
        """加载目录下所有 JSON 术语表文件。

        文件名（去掉 .json 后缀）作为领域名称。

        Returns:
            {domain_name: [GlossaryEntry, ...]} 字典。
        """
        all_terms: dict[str, list[GlossaryEntry]] = {}

        if not os.path.isdir(self._dir):
            return all_terms

        for filename in os.listdir(self._dir):
            if not filename.endswith(".json"):
                continue
            domain = filename[:-5]  # 去掉 .json
            entries = self.load_domain(domain)
            if entries:
                all_terms[domain] = entries

        return all_terms

    def load_domain(self, domain: str) -> list[GlossaryEntry]:
        """加载指定领域的术语表文件。

        文件路径: {glossary_dir}/{domain}.json

        Args:
            domain: 领域名称（如 "math"）。

        Returns:
            GlossaryEntry 列表。
        """
        filepath = os.path.join(self._dir, f"{domain}.json")
        if not os.path.isfile(filepath):
            return []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data: dict = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("术语表文件读取失败: %s — %s", filepath, e)
            return []

        # 兼容两种 JSON 结构：
        # {"domain": "math", "terms": [...]} 或 直接 [...] 列表
        if isinstance(data, list):
            raw_terms = data
        elif isinstance(data, dict):
            raw_terms = data.get("terms", [])
        else:
            return []

        entries: list[GlossaryEntry] = []
        for item in raw_terms:
            if isinstance(item, dict):
                try:
                    entry = GlossaryEntry(
                        en=item.get("en", ""),
                        zh=item.get("zh", ""),
                        domain=item.get("domain", domain),
                        force=item.get("force", False),
                        aliases=item.get("aliases", []),
                        notes=item.get("notes", ""),
                    )
                    entries.append(entry)
                except Exception:
                    continue

        return entries

    def save_domain(self, domain: str, entries: list[GlossaryEntry]) -> None:
        """保存指定领域的术语表（覆盖原文件）。

        Args:
            domain: 领域名称。
            entries: 术语条目列表。
        """
        filepath = os.path.join(self._dir, f"{domain}.json")
        data: dict = {
            "domain": domain,
            "terms": [e.model_dump() for e in entries],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def import_from_file(self, filepath: str) -> list[GlossaryEntry]:
        """从外部文件导入术语表。

        支持格式：JSON (.json)、CSV (.csv)。
        JSON 格式要求与 load_domain 相同。
        CSV 格式要求列：en, zh, domain(可选), force(可选)。

        Args:
            filepath: 文件路径。

        Returns:
            解析出的 GlossaryEntry 列表。
        """
        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".json":
            return self._import_json(filepath)
        elif ext == ".csv":
            return self._import_csv(filepath)
        else:
            _logger.error("不支持的术语表文件格式: %s", ext)
            raise ValueError(f"不支持的术语表文件格式: {ext}。仅支持 JSON 和 CSV。")

    def _import_json(self, filepath: str) -> list[GlossaryEntry]:
        """从 JSON 文件导入术语表。

        Args:
            filepath: JSON 文件路径。

        Returns:
            GlossaryEntry 列表。
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data: dict | list = json.load(f)

        if isinstance(data, list):
            raw_terms = data
            domain = "imported"
        else:
            raw_terms = data.get("terms", [])
            domain = data.get("domain", "imported")

        entries: list[GlossaryEntry] = []
        for item in raw_terms:
            if isinstance(item, dict):
                try:
                    entries.append(GlossaryEntry(
                        en=item.get("en", ""),
                        zh=item.get("zh", ""),
                        domain=item.get("domain", domain),
                        force=item.get("force", False),
                        aliases=item.get("aliases", []),
                        notes=item.get("notes", ""),
                    ))
                except Exception:
                    continue
        return entries

    def _import_csv(self, filepath: str) -> list[GlossaryEntry]:
        """从 CSV 文件导入术语表。

        CSV 列：en, zh, domain(可选), force(可选), notes(可选)

        Args:
            filepath: CSV 文件路径。

        Returns:
            GlossaryEntry 列表。
        """
        entries: list[GlossaryEntry] = []
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "en" not in row or "zh" not in row:
                    continue
                if not row["en"].strip() or not row["zh"].strip():
                    continue
                entries.append(GlossaryEntry(
                    en=row["en"].strip(),
                    zh=row["zh"].strip(),
                    domain=row.get("domain", "imported").strip(),
                    force=row.get("force", "false").strip().lower() == "true",
                    aliases=[a.strip() for a in row.get("aliases", "").split(",") if a.strip()],
                    notes=row.get("notes", "").strip(),
                ))
        return entries
