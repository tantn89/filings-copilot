from __future__ import annotations

import pytest

from filings_copilot.tenk_section_parser import html_to_text, split_sections
from filings_copilot.text_chunker import chunk_text

FILLER = "Our results depend on many factors described in this report. " * 60


def make_10k_html() -> str:
    toc = "".join(
        f"<tr><td>Item {i}.</td><td>{t}</td><td>{p}</td></tr>"
        for i, t, p in [("1", "Business", 4), ("1A", "Risk Factors", 12),
                        ("7", "Management's Discussion", 30), ("8", "Financial Statements", 50)]
    )
    return f"""<html><body>
      <table>{toc}</table>
      <p style="font-weight:bold">Item&#160;1. Business</p><p>We make GPUs. {FILLER}</p>
      <p>ITEM 1A. RISK FACTORS</p><p>Export controls may hurt us. {FILLER}</p>
      <div>Item 7 — Management's Discussion and Analysis</div><p>Revenue grew. {FILLER}</p>
      <p>Item 8. Financial Statements</p><p>See below.</p>
      <script>var x = "Item 7. fake";</script>
    </body></html>"""


def test_html_to_text_puts_blocks_on_lines_and_drops_scripts() -> None:
    text = html_to_text("<p>Hello&#160;world</p><div>Second</div><script>bad()</script>")
    assert text.splitlines() == ["Hello world", "Second"]


def test_split_sections_skips_table_of_contents() -> None:
    sections = split_sections(html_to_text(make_10k_html()))
    assert [s.item for s in sections] == ["1", "1A", "7"]
    assert "Export controls may hurt us" in sections[1].text
    assert "Revenue grew" in sections[2].text
    assert "Financial Statements" not in sections[2].text


def test_split_sections_ignores_short_bodies() -> None:
    assert split_sections("Item 1A. Risk Factors\nSee elsewhere.\nItem 2. Properties") == []


def test_chunk_text_respects_size_and_overlap() -> None:
    words = [f"w{i}" for i in range(1000)]
    chunks = chunk_text(" ".join(words), size_words=200, overlap_words=50)
    assert all(len(c.split()) <= 200 for c in chunks)
    assert chunks[0].split()[-50:] == chunks[1].split()[:50]
    covered = {w for c in chunks for w in c.split()}
    assert covered == set(words)


def test_chunk_text_keeps_short_paragraphs_together() -> None:
    chunks = chunk_text("alpha beta\ngamma delta\nepsilon", size_words=50, overlap_words=5)
    assert chunks == ["alpha beta gamma delta epsilon"]


def test_chunk_text_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", size_words=10, overlap_words=10)


def test_split_sections_folds_running_page_headers() -> None:
    page = "Demand for cloud services keeps growing across regions. " * 25
    text = "\n".join([
        "Item 1.", "Business", "Item 1A.", "Risk Factors", "Item 2.", "Properties",  # TOC
        "PART I", "Item 1", "ITEM 1. BUSINESS", page,
        "PART I", "Item 1", page,                                   # page header, same item
        "PART I", "Item 1A", "ITEM 1A. RISK FACTORS", page, "PART I", "Item 1A", page,
        "Item 2. Properties", "We own campuses.",
    ])
    sections = {s.item: s.text for s in split_sections(text)}
    assert set(sections) == {"1", "1A"}
    assert sections["1"].count("Demand for cloud") == 50
    assert "PART I" not in sections["1A"]
    assert "Item 2" not in sections["1A"]


def test_table_cells_stay_separate_and_currency_cells_rejoin() -> None:
    html = ("<table><tr><td>Revenue</td><td>$</td><td>130,497</td><td></td>"
            "<td>$</td><td>60,922</td></tr><tr><td>Gross margin</td><td>75.0</td><td>%</td>"
            "<td>(1,234</td><td>)</td></tr></table>")
    assert html_to_text(html).splitlines() == [
        "Revenue | $130,497 | $60,922", "Gross margin | 75.0% | (1,234)"]


def test_hard_wrapped_source_lines_are_not_headings() -> None:
    body = "Revenue grew strongly this year. " * 80
    html = (f"<p>Item 7. Management's Discussion</p><p>{body} As described in\n"
            f"Item 1A. Risk Factors, demand may fall. {body}</p><p>Item 8. Financial</p>")
    sections = split_sections(html_to_text(html))
    assert [s.item for s in sections] == ["7"]
    assert "demand may fall" in sections[0].text


def test_last_section_stops_at_the_signature_page() -> None:
    body = "Market risk discussion. " * 120
    text = f"Item 7A. Market Risk\n{body}\nSIGNATURES\n" + "Exhibit text. " * 300
    (section,) = split_sections(text)
    assert "Exhibit text" not in section.text
