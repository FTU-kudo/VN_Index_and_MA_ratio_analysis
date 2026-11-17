"""Test trang chống bot "Attention Required" (jskey) của Yuanta - dữ liệu mẫu lấy từ báo cáo chẩn đoán thật."""
import os, sys
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yuanta_client import YuantaClient, challenge_url

HREF = ("https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/stock/list_stock_info"
        "?exchange=HSX&jskey=B0C9rGJdyLmkSISF5PDt3AZVjC%2F99ZOPJArAJqkT4ks55GMN8XAScQM%3D")
CHALLENGE = ('<html><head><title>Attention Required</title></head><body><script>window.location.href="'
             + HREF + '";</script></body></html> ')
DATA = {"success": True, "response": [{"StockCode": "AAA", "Exchange": "HSX", "SymbolType": "ST"}]}


def _resp(text=None, payload=None):
    r = MagicMock(); r.status_code = 200; r.raise_for_status.return_value = None
    r.text = text if text is not None else "{}"; r.content = r.text.encode(); r.url = "https://x"; r.headers = {}
    if payload is not None: r.json.return_value = payload
    else: r.json.side_effect = ValueError("not json")
    return r


def test_challenge_url_extracts_exact_href():
    assert challenge_url(CHALLENGE) == HREF


def test_challenge_url_ignores_normal_and_foreign_pages():
    assert challenge_url('{"success": true, "response": []}') is None
    assert challenge_url("x" * 5000) is None
    assert challenge_url('<script>window.location.href="https://evil.example.com/?jskey=abc";</script>') is None


def test_client_follows_challenge_then_parses_json():
    c = YuantaClient(rate_limit_delay=0)
    c.session = MagicMock(); c.session.get.side_effect = [_resp(CHALLENGE), _resp(payload=DATA)]
    assert c.get_index_symbols("HOSE") == ["AAA"]
    assert c.session.get.call_args_list[1].args[0] == HREF      # gọi đúng URL có jskey, không bị mã hoá lại
    assert c.challenges_followed == 1


def test_endless_challenge_stops_after_two_hops_without_crash():
    c = YuantaClient(rate_limit_delay=0)
    c.session = MagicMock(); c.session.get.return_value = _resp(CHALLENGE)
    assert c.get_index_symbols("HOSE") == []
    assert c.challenges_followed >= 2 and c.failed_requests >= 1
