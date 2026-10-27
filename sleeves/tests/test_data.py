import io
from datetime import date

import pandas as pd
import pytest

from sleeves import data

OVERVIEW = "No.,Ticker,Company,Sector\n1,AAA,A Inc,Technology\n2,BBB,B Inc,Financial\n"
VALUATION = "No.,Ticker,P/E,P/B\n1,AAA,30,6.74\n2,BBB,9,-\n"
FINANCIAL = "No.,Ticker,Return on Equity,Operating Margin\n1,AAA,20.97%,24.09%\n2,BBB,11.00%,-3.50%\n"


def fake_urlopen(bodies):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def urlopen(req, timeout):
        url = req.full_url
        body = next(b for key, b in bodies.items() if key in url)
        return Resp(body.encode())

    return urlopen


def test_sp500_screen_merges_views_and_parses_percentages(monkeypatch):
    bodies = {"v=111": OVERVIEW, "v=121": VALUATION, "v=161": FINANCIAL}
    monkeypatch.setattr(data.urllib.request, "urlopen", fake_urlopen(bodies))
    s = data.sp500_screen("tok").set_index("Ticker")
    assert s.loc["AAA", "Sector"] == "Technology" and s.loc["AAA", "P/B"] == 6.74
    assert s.loc["AAA", "ROE"] == pytest.approx(0.2097) and s.loc["BBB", "Operating Margin"] == pytest.approx(-0.035)
    assert pd.isna(s.loc["BBB", "P/B"])  # "-" is missing, never zero


def test_expired_token_is_an_error(monkeypatch):
    monkeypatch.setattr(data.urllib.request, "urlopen", fake_urlopen({"export": "<html>Login</html>"}))
    with pytest.raises(RuntimeError, match="no rows"):
        data.sp500_screen("expired")
    with pytest.raises(RuntimeError, match="no price"):
        data.finviz_prices(["SPY"], "expired")


def test_finviz_prices_in_requested_order(monkeypatch):
    body = '"Ticker","Price"\n"SGOV",100.52\n"SPY",765.37\n'
    monkeypatch.setattr(data.urllib.request, "urlopen", fake_urlopen({"export": body}))
    assert data.finviz_prices(["SPY", "SGOV"], "tok") == {"SPY": 765.37, "SGOV": 100.52}


def test_finviz_token_must_be_set(monkeypatch):
    monkeypatch.delenv("FINVIZ_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="FINVIZ_TOKEN"):
        data.finviz_token("FINVIZ_TOKEN")


def frame(end):
    idx = pd.bdate_range(end=end, periods=30)
    return pd.DataFrame({"SPY": range(30), "GLD": range(30)}, index=idx, dtype=float)


def test_history_drops_today_and_requires_the_previous_close(monkeypatch):
    starts = []
    monkeypatch.setattr(data, "_download", lambda symbols, start: starts.append(start) or frame("2026-10-30"))
    h = data.history(["SPY", "GLD"], date(2026, 10, 30))
    assert starts == ["2024-10-30"]  # a bare date: yfinance rejects "2024-10-30 00:00:00"
    assert h.index[-1] == pd.Timestamp("2026-10-29")  # today's partial bar is replaced by the live price
    stale = frame("2026-10-29")
    stale.loc[pd.Timestamp("2026-10-29"), "GLD"] = float("nan")
    monkeypatch.setattr(data, "_download", lambda symbols, start: stale)
    with pytest.raises(RuntimeError, match=r"stale history: \['GLD'\]"):
        data.history(["SPY", "GLD"], date(2026, 10, 30))


def test_with_live_appends_today():
    out = data.with_live(frame("2026-10-29"), {"SPY": 99.0, "GLD": 98.0}, date(2026, 10, 30))
    assert out.index[-1] == pd.Timestamp("2026-10-30") and out.iloc[-1].tolist() == [99.0, 98.0]
