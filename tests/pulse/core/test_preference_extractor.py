from __future__ import annotations

from pulse.core.learning import PreferenceExtractor


def test_preference_extractor_extracts_preference_and_style() -> None:
    extractor = PreferenceExtractor()
    result = extractor.extract("以后默认用杭州，我不喜欢外包岗，回答尽量简短")
    assert result.prefs_updates["default_location"] == "杭州"
    assert "外包岗" in result.prefs_updates["dislike"]
    assert result.soul_updates["tone"] == "concise"
    assert "style_concise" in result.evidences


def test_preference_extractor_extracts_preferred_name() -> None:
    extractor = PreferenceExtractor()
    result = extractor.extract("以后称呼我 老王")
    assert result.prefs_updates["preferred_name"] == "老王"
