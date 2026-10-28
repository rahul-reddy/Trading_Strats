import json
import re
from datetime import date

import pandas as pd
import pytest
from dashboard_fixture import write_bucket, write_profile, write_state

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


def test_bucket_label_and_reconcile_record(tmp_path):
    write_state(tmp_path / "mix")
    (tmp_path / "mix" / "reconcile.jsonl").write_text(
        json.dumps({"ts": "2026-10-19T16:10:00", "status": "nothing pending"})
        + "\n"
        + json.dumps({"ts": "2026-10-20T16:10:00", "status": "reconciled, 1 still pending"})
        + "\n"
    )
    profile = write_profile(tmp_path / "mix.toml", "config.aws-paper-mix.toml", tmp_path / "mix")
    data = page_data(build([str(profile)], tmp_path / "out", now=NOW).read_text(encoding="utf-8"))
    mix = data["strategies"][0]["accounts"][0]
    assert mix["name"] == "Paper · mix 20%" and mix["bucket"] == "tqqq-blend/paper-mix"
    assert mix["capital"] == 20_000
    assert mix["reconcile"]["status"] == "reconciled, 1 still pending"


def test_unreadable_reconcile_log_is_an_error_not_silence(tmp_path):
    write_state(tmp_path / "paper")
    (tmp_path / "paper" / "reconcile.jsonl").write_text("{broken\n")
    profile = write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", tmp_path / "paper")
    data = page_data(build([str(profile)], tmp_path / "out", now=NOW).read_text(encoding="utf-8"))
    assert data["strategies"][0]["accounts"][0]["reconcile"]["status"].startswith("error")


@pytest.fixture
def mixed(tmp_path):
    write_state(tmp_path / "mix", capital=20_000.0)
    rot = write_bucket(tmp_path / "rot", "rotation", 30_000.0)
    write_bucket(tmp_path / "sec", "sectors", 25_000.0, start="2026-10-01", seed=12)
    profile = write_profile(tmp_path / "mix.toml", "config.aws-paper-mix.toml", tmp_path / "mix")
    dirs = [str(tmp_path / d) for d in ("mix", "rot", "sec", "nowhere")]
    html = build(
        [str(profile)], tmp_path / "out", now=NOW, buckets=dirs[1:3], portfolios=[["Mix portfolio", *dirs]]
    ).read_text(encoding="utf-8")
    return rot, page_data(html), html


def test_holdings_bucket_is_its_own_account(mixed):
    rot, data, _ = mixed
    sleeves = next(s for s in data["strategies"] if s["name"] == "sleeves")
    a = sleeves["accounts"][0]
    assert a["kind"] == "holdings" and a["name"] == "Paper · mix · rotation" and a["capital"] == 30_000
    assert a["metrics"]["equity"] == pytest.approx(rot[-1]["equity"])
    assert a["metrics"]["bench_return"] == pytest.approx(rot[-1]["benchmark_price"] / rot[0]["benchmark_price"] - 1)
    assert {h["symbol"] for h in a["holdings"]} == {"GLD", "SGOV", "Cash"}
    assert sum(h["weight"] for h in a["holdings"]) == pytest.approx(1.0)
    assert a["decision"]["why"]["summary"].startswith("Top 4") and a["trades_total"] == 2


def test_portfolio_sums_its_buckets_and_names_missing_ones(mixed):
    _, data, _ = mixed
    [p] = data["portfolios"]
    assert p["missing"] and p["missing"][0].endswith("nowhere")
    assert [m["capital"] for m in p["members"]] == [20_000, 30_000, 25_000]
    members = [a for s in data["strategies"] for a in s["accounts"]]

    def value_on(a, d):  # its latest equity on or before d, else its capital (it hadn't run yet)
        pts = [x["equity"] for x in a["series"] if x["date"] <= d]
        return pts[-1] if pts else a["capital"]

    for point in (p["series"][0], p["series"][len(p["series"]) // 2], p["series"][-1]):
        assert point["equity"] == pytest.approx(sum(value_on(a, point["date"]) for a in members))
    assert p["series"][0]["date"] < "2026-10-01"  # the sectors bucket counts at its capital before its first run
    assert p["metrics"]["return"] == pytest.approx(p["series"][-1]["equity"] / 75_000 - 1)
    assert sum(m["weight"] for m in p["members"]) == pytest.approx(1.0)


def test_a_forced_evening_run_does_not_replace_the_close_time_point():
    from tqqq_blend.dashboard import day_points

    runs = [
        {"ts": "2026-10-26T15:30:07", "equity": 100_870.0, "minutes_to_close": 29.9},
        {"ts": "2026-10-26T21:44:40", "equity": 100_802.0, "minutes_to_close": -344.0},
        {"ts": "2026-10-27T21:00:00", "equity": 101_000.0, "minutes_to_close": None},  # no scheduled run that day
    ]
    pts = day_points(runs)
    assert pts["2026-10-26"]["equity"] == 100_870.0 and pts["2026-10-27"]["equity"] == 101_000.0


def test_trades_count_only_orders_sent_and_paths_and_tokens_stay_off_the_page(tmp_path):
    write_state(tmp_path / "paper")
    with open(tmp_path / "paper" / "trades.csv", "a", encoding="utf-8") as f:
        f.write("2026-10-20T21:00:00,ibkr,TQQQ,BUY,5,MOC,70,0.5,DRY-RUN\n")
    with open(tmp_path / "paper" / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"ts": "2026-10-20T21:00:00", "status": "error: GET https://x/export?auth=s3cret&v=1 failed"})
            + "\n"
        )
    profile = write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", tmp_path / "paper")
    html = build([str(profile)], tmp_path / "out", now=NOW).read_text(encoding="utf-8")
    paper = page_data(html)["strategies"][0]["accounts"][0]
    assert paper["trades_sent"] == paper["trades_total"] - 1
    assert "s3cret" not in html and "auth=[redacted]" in html
    assert "state_dir" not in html and str(tmp_path).replace("\\", "/") not in html.replace("\\\\", "/")
