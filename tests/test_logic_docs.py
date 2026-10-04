"""docs/logic: every diagram carries the init line that stops Mermaid clipping labels, and the index links resolve."""
import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs" / "logic"
BLOCK = re.compile(r"```mermaid\n(.*?)```", re.S)


def test_every_diagram_uses_svg_text_labels():
    seen = 0
    for page in sorted(DOCS.glob("*.md")):
        for body in BLOCK.findall(page.read_text(encoding="utf-8")):
            seen += 1
            first = body.lstrip().splitlines()[0]
            assert first.startswith("%%{init:") and '"htmlLabels": false' in first, \
                f"{page.name}: a Mermaid block must start with the init line from docs/logic/README.md"
    assert seen


def test_index_links_point_at_pages_that_exist():
    index = (DOCS / "README.md").read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([0-9][^)#]*\.md)", index):
        assert (DOCS / target).exists(), target
