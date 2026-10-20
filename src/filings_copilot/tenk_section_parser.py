"""Split a 10-K HTML document into its standard Items (1, 1A, 7, 7A, ...).

10-K HTML differs a lot between filers, but every filing has "Item N." headings at the
start of a line. The table of contents repeats those headings, so for each item we keep
the occurrence followed by the longest body - the TOC entries are only one line long.
Running page headers ("PART I / Item 1A" on every page) are folded into the section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser

# Items we index for retrieval, with display titles.
SECTION_TITLES: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
}

_DASHES = "-" + chr(0x2013) + chr(0x2014)  # hyphen, en dash, em dash
_ITEM_HEADING = re.compile(
    r"^\s*item\s*(\d{1,2}[a-c]?)\s*[.:" + re.escape(_DASHES) + r"]?\s*(?=\S)",
    re.IGNORECASE | re.MULTILINE,
)
_BLOCK_TAGS = ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table")
# Bare "PART II" / "Item 7" / "Table of Contents" / page-number lines inside a section
# body are page headers and footers; drop them.
_RUNNING_HEADER = re.compile(
    r"^(?:part\s+[ivx]+|item\s*\d{1,2}[a-c]?\.?|table of contents|\d{1,3})\s*$\n?",
    re.IGNORECASE | re.MULTILINE,
)
# Shorter than this is a TOC line or cross-reference, not a real section body.
_MIN_SECTION_CHARS = 2000


@dataclass(frozen=True)
class Section:
    item: str
    title: str
    text: str


def html_to_text(html: str) -> str:
    """Flatten HTML into plain text, one block element per line."""
    tree = LexborHTMLParser(html)
    for node in tree.css("script, style, ix\\:header"):
        node.decompose()
    for tag in _BLOCK_TAGS:
        for node in tree.css(tag):
            node.insert_after("\n")
    body = tree.body or tree.root
    text = body.text(separator="") if body else ""
    text = text.replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def split_sections(text: str, wanted: dict[str, str] = SECTION_TITLES) -> list[Section]:
    headings: list[tuple[int, str]] = []
    for match in _ITEM_HEADING.finditer(text):
        item = match.group(1).upper()
        # Many filers repeat "PART I / Item 1A" as a running header on every page. A heading
        # for the item we are already in is a page header, not a new section.
        if not headings or headings[-1][1] != item:
            headings.append((match.start(), item))

    best: dict[str, tuple[int, int]] = {}
    for idx, (start, item) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(text)
        if item in wanted and end - start > best.get(item, (0, 0))[1] - best.get(item, (0, 0))[0]:
            best[item] = (start, end)

    sections = []
    for item, (start, end) in best.items():
        heading, _, body = text[start:end].strip().partition("\n")
        body = heading + "\n" + _RUNNING_HEADER.sub("", body)
        if len(body) >= _MIN_SECTION_CHARS:
            sections.append(Section(item=item, title=wanted[item], text=body))
    return sorted(sections, key=lambda s: list(wanted).index(s.item))
