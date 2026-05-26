"""Tests for the SQLite decision log: save, retrieve, update, stats."""

from agent.memory import AgentMemory, Decision


def _decision(**overrides) -> Decision:
    base = dict(
        action="buy",
        thinking="full reasoning",
        reasoning_summary="bought the breakout",
        confidence=0.7,
        cycle=1,
        ticker="AAPL.US",
        quantity=2.0,
        price_at_decision=200.0,
    )
    base.update(overrides)
    return Decision(**base)


def test_save_and_get_all_roundtrip(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    d = _decision()
    mem.save(d)
    all_rows = mem.get_all()
    assert len(all_rows) == 1
    assert all_rows[0].id == d.id
    assert all_rows[0].ticker == "AAPL.US"
    assert all_rows[0].confidence == 0.7


def test_get_recent_returns_newest_first(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    mem.save(_decision(cycle=1, timestamp="2026-01-01T00:00:01"))
    mem.save(_decision(cycle=2, timestamp="2026-01-01T00:00:02"))
    mem.save(_decision(cycle=3, timestamp="2026-01-01T00:00:03"))
    recent = mem.get_recent(2)
    assert [d.cycle for d in recent] == [3, 2]


def test_update_outcome_backfills_price_and_return(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    d = _decision()
    mem.save(d)
    mem.update_outcome(d.id, outcome_price=210.0, outcome_return_pct=5.0)
    stored = mem.get_all()[0]
    assert stored.outcome_price == 210.0
    assert stored.outcome_return_pct == 5.0


def test_judge_scores_persist_as_dict(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    d = _decision(judge_scores={"overall": 4, "summary": "solid"})
    mem.save(d)
    stored = mem.get_all()[0]
    assert stored.judge_scores == {"overall": 4, "summary": "solid"}


def test_get_stats_computes_win_rate_and_confidence(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    mem.save(_decision(confidence=0.8, outcome_return_pct=5.0))   # win
    mem.save(_decision(confidence=0.6, outcome_return_pct=-2.0))  # loss
    mem.save(_decision(confidence=0.4))                          # no outcome yet
    stats = mem.get_stats()
    assert stats["total_decisions"] == 3
    assert stats["decisions_with_outcome"] == 2
    assert stats["win_rate"] == 0.5
    assert abs(stats["avg_return_pct"] - 1.5) < 1e-9
    assert abs(stats["avg_confidence"] - 0.6) < 1e-9


def test_stats_empty_db_is_safe(tmp_path):
    mem = AgentMemory(db_path=str(tmp_path / "m.db"))
    stats = mem.get_stats()
    assert stats["total_decisions"] == 0
    assert stats["win_rate"] is None
    assert stats["avg_confidence"] is None
