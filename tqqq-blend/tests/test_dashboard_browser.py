"""The dashboard in a real browser (Chromium via Playwright): it renders without script errors, shows
both accounts, draws the charts, answers hover and keyboard focus, fits a phone, and supports dark mode.

Needs `uv run playwright install chromium` once. Locally the tests skip if the browser is missing;
CI sets DASHBOARD_BROWSER_REQUIRED=1 so they fail instead of skipping."""

import os

import pandas as pd
import pytest
from dashboard_fixture import write_profile, write_state

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


def open_page(browser, url, **ctx):
    page = browser.new_page(**ctx)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(url)
    return page, errors


def test_renders_both_accounts_and_charts(browser, url):
    page, errors = open_page(browser, url, viewport={"width": 1300, "height": 900})
    assert errors == []
    cards = page.locator(".account")
    assert cards.count() == 2
    paper, live = cards.nth(0), cards.nth(1)
    assert paper.locator(".hero .value").inner_text().startswith("$")
    assert paper.locator(".chart svg").count() == 3
    assert paper.locator(".chart svg path").count() > 3
    assert "with no run" in paper.locator(".alerts").inner_text()  # the fixture skips a trading day
    assert "No runs recorded yet" in live.inner_text()
    assert "orders off" in live.locator(".badge").inner_text()
    assert "DU1234567" not in page.content()
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
    assert errors == []
    page.close()


def test_dark_mode_and_theme_toggle(browser, url):
    page, errors = open_page(browser, url, color_scheme="dark", viewport={"width": 1300, "height": 900})
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(13, 13, 13)"
    page.locator("#theme").click()  # auto -> light
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(249, 249, 247)"
    assert errors == []
    page.close()
