"""Test offline cho scripts/fetch_index_ohlcv.py: chọn 'winner', ghi báo cáo, mã thoát."""
import json, os, sys
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fetch_index_ohlcv as f

BARS = {"s": "ok", "bars": [{"t": 1262563200 + 86400 * i, "o": 1, "h": 2, "l": 0.5, "c": 500 + i, "v": 10} for i in range(40)]}

def _resp(payload=None, status=200, text=None):
    r = MagicMock(); r.status_code = status; r.headers = {"Content-Type": "application/json"}; r.history = []
    r.content = b"x"; r.text = text if text is not None else json.dumps(payload)
    if payload is None: r.json.side_effect = ValueError("no json")
    else: r.json.return_value = payload
    return r

def test_probe_records_not_json_and_good():
    f.requests.get = MagicMock(return_value=_resp(None, 403, "<html>Access denied</html>"))
    p = f.probe("x", "https://u"); assert p["status"] == 403 and p["json_s"] == "NOT_JSON" and "denied" in p["body_head"] and not f.is_good(p)
    f.requests.get = MagicMock(return_value=_resp(BARS))
    p = f.probe("x", "https://u"); assert f.is_good(p) and p["n_bars"] == 40

def test_probe_records_network_error():
    f.requests.get = MagicMock(side_effect=ConnectionError("blocked"))
    p = f.probe("x", "https://u"); assert "ConnectionError" in p["error"] and not f.is_good(p)

def test_winner_is_first_working_profile():
    def fake_get(url, params=None, headers=None, timeout=None):
        ua = (headers or {}).get("User-Agent", "python-requests")
        if "ysradar.yuanta.com.vn/v2" in url and ua.startswith("curl"):   # chỉ kiểu curl_like được chấp nhận
            return _resp(BARS)
        return _resp(None, 403, "blocked")
    f.requests.get = fake_get
    _, winner = f.run_probes()
    assert winner == {"host": "ysradar", "url": f.HISTORY_URLS["ysradar"], "profile": "curl_like"}

def test_no_winner_gives_exit_2_and_report(tmp_path, monkeypatch):
    f.requests.get = MagicMock(return_value=_resp(None, 403, "blocked"))
    rep = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["x", "--report", str(rep)])
    assert f.main() == 2
    d = json.loads(rep.read_text()); assert d["status"] == "no_working_request" and len(d["probes"]) >= 10
