"""Symbol equivalence evidence for TinyBDMath alignment.

This module deliberately avoids TinyBDMath-local symbol tables.  It only
canonicalizes identity strings that were already produced upstream by KaTeX
target-tree building or PDF glyph identity repair.
"""

from __future__ import annotations

from functools import lru_cache
import unicodedata

from src.core.symbol_identity_repair import latex_for_unicode_text


class TinyBDSymbolEquivalence:
    """Resolve target/PDF symbol equality from existing identity evidence."""

    def equivalent(
        self,
        target_text: str,
        pdf_text: str,
        pdf_latex: str = "",
        target_aliases: tuple[str, ...] = (),
    ) -> bool:
        target_keys = self.target_keys(target_text, target_aliases=target_aliases)
        pdf_keys = self.pdf_keys(pdf_text, pdf_latex)
        return bool(target_keys and pdf_keys and target_keys.intersection(pdf_keys))

    def target_keys(self, text: str, *, target_aliases: tuple[str, ...] = ()) -> set[str]:
        return _keys_for_values((text, *target_aliases))

    def pdf_keys(self, text: str, latex: str = "") -> set[str]:
        return _keys_for_values((text, latex))


@lru_cache(maxsize=8192)
def _keys_for_values(values: tuple[str, ...]) -> set[str]:
    keys: set[str] = set()
    for value in values:
        keys.update(_basic_keys(value))
    return keys


def _basic_keys(value: str) -> set[str]:
    normalized = _canonical_text(value)
    if not normalized:
        return set()
    keys = {normalized}
    folded = normalized.casefold()
    if folded:
        keys.add(folded)
    latex = _canonical_text(latex_for_unicode_text(normalized))
    if latex and latex != normalized:
        keys.add(latex)
        keys.add(latex.casefold())
    return keys


def _canonical_text(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    return unicodedata.normalize("NFKC", value)
