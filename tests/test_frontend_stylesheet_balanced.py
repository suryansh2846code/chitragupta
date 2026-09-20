"""`styles.css` must parse — every block closed, no conflict markers left.

Both failures this guards against have happened, and neither showed up as an
error anywhere. A merge dropped the `}` closing an
`@media (prefers-reduced-motion: reduce)` block, so ~40 rules that followed it —
the whole Appearance screen — silently became reduced-motion-only. The roster
rendered as unstyled grey blocks with the labels running together, and the
editor's gold accents came through in the package's own default orange, because
the rules that override them were inside the swallowed range.

CSS has no parse errors to catch: a browser recovers from an unclosed block by
nesting everything after it, and the page still loads. Nothing in the test suite
looked at the stylesheet as a document, so the only signal was a screenshot.
"""
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "chitragupta" / "web"
SHEETS = sorted(WEB.glob("*.css"))


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_braces_balanced(sheet: Path) -> None:
    """Counted rather than parsed: the imbalance is what breaks the cascade,
    and a brace inside a string or a comment would be an unusual enough thing
    to write that catching it as a false positive is the cheaper mistake."""
    text = sheet.read_text()
    opened, closed = text.count("{"), text.count("}")
    assert opened == closed, (
        f"{sheet.name}: {opened} '{{' vs {closed} '}}' — an unclosed block "
        f"swallows every rule after it into itself, and the browser will not "
        f"complain. Difference: {opened - closed:+d}"
    )


@pytest.mark.parametrize("sheet", SHEETS, ids=lambda p: p.name)
def test_no_conflict_markers(sheet: Path) -> None:
    for marker in ("<<<<<<<", ">>>>>>>"):
        assert marker not in sheet.read_text(), (
            f"{sheet.name} still contains a '{marker}' conflict marker"
        )
