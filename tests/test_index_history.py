"""Test offline cho endpoint TradingView (dữ liệu mẫu lấy đúng từ ảnh VNINDEX/VNHEAL của Yuanta) và IndexStore."""
import os, sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from index_store import IndexStore as _IS


def IndexStore(path, ohlcv=None):
    return _IS(path, ohlcv or path.replace('.csv', '_ohlcv.csv'))
from yuanta_client import VN_TZ, YuantaClient

VNINDEX = {"s": "ok", "bars": [
    {"t": 1262563200, "o": 501.74, "h": 517.05, "l": 501.74, "c": 517.05, "v": 44914820},
    {"t": 1262649600, "o": 529.23, "h": 539.39, "l": 529.23, "c": 532.53, "v": 70051390},
    {"t": 1262736000, "o": 529.47, "h": 538.84, "l": 529.47, "c": 534.46, "v": 67363510},
    {"t": 1263168000, "o": 522.78, "h": 524.06, "l": 515.06, "c": 515.06, "v": 44775500}]}
VNHEAL = {"s": "ok", "bars": [
    {"t": 1453680000, "o": 533.49, "h": 540.06, "l": 532.45, "c": 536.52002, "v": 146010},
    {"t": 1453766400, "o": 536.52, "h": 536.89, "l": 521.04, "c": 532.94, "v": 180000}]}

def _client(payload):
    c = YuantaClient(rate_limit_delay=0)
    r = MagicMock(); r.raise_for_status.return_value = None; r.json.return_value = payload
    c.session = MagicMock(); c.session.get.return_value = r
    return c

def test_history_dates_are_vn_trading_days():
    df = _client(VNINDEX).get_index_history("vnindex")
    assert str(df["time"].iloc[0]) == "2010-01-04" and str(df["time"].iloc[-1]) == "2010-01-11"
    assert df["close"].tolist() == [517.05, 532.53, 534.46, 515.06] and (df["code"] == "VNINDEX").all()

def test_float_noise_rounded_and_first_date_vnheal():
    df = _client(VNHEAL).get_index_history("VNHEAL")
    assert str(df["time"].iloc[0]) == "2016-01-25" and df["close"].iloc[0] == 536.52

def test_request_params_match_yuanta_url():
    c = _client(VNINDEX); c.get_index_history("VNINDEX", "2010-01-01")
    params = c.session.get.call_args.kwargs["params"]
    assert params["symbol"] == "VNINDEX" and params["resolution"] == "1D" and params["limit"] == 100000
    assert params["from"] == 1262304000  # 2010-01-01 00:00 UTC, đúng như URL bạn đã test
    assert params["to"] % 86400 == 0 and params["to"] > params["from"]

def test_no_data_returns_empty():
    assert _client({"s": "no_data"}).get_index_history("XYZ").empty
    assert _client({"s": "ok", "bars": []}).get_index_history("XYZ").empty

def test_store_full_then_incremental(tmp_path):
    bars = {"s": "ok", "bars": [{"t": 1262563200 + 86400 * i, "c": 500.0 + i} for i in range(40)]}
    c = _client(bars); path = str(tmp_path / "h.csv")
    st = IndexStore(path); st.update_from_history(c, ["VNINDEX"])
    assert c.session.get.call_args.kwargs["params"]["from"] == 1262304000      # lần đầu: tải đủ
    assert len(IndexStore(path).ohl) == 40
    st2 = IndexStore(path); st2.update_from_history(c, ["VNINDEX"], overlap_days=2)
    assert c.session.get.call_args.kwargs["params"]["from"] > 1262304000        # lần sau: chỉ tải bù
    assert len(st2.ohl) == 40                                                   # idempotent

def test_today_before_close_is_provisional_not_persisted(tmp_path):
    now = datetime.now(VN_TZ)
    if now.hour >= 15:
        return
    t = int(now.replace(hour=7, minute=0, second=0, microsecond=0).timestamp())
    c = _client({"s": "ok", "bars": [{"t": t - 86400 * 3, "c": 1700.0}, {"t": t, "c": 1710.0}]})
    st = IndexStore(str(tmp_path / "h.csv")); st.update_from_history(c, ["VNINDEX"])
    assert len(st.ohl) == 1 and st.series("VNINDEX")["close"].iloc[-1] == 1710.0
    snap = st.snapshot_for("VNINDEX")
    assert snap["derived"] and snap["change"] == 10.0 and snap["is_final"] is False

def test_snapshot_does_not_override_tradingview(tmp_path, capsys):
    day = datetime.now(VN_TZ) - timedelta(days=3)
    t = int(day.replace(hour=7, minute=0, second=0, microsecond=0).timestamp())
    st = IndexStore(str(tmp_path / "h.csv")); st.update_from_history(_client({"s": "ok", "bars": [{"t": t, "c": 1700.0}]}), ["VNINDEX"])
    st.upsert(pd.DataFrame())  # no-op
    snap = {"code": "VNINDEX", "date": day.strftime("%Y-%m-%d"), "close": 1650.0, "ref": 1600.0, "change": 50.0, "change_pct": 3.1,
            "up": 1, "down": 1, "no_change": 0, "is_final": True}
    client = MagicMock(); client.get_index_snapshot.return_value = snap
    st.update_from_api(client, ["VNINDEX"])
    assert st.ohl["close"].iloc[0] == 1700.0 and st.df.empty and "khác" in capsys.readouterr().out


def test_snapshot_only_rows_do_not_block_full_history(tmp_path):
    """Lỗi từng gặp: kho chỉ có dòng snapshot 1 ngày -> code tưởng đã có lịch sử và chỉ tải bù 10 ngày."""
    path = str(tmp_path / "h.csv")
    st = IndexStore(path)
    st.upsert(pd.DataFrame({"time": ["2026-11-16"], "code": ["VNINDEX"], "close": [1736.89], "ref": [1734.93], "source": ["yuanta_index_transaction"]}))
    st.save()
    c = _client(VNINDEX)
    IndexStore(path).update_from_history(c, ["VNINDEX"])
    assert c.session.get.call_args.kwargs["params"]["from"] == 1262304000


def test_series_merges_ohlcv_primary_with_history_gap_fill(tmp_path):
    st = IndexStore(str(tmp_path / "h.csv"))
    st.upsert_ohlcv(pd.DataFrame({"time": ["2026-11-12", "2026-11-13"], "code": "VNINDEX", "open": [1.0, 2.0], "high": [3.0, 4.0],
                                  "low": [0.5, 1.5], "close": [1700.0, 1710.0], "volume": [100, 200]}))
    st.upsert(pd.DataFrame({"time": ["2026-11-13", "2026-11-16"], "code": "VNINDEX", "close": [9999.0, 1736.89], "ref": [None, 1734.93],
                            "source": "yuanta_index_transaction"}))
    o = st.ohlcv_series("VNINDEX")
    assert o["close"].tolist() == [1700.0, 1710.0, 1736.89]          # OHLCV thắng history ở 13/11; history lấp 16/11
    assert o["volume"].tolist()[:2] == [100, 200] and pd.isna(o["open"].iloc[-1])


def test_circuit_breaker_stops_after_three_failures(tmp_path):
    c = YuantaClient(rate_limit_delay=0)
    c.get_index_history = MagicMock(return_value=pd.DataFrame())
    st = IndexStore(str(tmp_path / "h.csv"))
    st.update_from_history(c, ["A", "B", "C", "D", "E", "F"])
    assert c.get_index_history.call_count == 3 and st.stats["F"]["skipped"] == "circuit_breaker"
