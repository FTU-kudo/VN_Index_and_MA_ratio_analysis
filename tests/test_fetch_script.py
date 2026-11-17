"""Test offline cho scripts/fetch_index_ohlcv.py: chọn 'winner', ghi báo cáo, mã thoát."""
import json, os, sys
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fetch_index_ohlcv as f
import pytest
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def _restore_requests():
    """Các test vá requests.get / requests.Session toàn cục -> luôn khôi phục để không làm hỏng test khác."""
    get, session = f.requests.get, f.requests.Session
    yield
    f.requests.get, f.requests.Session = get, session


def _patch_http(fake_get):
    """probe() dùng Session().get; egress_info() dùng requests.get -> vá cả hai."""
    f.requests.get = fake_get
    f.requests.Session = lambda: SimpleNamespace(get=fake_get)

BARS = {"s": "ok", "bars": [{"t": 1262563200 + 86400 * i, "o": 1, "h": 2, "l": 0.5, "c": 500 + i, "v": 10} for i in range(40)]}

def _resp(payload=None, status=200, text=None):
    r = MagicMock(); r.status_code = status; r.headers = {"Content-Type": "application/json"}; r.history = []
    r.content = b"x"; r.text = text if text is not None else json.dumps(payload)
    if payload is None: r.json.side_effect = ValueError("no json")
    else: r.json.return_value = payload
    return r

def test_probe_records_not_json_and_good():
    _patch_http(MagicMock(return_value=_resp(None, 403, "<html>Access denied</html>")))
    p = f.probe("x", "https://u"); assert p["status"] == 403 and p["json_s"] == "NOT_JSON" and "denied" in p["body_head"] and not f.is_good(p)
    _patch_http(MagicMock(return_value=_resp(BARS)))
    p = f.probe("x", "https://u"); assert f.is_good(p) and p["n_bars"] == 40

def test_probe_records_network_error():
    _patch_http(MagicMock(side_effect=ConnectionError("blocked")))
    p = f.probe("x", "https://u"); assert "ConnectionError" in p["error"] and not f.is_good(p)

def test_winner_is_first_working_profile():
    def fake_get(url, params=None, headers=None, timeout=None):
        ua = (headers or {}).get("User-Agent", "python-requests")
        if "ysradar.yuanta.com.vn/v2" in url and ua.startswith("curl"):   # chỉ kiểu curl_like được chấp nhận
            return _resp(BARS)
        return _resp(None, 403, "blocked")
    _patch_http(fake_get)
    _, winner = f.run_probes()
    assert winner == {"host": "ysradar", "url": f.HISTORY_URLS["ysradar"], "profile": "curl_like"}
    assert {"address_bar", "referer_only", "origin_only"} <= set(f.PROFILES)

def test_no_winner_gives_exit_2_and_report(tmp_path, monkeypatch):
    _patch_http(MagicMock(return_value=_resp(None, 403, "blocked")))
    rep = tmp_path / "r.json"
    monkeypatch.setattr(sys, "argv", ["x", "--report", str(rep)])
    assert f.main() == 2
    d = json.loads(rep.read_text()); assert d["status"] == "no_working_request" and len(d["probes"]) >= 10


def test_build_pages_refuses_to_wipe_page_without_data(tmp_path, monkeypatch):
    """Lỗi từng gặp: rebuild khi thiếu breadth_groups.json ghi BREADTH_DATA = null đè lên trang tốt."""
    import importlib, subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    (tmp_path / "output").mkdir(); (tmp_path / "docs").mkdir()
    (tmp_path / "output" / "market_breadth_chart.html").write_text("<html><head></head><body>x</body></html>")
    (tmp_path / "docs" / "index.html").write_text("TRANG TỐT")
    r = subprocess.run([sys.executable, os.path.join(root, "build_pages.py")], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1 and "KHÔNG ghi đè" in r.stdout
    assert (tmp_path / "docs" / "index.html").read_text() == "TRANG TỐT"


CHALLENGE = ('<html><head><title>Attention Required</title></head><body><script>window.location.href='
             '"https://ysradar.yuanta.com.vn/v2/tradingview/history?symbol=VNINDEX&jskey=B0C9rGJdyLmkS%2F99ZOP%3D";</script></body></html>')

def test_probe_follows_js_challenge_like_a_browser():
    calls = []
    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return _resp(None, 200, CHALLENGE) if "jskey" not in url else _resp(BARS)
    _patch_http(fake_get)
    p = f.probe("x", "https://ysradar.yuanta.com.vn/v2/tradingview/history", {"symbol": "VNINDEX"})
    assert f.is_good(p) and p["challenge_followed"] and "jskey=B0C9rGJdyLmkS%2F99ZOP%3D" in calls[-1]
