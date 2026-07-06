from src.core.models import BlockType, DocumentBlock
from src.core.navigator import Navigator


def _block(
    idx: int,
    content: str,
    block_type: BlockType = BlockType.HEADING,
    page_num: int = 0,
) -> DocumentBlock:
    y = float(idx * 10)
    return DocumentBlock(
        id=f"p{page_num}_b{idx}",
        page_num=page_num,
        block_type=block_type,
        content=content,
        bbox=(0.0, y, 100.0, y + 8.0),
    )


def test_generated_toc_returns_empty_for_unreliable_heading_text() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(0, "\ufffd\ufffd\ufffd\ufffd"),
        _block(1, "(cid:12)(cid:91)"),
        _block(2, "----"),
        _block(3, "regular paragraph", BlockType.PARAGRAPH),
    ])

    assert toc == []
    assert navigator.toc == []


def test_generated_toc_requires_multiple_distinct_readable_headings() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(0, "Introduction"),
        _block(1, "Introduction", page_num=1),
    ])

    assert toc == []
    assert navigator.toc == []


def test_generated_toc_keeps_readable_headings_and_normalizes_titles() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(2, " 2 Method\n", page_num=1),
        _block(0, "1 Introduction", page_num=0),
        _block(3, "方法", page_num=2),
        _block(4, "Cœur et modèle", page_num=3),
        _block(1, "1 Introduction", page_num=0),
    ])

    assert [item["title"] for item in toc] == [
        "1 Introduction",
        "2 Method",
        "方法",
        "Cœur et modèle",
    ]
    assert [item["page"] for item in toc] == [0, 1, 2, 3]
    assert navigator.toc == toc


def test_generated_toc_prefers_structural_titles_over_formula_heading_noise() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(0, "sin lim x", page_num=0),
        _block(1, "第一节 函数的极限", BlockType.PARAGRAPH, page_num=0),
        _block(2, "一、直观的极限概念和无穷小量", BlockType.PARAGRAPH, page_num=0),
        _block(3, "二、导数的几何意义，微分 1、导数的几何意义", BlockType.PARAGRAPH, page_num=5),
        _block(4, "div", page_num=36),
    ])

    assert [item["title"] for item in toc] == [
        "第一节 函数的极限",
        "一、直观的极限概念和无穷小量",
        "二、导数的几何意义，微分",
        "1、导数的几何意义",
    ]
    assert [item["level"] for item in toc] == [1, 2, 2, 3]
    assert [item["page"] for item in toc] == [0, 0, 5, 5]


def test_generated_toc_splits_multiple_structural_titles_from_one_block_and_deduplicates() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(
            0,
            "一、函数概念 1、定义域 1、定义域 二、函数性质 2、单调性",
            BlockType.PARAGRAPH,
            page_num=3,
        ),
        _block(1, "二、函数性质", BlockType.PARAGRAPH, page_num=4),
    ])

    assert [item["title"] for item in toc] == [
        "一、函数概念",
        "1、定义域",
        "二、函数性质",
        "2、单调性",
    ]
    assert [item["level"] for item in toc] == [2, 3, 2, 3]
    assert [item["page"] for item in toc] == [3, 3, 3, 3]


def test_generated_toc_rejects_formula_like_heading_fragments() -> None:
    navigator = Navigator()

    toc = navigator.generate_toc_from_blocks([
        _block(0, "sin lim x"),
        _block(1, "div", page_num=1),
        _block(2, "x y z", page_num=2),
    ])

    assert toc == []
    assert navigator.toc == []


def test_generated_toc_clears_previous_toc_when_later_document_has_no_reliable_toc() -> None:
    navigator = Navigator()
    navigator.generate_toc_from_blocks([
        _block(0, "1 Introduction"),
        _block(1, "2 Method", page_num=1),
    ])
    assert navigator.toc

    toc = navigator.generate_toc_from_blocks([])

    assert toc == []
    assert navigator.toc == []
