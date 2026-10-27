"""The dashboard in a real browser (Chromium via Playwright): it renders without script errors, shows an
overview of every account and a detail view for each one with runs, keeps bars inside the plot, scopes the
charts by range, warns when the page is stale, answers hover and keyboard focus, fits a phone, and supports
dark mode.

Needs `uv run playwright install chromium` once. Locally the tests skip if the browser is missing;
CI sets DASHBOARD_BROWSER_REQUIRED=1 so they fail instead of skipping."""

import json
import os

import pandas as pd
import pytest
from dashboard_fixture import write_bucket, write_profile, write_state

from tqqq_blend.dashboard import build

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - missing browser binary
            if os.environ.get("DASHBOARD_BROWSER_REQUIRED"):
                raise
            pytest.skip(f"Chromium not installed: {e}")
        yield b
        b.close()


@pytest.fixture(scope="module")
def url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dash")
    write_state(tmp / "paper")
    (tmp / "live").mkdir()
    profiles = [
        write_profile(tmp / "paper.toml", "config.ibkr-paper.toml", tmp / "paper"),
        write_profile(tmp / "live.toml", "config.ibkr-live.toml", tmp / "live"),
    ]
    now = pd.Timestamp("2026-10-20 17:00", tz="America/New_York")
    return build([str(p) for p in profiles], tmp / "out", now=now).as_uri()


def open_page(browser, url, now=None, **ctx):
    page = browser.new_page(**ctx)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    if now is not None:
        page.clock.set_fixed_time(pd.Timestamp(now, tz="America/New_York").to_pydatetime())
    page.goto(url)
    return page, errors


def test_overview_and_detail(browser, url):
    page, errors = open_page(browser, url, now="2026-10-20 18:00", viewport={"width": 1300, "height": 900})
    assert errors == []
    overview = page.locator(".ov")
    assert overview.count() == 2  # every account, with or without runs
    assert overview.nth(0).locator(".ov-value").inner_text().startswith("$")
    assert "No runs yet" in overview.nth(1).inner_text()
    assert "dry run" in overview.nth(1).locator(".badge").inner_text()
    detail = page.locator(".account")
    assert detail.count() == 1  # an account without runs gets no empty detail section
    paper = detail.first
    assert paper.locator(".kpi.hero .v").inner_text().startswith("$")
    assert paper.locator(".chart svg").count() == 3
    assert paper.locator(".chart svg path").count() > 3
    assert "with no run" in paper.locator(".alerts").inner_text()  # the fixture skips a trading day
    assert "Target:" in paper.locator(".decision").inner_text()
    assert "DU1234567" not in page.content()
    page.close()


