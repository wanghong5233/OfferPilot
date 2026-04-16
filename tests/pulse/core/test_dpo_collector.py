from __future__ import annotations

from pulse.core.learning import DPOCollector
from tests.pulse.support.fakes import FakeCorrectionsDB


def test_dpo_collector_add_and_recent(tmp_path) -> None:
    _ = tmp_path
    collector = DPOCollector(db_engine=FakeCorrectionsDB())
    first = collector.add_pair(
        prompt="用户问天气",
        chosen="给出天气并提醒是否需要航班信息",
        rejected="只回复不知道",
        metadata={"session_id": "u1"},
    )
    assert first["pair_id"].startswith("dpo_")
    assert collector.count() == 1

    second = collector.add_pair(
        prompt="用户纠正默认城市",
        chosen="更新默认城市并确认",
        rejected="忽略纠正",
    )
    assert collector.count() == 2

    rows = collector.recent(limit=5)
    assert len(rows) == 2
    assert rows[0]["prompt"] == "用户纠正默认城市"
