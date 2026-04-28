from __future__ import annotations

from pulse.core.learning import PreferenceExtractor


class _JobGoalLLM:
    def __init__(self) -> None:
        self.prompt = ""
        self.route = ""

    def invoke_text(self, prompt: str, *, route: str = "default") -> str:
        self.prompt = prompt
        self.route = route
        return """
        {
          "core_prefs": {},
          "soul_updates": {},
          "domain_prefs": [
            {
              "domain": "job",
              "op": "memory.record",
              "args": {
                "item": {
                  "type": "favor_trait",
                  "target": null,
                  "content": "偏好互联网垂直实习，业务要合适、垂直匹配、含金量高，秋招叙事顺畅",
                  "valid_until": null
                }
              },
              "evidence": "我的核心目的是找一段有含金量的互联网的垂直实习",
              "confidence": 0.93
            }
          ],
          "evidences": ["job_goal"]
        }
        """


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


def test_preference_extractor_routes_abstract_job_goal_to_domain_memory() -> None:
    llm = _JobGoalLLM()
    extractor = PreferenceExtractor(llm_router=llm)

    result = extractor.extract(
        "我的核心目的是找一段有含金量的互联网的垂直实习，"
        "第一目标是业务要合适，垂直匹配，含金量要高，秋招叙事顺畅。"
    )

    assert llm.route == "classification"
    assert "抽象择业目标" in llm.prompt
    assert result.core_prefs == {}
    assert len(result.domain_prefs) == 1
    pref = result.domain_prefs[0]
    assert pref.domain == "job"
    assert pref.op == "memory.record"
    assert pref.args["item"]["type"] == "favor_trait"
    assert "垂直匹配" in pref.args["item"]["content"]


def test_preference_extractor_passes_full_user_text_to_llm() -> None:
    llm = _JobGoalLLM()
    extractor = PreferenceExtractor(llm_router=llm)
    tail_goal = "尾部目标：最低薪资不要低于300每天，优先杭州上海，已联系过不要重复投递"
    long_text = "背景信息" * 700 + tail_goal

    extractor.extract(long_text)

    assert tail_goal in llm.prompt
