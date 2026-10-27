import sys

import pytest

from tqqq_blend import __main__ as cli
from tqqq_blend import runner


@pytest.mark.parametrize(
    ("status", "code"), [("reconciled", None), ("nothing pending", None), ("error: no gateway", 1)]
)
def test_reconcile_exit_status(monkeypatch, tmp_path, status, code):
    """deploy/server/reconcile.sh relies on a failed reconcile exiting non-zero."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["tqqq_blend", "reconcile"])
    monkeypatch.setattr(runner, "reconcile", lambda cfg: {"status": status})
    if code is None:
        cli.main()
    else:
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code == code
