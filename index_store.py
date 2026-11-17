"""
Kho điểm số các chỉ số (VNINDEX, VN30, VNREAL, ...)
----------------------------------------------------
Hai file, mỗi file do MỘT workflow sở hữu (tránh xung đột commit):

  data/index_ohlcv.csv     OHLCV ngày đầy đủ từ Yuanta TradingView (2010 -> nay).
                           Do workflow "Fetch Index OHLCV" cập nhật.
                           Cột: time, code, open, high, low, close, volume, source

  data/index_history.csv   Điểm đóng cửa từng ngày lấy từ index_transaction (snapshot) hoặc nạp tay (vnstock/CSV).
                           Do workflow chính "VN-Index and MA Ratio Analysis" cập nhật.
                           Cột: time, code, close, ref, source

`series()` / `ohlcv_series()` ghép cả hai: OHLCV là nguồn chính, history chỉ lấp các ngày chưa có trong OHLCV,
rồi thêm điểm TẠM THỜI của phiên đang diễn ra (không bao giờ ghi vào file).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

import pandas as pd

from yuanta_client import GROUP_TO_INDEX_CODE, VN_TZ, YuantaClient

HISTORY_CSV = "data/index_history.csv"
OHLCV_CSV = "data/index_ohlcv.csv"
COLUMNS = ["time", "code", "close", "ref", "source"]
OHLCV_COLUMNS = ["time", "code", "open", "high", "low", "close", "volume", "source"]
SNAPSHOT_SOURCE = "yuanta_index_transaction"
TV_SOURCE = "yuanta_tradingview"
MIN_ROWS_FOR_INCREMENTAL = 30      # ít hơn ngưỡng này -> coi như chưa có lịch sử, tải đủ từ 2010
MAX_CONSECUTIVE_FAILS = 3          # cầu chì: 3 mã liên tiếp lỗi -> dừng, tránh gọi vô ích 22 lần


def group_index_code(group: str) -> str:
    """HOSE -> VNINDEX; các nhóm còn lại dùng chính mã nhóm (VN30, VNREAL, ...)."""
    g = str(group).strip().upper()
    return GROUP_TO_INDEX_CODE.get(g, g)


def _read_csv(path: str, columns) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=columns)
    df = pd.read_csv(path)
    for c in columns:
        if c not in df.columns:
            df[c] = None
    df["time"] = pd.to_datetime(df["time"]).dt.date
    df["code"] = df["code"].astype(str).str.upper()
    return df[columns].drop_duplicates(["time", "code"], keep="last")


class IndexStore:
    def __init__(self, path: str = HISTORY_CSV, ohlcv_path: str = OHLCV_CSV):
        self.path = path
        self.ohlcv_path = ohlcv_path
        self.df = _read_csv(path, COLUMNS)
        self.ohl = _read_csv(ohlcv_path, OHLCV_COLUMNS)
        self.snapshots: Dict[str, dict] = {}
        self.provisional: Dict[str, dict] = {}   # điểm của phiên đang diễn ra (chưa ghi vào lịch sử)
        self.stats: Dict[str, dict] = {}         # thống kê lần cập nhật để in log / ghi báo cáo
        self._ohl_dirty = False

    # ------------------------------------------------------------------ ghi
    def upsert(self, rows: pd.DataFrame) -> None:
        """Ghi điểm đóng cửa (snapshot / nạp tay) vào file history."""
        if rows is None or rows.empty:
            return
        rows = rows.copy()
        rows["time"] = pd.to_datetime(rows["time"]).dt.date
        if "ref" not in rows:
            rows["ref"] = None
        if "source" not in rows:
            rows["source"] = "import"
        merged = pd.concat([self.df, rows[COLUMNS]], ignore_index=True)
        self.df = merged.drop_duplicates(["time", "code"], keep="last").sort_values(["code", "time"]).reset_index(drop=True)

    def upsert_ohlcv(self, rows: pd.DataFrame) -> None:
        if rows is None or rows.empty:
            return
        rows = rows.copy()
        rows["time"] = pd.to_datetime(rows["time"]).dt.date
        if "source" not in rows:
            rows["source"] = TV_SOURCE
        merged = pd.concat([self.ohl, rows[OHLCV_COLUMNS]], ignore_index=True)
        self.ohl = merged.drop_duplicates(["time", "code"], keep="last").sort_values(["code", "time"]).reset_index(drop=True)
        self._ohl_dirty = True

    def save(self) -> None:
        if not self.df.empty:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self.df.sort_values(["code", "time"]).to_csv(self.path, index=False)

    def save_ohlcv(self) -> None:
        if self._ohl_dirty and not self.ohl.empty:
            os.makedirs(os.path.dirname(self.ohlcv_path) or ".", exist_ok=True)
            self.ohl.sort_values(["code", "time"]).to_csv(self.ohlcv_path, index=False)
            self._ohl_dirty = False

    # ------------------------------------------------------- nguồn chính: TradingView
    def update_from_history(self, client: YuantaClient, codes: Iterable[str], full_from: str = "2010-01-01",
                            overlap_days: int = 10, force_full: bool = False) -> Dict[str, dict]:
        """
        Tải OHLCV ngày từ Yuanta TradingView. Mã chưa có đủ lịch sử trong index_ohlcv.csv -> tải đủ từ `full_from`;
        ngược lại chỉ tải bù từ (ngày cuối - overlap_days) để vá phiên bị sửa/bị lỡ.
        Bar của HÔM NAY trước 15:00 (UTC+7) là điểm tạm thời: hiển thị nhưng không ghi vào file.
        """
        now = datetime.now(VN_TZ)
        today, closed = now.date(), now.hour >= 15
        full_flag = force_full or os.getenv("INDEX_FULL_REFRESH") == "1"
        code_list = list(dict.fromkeys(str(c).upper() for c in codes))
        ok = fails_in_row = 0
        for code in code_list:
            if fails_in_row >= MAX_CONSECUTIVE_FAILS:
                print(f"  [IndexHist] Dừng sớm: {fails_in_row} mã liên tiếp lỗi -> endpoint TradingView không dùng được từ môi trường này.")
                self.stats[code] = {"fetched": 0, "skipped": "circuit_breaker"}
                continue
            have = self.ohl[self.ohl["code"] == code]
            need_full = full_flag or len(have) < MIN_ROWS_FOR_INCREMENTAL
            start = full_from if need_full else (max(have["time"]) - timedelta(days=overlap_days)).strftime("%Y-%m-%d")
            h = client.get_index_history(code, start)
            if h.empty:
                fails_in_row += 1
                print(f"  [IndexHist] {code}: không có dữ liệu lịch sử (từ {start}).")
                self.stats[code] = {"fetched": 0, "from": start}
                continue
            fails_in_row = 0
            ok += 1
            live = h[(h["time"] == today) & (not closed)]
            if not live.empty:
                self.provisional[code] = {"date": today, "close": float(live["close"].iloc[-1])}
            h = h.drop(live.index)
            self.upsert_ohlcv(h.assign(source=TV_SOURCE)[OHLCV_COLUMNS])
            self.stats[code] = {
                "fetched": len(h), "from": start,
                "first": str(h["time"].min()) if len(h) else None, "last": str(h["time"].max()) if len(h) else None,
            }
            print(f"  [IndexHist] {code}: +{len(h)} phiên (từ {start}) | {h['time'].min()} -> {h['time'].max()} | điểm cuối {h['close'].iloc[-1] if len(h) else 'n/a'}"
                  + (" | + điểm tạm thời hôm nay" if not live.empty else ""))
        print(f"Tổng kết OHLCV chỉ số: {ok}/{len(code_list)} mã tải được.")
        if ok == 0:
            print("[CẢNH BÁO] KHÔNG tải được OHLCV của chỉ số nào từ Yuanta TradingView - dùng các file CSV hiện có (nếu có).")
        self.save_ohlcv()
        return self.stats

    # ------------------------------------------------------- bổ sung: snapshot phiên hiện tại
    def update_from_api(self, client: YuantaClient, codes: Iterable[str]) -> Dict[str, dict]:
        """Lấy snapshot mới nhất của từng mã chỉ số. Chỉ GHI VÀO FILE khi phiên đã chốt (is_final)."""
        rows = []
        code_list = list(dict.fromkeys(str(c).upper() for c in codes))
        for code in code_list:
            snap = client.get_index_snapshot(code)
            if not snap:
                print(f"  [Index] {code}: không lấy được snapshot.")
                continue
            self.snapshots[code] = snap
            tag = "CHỐT" if snap["is_final"] else "TẠM THỜI (chưa ghi vào lịch sử)"
            print(f"  [Index] {code}: {snap['close']} (tham chiếu {snap['ref']}, {snap['change_pct']}%) ngày {snap['date']} - {tag}")
            if snap["is_final"]:
                d = pd.to_datetime(snap["date"]).date()
                ex = self.ohl[(self.ohl["code"] == code) & (self.ohl["time"] == d)]
                if ex.empty:
                    rows.append({"time": snap["date"], "code": code, "close": snap["close"], "ref": snap["ref"], "source": SNAPSHOT_SOURCE})
                else:  # đã có OHLCV từ TradingView -> chỉ đối chiếu, không ghi đè
                    diff = abs(float(ex["close"].iloc[-1]) - snap["close"]) / snap["close"]
                    if diff > 0.001:
                        print(f"  [CẢNH BÁO] {code} {snap['date']}: TradingView={ex['close'].iloc[-1]} khác index_transaction={snap['close']} ({diff:.2%}). Giữ TradingView.")
        print(f"Tổng kết snapshot chỉ số: {len(self.snapshots)}/{len(code_list)} thành công, {len(rows)} chỉ số được ghi vào lịch sử.")
        if not self.snapshots:
            print("[CẢNH BÁO] KHÔNG lấy được snapshot của chỉ số nào -> web sẽ không có điểm số. "
                  "Xem các dòng [YuantaClient] phía trên (HTTP status / body không phải JSON).")
        if rows:
            self.upsert(pd.DataFrame(rows))
        self.save()
        return self.snapshots

    # ------------------------------------------------------------------ đọc
    def ohlcv_series(self, code: str) -> pd.DataFrame:
        """
        [time, open, high, low, close, volume] của một mã. OHLCV (TradingView) là nguồn chính;
        ngày nào chưa có OHLCV thì lấy `close` từ history (open/high/low/volume để trống);
        cuối cùng thêm điểm tạm thời của phiên đang diễn ra.
        """
        code = str(code).upper()
        cols = ["time", "open", "high", "low", "close", "volume"]
        out = self.ohl[self.ohl["code"] == code][cols].copy()
        hist = self.df[self.df["code"] == code][["time", "close"]]
        hist = hist[~hist["time"].isin(set(out["time"]))]
        if not hist.empty:
            out = pd.concat([out, hist.reindex(columns=cols)], ignore_index=True)
        live = self.provisional.get(code)
        snap = self.snapshots.get(code)
        if live is None and snap and not snap["is_final"]:
            live = {"date": pd.to_datetime(snap["date"]).date(), "close": snap["close"]}
        if live:
            out = out[out["time"] != live["date"]]
            out = pd.concat([out, pd.DataFrame({"time": [live["date"]], "close": [live["close"]]}).reindex(columns=cols)], ignore_index=True)
        for c in cols[1:]:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out.sort_values("time").reset_index(drop=True)

    def series(self, code: str) -> pd.DataFrame:
        """Chuỗi điểm đóng cửa [time, close]."""
        return self.ohlcv_series(code)[["time", "close"]]

    def snapshot_for(self, code: str) -> Optional[dict]:
        """Snapshot để hiển thị: ưu tiên index_transaction (có tăng/giảm); nếu lỗi thì suy ra từ 2 phiên cuối của lịch sử."""
        code = str(code).upper()
        if code in self.snapshots:
            return self.snapshots[code]
        ser = self.series(code)
        if ser.empty:
            return None
        last = ser.iloc[-1]
        prev = float(ser.iloc[-2]["close"]) if len(ser) > 1 else None
        close = float(last["close"])
        live = self.provisional.get(code)
        return {
            "code": code, "date": last["time"].strftime("%Y-%m-%d"), "close": round(close, 2),
            "ref": round(prev, 2) if prev else None,
            "change": round(close - prev, 2) if prev else None,
            "change_pct": round((close - prev) / prev * 100, 2) if prev else None,
            "up": None, "down": None, "no_change": None, "ceiling": None, "floor": None,
            "is_final": live is None, "derived": True,
        }
