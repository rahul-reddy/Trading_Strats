import io

import pytest

from tqqq_blend import quotes
from tqqq_blend.config import Config

CSV = '"Ticker","Price"\n"QQQ",718.61\n"SQQQ",37.52\n"TQQQ",70.71\n'


class FakeBroker:
    name = "ibkr"

    def last_price(self, symbol):
        return {"QQQ": 700.0, "TQQQ": 70.0, "SQQQ": 37.0}[symbol]


def fake_urlopen(body):
    class Resp(io.BytesIO):
        def __enter__(self):
            return self
        def __exit__(self, *a):
            self.close()
    return lambda req, timeout: Resp(body.encode())


def finviz_cfg():
    cfg = Config()
    cfg.broker.quote_source = "finviz"
    return cfg


def test_finviz_prices_parses_export_in_requested_order(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen", fake_urlopen(CSV))
    assert quotes.finviz_prices(["QQQ", "TQQQ", "SQQQ"], "tok") == {"QQQ": 718.61, "TQQQ": 70.71, "SQQQ": 37.52}


def test_finviz_login_page_is_an_error(monkeypatch):
    monkeypatch.setattr(quotes.urllib.request, "urlopen", fake_urlopen("<html>Login</html>"))
    with pytest.raises(RuntimeError, match="no price"):
        quotes.finviz_prices(["QQQ"], "expired")


def test_get_prices_uses_finviz_when_token_set(monkeypatch):
    monkeypatch.setenv("FINVIZ_TOKEN", "tok")
    monkeypatch.setattr(quotes.urllib.request, "urlopen", fake_urlopen(CSV))
    prices, source = quotes.get_prices(finviz_cfg(), FakeBroker(), ["QQQ", "TQQQ"])
    assert source == "finviz" and prices == {"QQQ": 718.61, "TQQQ": 70.71}


def test_get_prices_falls_back_to_broker_without_token(monkeypatch):
    monkeypatch.delenv("FINVIZ_TOKEN", raising=False)
    prices, source = quotes.get_prices(finviz_cfg(), FakeBroker(), ["QQQ"])
    assert source == "ibkr" and prices == {"QQQ": 700.0}


def test_live_signal_bars_patches_todays_bar_with_int_volume(monkeypatch):
    import pandas as pd
    from tqqq_blend import data
    today = pd.Timestamp.now(tz="America/New_York").normalize()
    idx = pd.DatetimeIndex([today - pd.Timedelta(days=1), today])
    raw = pd.DataFrame({"Open": [700.0, 701.0], "High": [705.0, 702.0], "Low": [695.0, 699.0],
                        "Close": [701.0, 700.0], "Volume": [40_000_000, 15_654_631]}, index=idx)
    monkeypatch.setattr(data.yf, "Ticker", lambda s: type("T", (), {"history": lambda self, **k: raw})())
    df = data.live_signal_bars("QQQ", 710.0, 1.15)
    last = df.iloc[-1]
    assert (last["close"], last["high"]) == (710.0, 710.0)
    assert last["volume"] == pytest.approx(15_654_631 * 1.15)
