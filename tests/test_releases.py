from pe_core import releases

APP = """# Changelog

Intro.

## 0.9.50 (beta)

### Behaviour changes
- Fifty.

## 0.9.49 (beta)

- Forty-nine.

## 0.9.48 (beta)

- Forty-eight.
"""
CARD = """# Changelog

## 0.9.50

- No card changes; version kept in step with the app.

## 0.9.49

- New update card.
"""


def test_sections_and_versions():
    s = releases.sections(APP)
    assert list(s) == ["0.9.50", "0.9.49", "0.9.48"]
    assert s["0.9.49"] == "- Forty-nine."
    assert releases.vkey("v0.9.10") > releases.vkey("0.9.9")


def test_summary_concatenates_newer_notes_with_card_changes():
    out = releases.summary("0.9.48", APP, CARD)
    assert out["available"] and out["latest"] == "0.9.50" and out["behind"] == 2
    assert out["notes"].index("### 0.9.50") < out["notes"].index("### 0.9.49")
    assert "**Card:** - New update card." in out["notes"]
    assert "No card changes" not in out["notes"] and "Forty-eight" not in out["notes"]
    same = releases.summary("0.9.50", APP, CARD)
    assert not same["available"] and same["behind"] == 0 and same["notes"] == ""


def test_long_notes_turn_into_links():
    out = releases.summary("0.9.48", APP, CARD, budget=60)
    assert "### 0.9.50" in out["notes"] and "### 0.9.49" not in out["notes"]
    assert "[0.9.49](https://github.com/durkimat/ha-powerengine-controller/releases/tag/v0.9.49)" in out["notes"]
