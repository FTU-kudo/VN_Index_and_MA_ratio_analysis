"""Test offline cho YuantaClient - dùng đúng cấu trúc JSON thực tế của Yuanta (success/response, PascalCase)."""
import json
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from yuanta_client import YuantaClient, _extract_records

LIST_PAYLOAD = {"success": True, "response": [
    {"StockCode": "AAA", "Exchange": "HSX", "SymbolType": "ST", "RefP": 6820},
    {"StockCode": "AAM", "Exchange": "HSX", "SymbolType": "ST", "RefP": 7400},
    {"StockCode": "E1VFVN30", "Exchange": "HSX", "SymbolType": "ETF"},
    {"StockCode": "CACB2601", "Exchange": "HSX", "SymbolType": "CW"},
    {"StockCode": "ABC", "Exchange": "HNX", "SymbolType": "ST"},
]}
PRICE_PAYLOAD = {"success": True, "response": [
    {"TradingDate": "2026-11-16", "PerChange": 0.49, "Close": 20450, "Open": 20350, "Highest": 20450,
     "Lowest": 20300, "Average": 20369, "Volume": 6816000, "Value": 138922610000,
     "ForeignVolumeBuy": 1945600, "ForeignVolumeSell": 3785000, "PutThroughVolume": 56900, "PutThroughValue": 1159735800},
    {"TradingDate": "2026-11-13", "PerChange": -0.25, "Close": 20350, "Open": 20350, "Highest": 20500,
     "Lowest": 20300, "Average": 20360, "Volume": 10119400, "Value": 206020010000,
     "ForeignVolumeBuy": 4286600, "ForeignVolumeSell": 4153900, "PutThroughVolume": 9650000, "PutThroughValue": 205593850000},
]}


def _client_with(payload, status=200, text=None):
    c = YuantaClient(rate_limit_delay=0)
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status.return_value = None
    resp.json.side_effect = (lambda: payload) if text is None else (lambda: (_ for _ in ()).throw(ValueError("x")))
    resp.text = text or json.dumps(payload)
    resp.content = resp.text.encode()
    resp.url = "https://x"
    resp.headers = {"Content-Type": "text/html"}
    c.session = MagicMock()
    c.session.get.return_value = resp
    return c


def test_extract_response_key():
    assert len(_extract_records(PRICE_PAYLOAD)) == 2
    assert _extract_records({"success": True, "response": []}) == []
    assert _extract_records("garbage") == []


def test_symbols_filtered_to_hose_stocks():
    c = _client_with(LIST_PAYLOAD)
    assert c.get_index_symbols("HOSE") == ["AAA", "AAM"]


def test_price_history_normalised():
    c = _client_with(PRICE_PAYLOAD)
    df = c.get_price_history("ACB", "2026-11-01", "2026-11-16")
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "symbol"]
    assert len(df) == 2 and df["close"].iloc[-1] == 20450 and df["high"].iloc[-1] == 20450
    assert str(df["time"].iloc[-1]) == "2026-11-16"


def test_non_json_body_is_reported_not_crash(capsys):
    c = _client_with(None, text="<html>blocked</html>")
    assert c.get_index_symbols("HOSE") == []
    out = capsys.readouterr().out
    assert "KHÔNG phải JSON" in out and c.failed_requests >= 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