def test_exposure_bars_stay_inside_the_plot(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    svg = page.locator(".account .chart svg").nth(2)  # growth, drawdown, exposure
    bounds = svg.evaluate(
        """s => {
          const lines = [...s.querySelectorAll('.grid line')];
          const left = +lines[0].getAttribute('x1'), right = +lines[0].getAttribute('x2');
          const bars = [...s.querySelectorAll('path')].filter(p => (p.getAttribute('fill') || '').includes('--long')
            || (p.getAttribute('fill') || '').includes('--short'));
          return { left, right, n: bars.length, min: Math.min(...bars.map(b => b.getBBox().x)),
                   max: Math.max(...bars.map(b => b.getBBox().x + b.getBBox().width)) };
        }"""
    )
    assert bounds["n"] > 10
    assert bounds["min"] >= bounds["left"] and bounds["max"] <= bounds["right"]
    assert errors == []
    page.close()


def test_range_buttons_scope_the_charts(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    growth = page.locator(".account .chart svg").first
    # x-axis date labels are the last five texts in the axis group
    dates = lambda: growth.evaluate("s => [...s.querySelectorAll('.axis text')].slice(-5).map(t => t.textContent)")  # noqa: E731
    before = dates()
    assert before[0] == "Aug 3"
    page.get_by_role("button", name="1M").click()
    assert page.get_by_role("button", name="1M").get_attribute("aria-pressed") == "true"
    after = dates()
    assert after[0] != "Aug 3" and after[-1] == "Oct 20"  # starts later, still ends on the latest day
    assert errors == []
    page.close()


def test_stale_page_banner(browser, url):
    fresh, errors = open_page(browser, url, now="2026-10-20 18:00", viewport={"width": 1300, "height": 900})
    assert fresh.locator("#stale .banner").count() == 0
    fresh.close()
    stale, errors2 = open_page(browser, url, now="2026-10-22 18:00", viewport={"width": 1300, "height": 900})
    assert "last rebuilt on Oct 20, 2026" in stale.locator("#stale .banner").inner_text()
    assert errors == [] and errors2 == []
    stale.close()


def test_activity_tabs_and_show_all(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    act = page.locator(".activity")
    assert act.locator("tbody tr").count() == 10
    act.get_by_role("button", name="Show all 30").click()
    assert act.locator("tbody tr").count() == 30
    act.get_by_role("tab", name="Trades (31)").click()
    assert act.get_by_role("tab", name="Trades (31)").get_attribute("aria-selected") == "true"
    assert "Side" in act.locator("thead").inner_text()
    act.get_by_role("tab", name="Missed days (1)").click()
    assert "Sep 22, 2026" in act.inner_text()
    assert errors == []
    page.close()


def test_hover_and_keyboard_show_the_tooltip(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    chart = page.locator(".account").first.locator(".chart").first
    chart.scroll_into_view_if_needed()  # the mouse only reaches what is on screen
    box = chart.locator("svg").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    tip = chart.locator(".tip")
    assert tip.is_visible()
    assert tip.locator(".row").count() == 3  # every series at that date, not just the nearest line
    page.mouse.move(0, 0)
    assert not tip.is_visible()
    chart.locator("rect[tabindex='0']").focus()
    assert tip.is_visible()
    assert errors == []
    page.close()


def test_fits_a_phone_without_sideways_scroll(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 375, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth") <= 375
    svg_width = page.locator(".account .chart svg").first.bounding_box()["width"]
    assert svg_width <= 375
    # charts draw at their real width, so axis text stays readable instead of scaling down
    assert page.locator(".account .chart svg").first.get_attribute("viewBox").split()[2] == str(round(svg_width))
    table = page.locator(".activity .table-wrap").first
    assert table.evaluate("e => e.scrollWidth <= e.clientWidth")  # the run table fits without scrolling
    assert errors == []
    page.close()


def test_dark_mode_and_theme_toggle(browser, url):
    page, errors = open_page(browser, url, color_scheme="dark", viewport={"width": 1300, "height": 900})
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(13, 13, 13)"
    page.locator("#theme").click()  # auto -> light
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(246, 245, 241)"
    assert errors == []
    page.close()


def test_values_that_round_to_zero_have_no_sign(browser, url):
    # The fixture's 2026-10-14 run has net -0.003 or so: it must read 0.00, never "−0.00".
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    act = page.locator(".activity")
    act.get_by_role("button", name="Show all 30").click()
    assert "−0.00" not in act.inner_text() and "−0.0%" not in page.locator("main").inner_text()
    assert errors == []
    page.close()


def test_decision_explains_the_rules_and_the_orders(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    decision = page.locator(".decision")
    text = decision.inner_text()
    assert "WHAT IT DID" in text.upper() and "rebalance band" in text  # why it did or didn't trade
    assert decision.locator(".rule-title").count() == 3  # one rule per sleeve
    assert "HOW IT ADDS UP" in text.upper()
    act = page.locator(".activity")
    act.get_by_role("tab", name="Trades (31)").click()
    assert "Trend: " in act.inner_text()  # a recent trade's reasons
    assert "Recorded before rule-by-rule reasons" in act.inner_text()  # an older one says so
    assert errors == []
    page.close()


def test_order_reason_sits_under_its_order(browser, tmp_path):
    # The fixture's run on 2026-10-16 placed an order, so a page built that evening has one to explain.
    write_state(tmp_path / "paper", end="2026-10-16")
    profile = write_profile(tmp_path / "paper.toml", "config.ibkr-paper.toml", tmp_path / "paper")
    page_url = build([str(profile)], tmp_path / "out", now=pd.Timestamp("2026-10-16 17:00", tz="America/New_York"))
    page, errors = open_page(browser, page_url.as_uri(), viewport={"width": 1300, "height": 900})
    order = page.locator(".decision li.order").first
    assert "rebalance band" in order.locator(".order-why").inner_text()
    line = order.locator(".order-line").bounding_box()
    why = order.locator(".order-why").bounding_box()
    assert why["y"] >= line["y"] + line["height"] - 1  # below its order line, not squeezed beside it
    assert errors == []
    page.close()


@pytest.mark.parametrize(
    ("status", "level"),
    [
        ("error: IB not connected", "Failing"),
        ("reconciled, 1 still pending", "Needs a look"),
        ("reconciled", "Healthy"),
    ],
)
def test_reconcile_status_shows_as_an_alert(browser, tmp_path, status, level):
    write_state(tmp_path / "paper", end="2026-10-20", missed=None)
    (tmp_path / "paper" / "reconcile.jsonl").write_text(json.dumps({"ts": "2026-10-20T16:10:00", "status": status}))
    profile = write_profile(tmp_path / "p.toml", "config.ibkr-paper.toml", tmp_path / "paper")
    url = build([str(profile)], tmp_path / "out", now=pd.Timestamp("2026-10-20 17:00", tz="America/New_York")).as_uri()
    page, errors = open_page(browser, url, now="2026-10-20 18:00", viewport={"width": 1300, "height": 900})
    assert errors == []
    sec = page.locator(".account").first
    assert level in sec.locator(".acct-head").inner_text()
    alerts = sec.locator(".alerts")
    if level == "Healthy":
        assert alerts.count() == 0 or "reconcile" not in alerts.inner_text()
    else:
        assert "reconcile" in alerts.inner_text() and status in alerts.inner_text()
    page.close()


@pytest.fixture(scope="module")
def mixed_url(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mixed")
    write_state(tmp / "mix", capital=20_000.0)
    write_bucket(tmp / "rot", "rotation", 30_000.0)
    write_bucket(tmp / "sec", "sectors", 25_000.0, start="2026-10-01", seed=12)
    profile = write_profile(tmp / "mix.toml", "config.aws-paper-mix.toml", tmp / "mix")
    dirs = [str(tmp / d) for d in ("mix", "rot", "sec")]
    now = pd.Timestamp("2026-10-20 17:00", tz="America/New_York")
    return build([str(profile)], tmp / "out", now=now, buckets=dirs[1:], portfolios=[["Mix portfolio", *dirs]]).as_uri()


def test_portfolio_and_holdings_buckets_render(browser, mixed_url):
    page, errors = open_page(browser, mixed_url, now="2026-10-20 18:00", viewport={"width": 1300, "height": 900})
    assert errors == []
    folio = page.locator(".portfolio")
    assert folio.count() == 1 and "Mix portfolio" in folio.inner_text()
    assert folio.locator(".kpi.hero .v").inner_text().startswith("$")
    rows = folio.locator("table tbody tr")
    assert rows.count() == 3 and "Paper · mix 20%" in rows.nth(0).inner_text()
    assert folio.locator(".chart svg path").count() > 2
    buckets = page.locator('.account[data-kind="holdings"]')
    assert buckets.count() == 2
    rot = buckets.first
    assert "GLD" in rot.locator('[aria-label="Holdings"]').inner_text()
    decision = rot.locator(".decision").inner_text()
    assert "Top 4 eligible" in decision and "not eligible (below trend)" in decision and "market on close" in decision
    rot.locator('[aria-label="Holdings"] table').wait_for()
    page.locator(".portfolio table a").nth(1).click()  # the allocation table links to the bucket
    assert page.evaluate("location.hash") == "#" + rot.get_attribute("id")
    assert page.locator(".ov").count() == 3
    page.close()


def test_portfolio_fits_a_phone(browser, mixed_url):
    page, errors = open_page(browser, mixed_url, viewport={"width": 390, "height": 844})
    assert errors == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.close()


def test_portfolio_in_dark_mode(browser, mixed_url):
    page, errors = open_page(browser, mixed_url, color_scheme="dark", viewport={"width": 1300, "height": 900})
    assert errors == []
    bg = page.evaluate("getComputedStyle(document.querySelector('.portfolio .kpi')).backgroundColor")
    assert bg not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)")
    page.close()
