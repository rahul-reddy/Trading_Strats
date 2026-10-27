from tqqq_blend.brokers import Order
from tqqq_blend.brokers.paper import PaperBroker
from tqqq_blend.config import Config


def test_paper_fills_are_tagged_and_persist(tmp_path, monkeypatch):
    cfg = Config()
    cfg.state_dir, cfg.risk.capital = str(tmp_path), 1000.0
    monkeypatch.setattr(PaperBroker, "last_price", lambda self, s: 10.0)
    b = PaperBroker(cfg)
    b.connect()
    b.place(Order("TQQQ", "BUY", 5, "MOC", "a"))
    b.place(Order("TQQQ", "SELL", 2, "MOC", "b"))
    b.disconnect()
    b2 = PaperBroker(cfg)
    b2.connect()
    assert b2.positions() == {"TQQQ": 3} and b2.net_liquidation() == 1000.0
    [f] = b2.fills("a")
    assert (f.exec_id, f.symbol, f.shares, f.price) == ("paper-1", "TQQQ", 5, 10.0)
    assert [x.shares for x in b2.fills("b")] == [-2]
