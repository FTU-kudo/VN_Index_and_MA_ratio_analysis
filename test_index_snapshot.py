"""Test offline cho index_transaction (cấu trúc JSON theo ảnh chụp VNREAL) và kho lịch sử chỉ số."""
import os, sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from index_store import IndexStore, group_index_code
from yuanta_client import VN_TZ, YuantaClient

def _payload(day: datetime, close=3276.85):
    t0 = int(day.replace(hour=9, minute=0, second=55).timestamp())
    tx = [{"Index": 3229.43 + i * 0.1, "Volume": 0, "UnixTime": t0 + 60 * i} for i in range(346)]
    tx.append({"Index": close, "Volume": 0, "UnixTime": t0 + 60 * 346})
    return {"success": True, "response": {
        "IndexCode": "VNREAL", "IndexName": "VNREAL", "RefIndex": 3233.6, "CurrIndex": close, "Ceiling": 0,
        "Up": 13, "NoChange": 4, "Down": 27, "Floor": 0, "TotalVolTrade": 70163985, "TotalValTrade": 1684126779240,
        "TotalRecords": 347, "CurrTradingSessionID": "99", "ListTransaction": tx}}

def _client(payload):
    c = YuantaClient(rate_limit_delay=0)
    r = MagicMock(); r.raise_for_status.return_value = None; r.json.return_value = payload
    c.session = MagicMock(); c.session.get.return_value = r
    return c

def test_snapshot_parsed_and_final_for_past_day():
    day = datetime.now(VN_TZ) - timedelta(days=3)
    s = _client(_payload(day)).get_index_snapshot("vnreal")
    assert s["code"] == "VNREAL" and s["close"] == 3276.85 and s["ref"] == 3233.6
    assert s["change"] == 43.25 and s["change_pct"] == 1.34
    assert (s["up"], s["no_change"], s["down"]) == (13, 4, 27)
    assert s["date"] == day.strftime("%Y-%m-%d") and s["last_tx_time"] == "14:46:55" and s["is_final"] is True

def test_intraday_today_is_provisional_and_not_persisted(tmp_path):
    now = datetime.now(VN_TZ)
    if now.hour >= 15:  # test chỉ có nghĩa khi chạy trước 15:00
        return
    store = IndexStore(str(tmp_path / "h.csv"))
    store.update_from_api(_client(_payload(now)), ["VNREAL"])
    assert store.snapshots["VNREAL"]["is_final"] is False
    assert store.df.empty and not os.path.exists(store.path)
    assert len(store.series("VNREAL")) == 1  # nhưng vẫn hiện điểm tạm thời trên biểu đồ

def test_store_upsert_idempotent(tmp_path):
    day = datetime.now(VN_TZ) - timedelta(days=3)
    path = str(tmp_path / "h.csv")
    for _ in range(2):
        st = IndexStore(path); st.update_from_api(_client(_payload(day)), ["VNREAL"])
    assert len(IndexStore(path).df) == 1

def test_group_codes():
    assert group_index_code("HOSE") == "VNINDEX" and group_index_code("vnreal") == "VNREAL"

def test_bad_payload_returns_none():
    assert _client({"success": True, "response": []}).get_index_snapshot("VNINDEX") is None
