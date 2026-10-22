import json
import re
from datetime import date

import pandas as pd
import pytest
from dashboard_fixture import write_profile, write_state

from tqqq_blend.dashboard import build, due_through

NOW = pd.Timestamp("2026-10-20 17:00", tz="America/New_York")
DATA = re.compile(r'<script id="dashboard-data" type="application/json">(.*?)</script>', re.S)


def page_data(html: str) -> dict:
    return json.loads(DATA.search(html).group(1))


@pytest.fixture
def built(tmp_path):
    runs = write_state(tmp_path / "paper")
    (tmp_path / "live").mkdir()
    profiles = [
        write_profile(tmp_path / "paper.toml", "config.ibkr-paper.toml", tmp_path / "paper"),
        write_profile(tmp_path / "live.toml", "config.ibkr-live.toml", tmp_path / "live"),
    ]
    html = build([str(p) for p in profiles], tmp_path / "out", now=NOW).read_text(encoding="utf-8")
    return runs, html, page_data(html)


def test_metrics_come_from_the_recorded_runs(built):
    runs, _, data = built
    paper = data["strategies"][0]["accounts"][0]
    valued = [r for r in runs if "equity" in r]
    m = paper["metrics"]
    assert m["equity"] == pytest.approx(valued[-1]["equity"])
    assert m["return"] == pytest.approx(valued[-1]["equity"] / valued[0]["equity"] - 1)
    assert m["qqq_return"] == pytest.approx(valued[-1]["qqq"] / valued[0]["qqq"] - 1)
    assert m["long_return"] == pytest.approx(valued[-1]["prices"]["TQQQ"] / valued[0]["prices"]["TQQQ"] - 1)
    peak, worst = valued[0]["equity"], 0.0
    for r in valued:
        peak = max(peak, r["equity"])
        worst = min(worst, r["equity"] / peak - 1)
    assert m["max_drawdown"] == pytest.approx(worst)


def test_one_point_per_day_and_failed_runs_never_become_points(built):
    runs, _, data = built
    paper = data["strategies"][0]["accounts"][0]
    dates = [p["date"] for p in paper["series"]]
    assert len(dates) == len(set(dates))
    assert "2026-09-15" not in dates  # that day's only run failed
    assert "2026-10-06" in dates  # failed then retried: the retry counts
    assert paper["runs_total"] == len(runs)
    assert paper["errors_30d"] == 1  # the 2026-10-06 failure; 2026-09-15 is over 30 days old


def test_missed_trading_days_are_reported(built):
    _, _, data = built
    paper = data["strategies"][0]["accounts"][0]
    assert paper["missed_days"] == ["2026-09-22"]
    assert paper["due_through"] == "2026-10-20"


def test_profile_without_runs_and_live_mode(built):
    _, _, data = built
    paper, live = data["strategies"][0]["accounts"]
    assert paper["mode"] == "paper"
    assert live["mode"] == "live-disabled"  # live_trading_enabled = false in config.ibkr-live.toml
    assert live["runs_total"] == 0 and live["metrics"] is None and live["series"] == []


def test_account_ids_never_reach_the_page(built):
    _, html, _ = built
    assert "DU1234567" not in html
    assert "[account]" in html


def test_recorded_text_cannot_close_the_data_script(tmp_path):
    state = tmp_path / "s"
    write_state(state, start="2026-10-12", end="2026-10-16", error_on=None, missed=None, retried=None)
    with open(state / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-16T15:40:00", "status": "error: </script><script>alert(1)</script>"}) + "\n")
    html = build([str(write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", state))], tmp_path / "out", now=NOW)
    text = html.read_text(encoding="utf-8")
    assert "<script>alert(1)" not in text
    assert page_data(text)["strategies"][0]["accounts"][0]["last_run"]["status"].endswith("</script>")


def test_nan_and_unreadable_lines_are_handled_not_hidden(tmp_path):
    state = tmp_path / "s"
    write_state(state, start="2026-10-12", end="2026-10-16", error_on=None, missed=None, retried=None)
    with open(state / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-10-16T15:41:00", "status": "no change", "net": NaN, "equity": NaN}\n')
        f.write("not json\n")
    html = build([str(write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", state))], tmp_path / "out", now=NOW)
    paper = page_data(html.read_text(encoding="utf-8"))["strategies"][0]["accounts"][0]
    assert paper["unreadable_run_lines"] == 1
    assert paper["last_run"]["net"] is None
    assert paper["decision"]["ts"] < "2026-10-16T15:41:00"  # a NaN signal is not a decision


def test_kill_switch_and_halt_are_surfaced(tmp_path):
    state = tmp_path / "s"
    write_state(state, start="2026-10-12", end="2026-10-16", error_on=None, missed=None, retried=None)
    (state / "STOP").touch()
    (state / "state.json").write_text(json.dumps({"halted": "drawdown breaker 50000 vs peak 100000"}))
    html = build([str(write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", state))], tmp_path / "out", now=NOW)
    paper = page_data(html.read_text(encoding="utf-8"))["strategies"][0]["accounts"][0]
    assert paper["kill_switch"] is True
    assert paper["halted"].startswith("drawdown breaker")


@pytest.mark.parametrize(
    "now, expected",
    [
        ("2026-10-20 15:00", date(2026, 10, 19)),  # Tuesday before the run: Monday is the last one due
        ("2026-10-20 15:45", date(2026, 10, 20)),
        ("2026-10-25 12:00", date(2026, 10, 23)),  # Sunday: Friday
    ],
)
def test_due_through(now, expected):
    assert due_through(pd.Timestamp(now, tz="America/New_York")) == expected


def test_zero_or_negative_equity_does_not_crash_the_build(tmp_path):
    state = tmp_path / "s"
    state.mkdir()
    with open(state / "runs.jsonl", "w", encoding="utf-8") as f:
        for d, eq in (("2026-10-14", 0.0), ("2026-10-15", -50.0), ("2026-10-16", 100.0)):
            f.write(json.dumps({"ts": f"{d}T15:30:00", "status": "no change", "net": 0.0, "equity": eq}) + "\n")
    html = build([str(write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", state))], tmp_path / "out", now=NOW)
    m = page_data(html.read_text(encoding="utf-8"))["strategies"][0]["accounts"][0]["metrics"]
    assert m["return"] is None  # no ratio from a zero starting equity
    assert m["max_drawdown"] == 0.0


def test_trades_carry_their_runs_reasons(built):
    _, _, data = built
    paper = data["strategies"][0]["accounts"][0]
    assert paper["rebalance_band"] == pytest.approx(0.10)
    recent = [t for t in paper["trades"] if t["ts"] >= "2026-10-01"]
    older = [t for t in paper["trades"] if t["ts"] < "2026-10-01"]
    assert recent and older
    for t in recent:  # runs from 2026-10-01 on recorded "why"
        assert t["why"]["rules"][0].startswith("Trend: ")
        assert t["why"]["net"] == pytest.approx(float(t["net_signal"]), abs=1e-3)
    for t in older:  # earlier runs predate it: the target is still known, the rules are not
        assert t["why"]["rules"] == [] and t["why"]["net"] is not None
