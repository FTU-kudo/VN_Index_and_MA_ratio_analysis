"""
Kho lịch sử điểm số các chỉ số (VNINDEX, VN30, VNREAL, ...)
------------------------------------------------------------
Endpoint Yuanta `index_transaction/{code}` chỉ trả dữ liệu của PHIÊN GẦN NHẤT, nên lịch sử điểm số được
tích luỹ vào `data/index_history.csv` (commit vào repo, không dùng actions/cache vì cache có thể bị xoá).
Nguồn chính: `update_from_history` - endpoint TradingView của Yuanta (toàn bộ lịch sử ngày của MỌI chỉ số, từ
2010 hoặc từ khi chỉ số ra đời). Lần đầu tải đủ lịch sử, các lần sau chỉ tải bù ~10 ngày gần nhất.
Nguồn bổ sung: `update_from_api` - snapshot phiên hiện tại (tăng/giảm, tham chiếu) + kiểm tra chéo với lịch sử.
`scripts/backfill_index_history.py --source yuanta` chạy cùng logic trên máy cá nhân nếu runner bị chặn.

Schema: time (YYYY-MM-DD), code, close, ref, source   -- khoá duy nhất (time, code)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

import pandas as pd

from yuanta_client import GROUP_TO_INDEX_CODE, VN_TZ, YuantaClient

HISTORY_CSV = "data/index_history.csv"
COLUMNS = ["time", "code", "close", "ref", "source"]


def group_index_code(group: str) -> str:
    """HOSE -> VNINDEX; các nhóm còn lại dùng chính mã nhóm (VN30, VNREAL, ...)."""
    g = str(group).strip().upper()
    return GROUP_TO_INDEX_CODE.get(g, g)


class IndexStore:
    def __init__(self, path: str = HISTORY_CSV):
        self.path = path
        self.df = self._load()
        self.snapshots: Dict[str, dict] = {}
        self.provisional: Dict[str, dict] = {}   # điểm của phiên đang diễn ra (chưa ghi vào lịch sử)
        self.stats: Dict[str, dict] = {}         # thống kê lần cập nhật để in log

    def _load(self) -> pd.DataFrame:
        if not os.path.exists(self.path):
            return pd.DataFrame(columns=COLUMNS)
        df = pd.read_csv(self.path)
        df["time"] = pd.to_datetime(df["time"]).dt.date
        df["code"] = df["code"].astype(str).str.upper()
        return df[COLUMNS].drop_duplicates(["time", "code"], keep="last")

    def upsert(self, rows: pd.DataFrame) -> None:
        if rows is None or rows.empty:
            return
        rows = rows.copy()
        rows["time"] = pd.to_datetime(rows["time"]).dt.date
        if "ref" not in rows:
            rows["ref"] = None
        if "source" not in rows:
            rows["source"] = "import"
        merged = pd.concat([self.df, rows[COLUMNS]], ignore_index=True)
        # Dòng mới ghi đè dòng cũ cùng (time, code)
        self.df = merged.drop_duplicates(["time", "code"], keep="last").sort_values(["code", "time"]).reset_index(drop=True)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.df.sort_values(["code", "time"]).to_csv(self.path, index=False)

    def update_from_history(self, client: YuantaClient, codes: Iterable[str], full_from: str = "2010-01-01",
                            overlap_days: int = 10, force_full: bool = False) -> Dict[str, dict]:
        """
        Nguồn chính: lịch sử ngày từ Yuanta TradingView. Chưa có dữ liệu của mã -> tải đủ từ `full_from`;
        đã có -> chỉ tải bù từ (ngày cuối - overlap_days) để vá các phiên bị sửa/bị lỡ.
        Bar của HÔM NAY trước 15:00 (UTC+7) là điểm tạm thời: hiển thị nhưng không ghi vào lịch sử.
        """
        now = datetime.now(VN_TZ)
        today, closed = now.date(), now.hour >= 15
        full_flag = force_full or os.getenv("INDEX_FULL_REFRESH") == "1"
        ok = 0
        for code in dict.fromkeys(str(c).upper() for c in codes):
            have = self.df[(self.df["code"] == code) & (self.df["source"] != "yuanta_index_transaction")]
            # Các dòng snapshot (mỗi ngày 1 dòng) KHÔNG phải lịch sử -> không được làm mất lượt tải đầy đủ từ 2010
            need_full = full_flag or len(have) < 30
            start = full_from if need_full else (max(have["time"]) - timedelta(days=overlap_days)).strftime("%Y-%m-%d")
            h = client.get_index_history(code, start)
            if h.empty:
                print(f"  [IndexHist] {code}: không có dữ liệu lịch sử (từ {start}).")
                self.stats[code] = {"fetched": 0, "from": start}
                continue
            ok += 1
            live = h[(h["time"] == today) & (not closed)]
            if not live.empty:
                self.provisional[code] = {"date": today, "close": float(live["close"].iloc[-1])}
            h = h.drop(live.index)
            rows = pd.DataFrame({"time": h["time"], "code": code, "close": h["close"], "ref": None, "source": "yuanta_tradingview"})
            self.upsert(rows)
            self.stats[code] = {"fetched": len(h), "from": start, "first": h["time"].min() if len(h) else None, "last": h["time"].max() if len(h) else None}
            print(f"  [IndexHist] {code}: +{len(h)} phiên (từ {start}) | {h['time'].min()} -> {h['time'].max()} | điểm cuối {h['close'].iloc[-1] if len(h) else 'n/a'}"
                  + (" | + điểm tạm thời hôm nay" if not live.empty else ""))
        n_all = len(dict.fromkeys(str(c).upper() for c in codes))
        print(f"Tổng kết lịch sử chỉ số: {ok}/{n_all} mã tải được.")
        if ok == 0:
            print("[CẢNH BÁO] KHÔNG tải được lịch sử của chỉ số nào từ Yuanta TradingView - dùng kho CSV hiện có (nếu có).")
        self.save()
        return self.stats

    def update_from_api(self, client: YuantaClient, codes: Iterable[str]) -> Dict[str, dict]:
        """Lấy snapshot mới nhất của từng mã chỉ số. Chỉ GHI VÀO KHO khi phiên đã chốt (is_final)."""
        rows = []
        for code in dict.fromkeys(str(c).upper() for c in codes):
            snap = client.get_index_snapshot(code)
            if not snap:
                print(f"  [Index] {code}: không lấy được snapshot.")
                continue
            self.snapshots[code] = snap
            tag = "CHỐT" if snap["is_final"] else "TẠM THỜI (chưa ghi vào lịch sử)"
            print(f"  [Index] {code}: {snap['close']} (tham chiếu {snap['ref']}, {snap['change_pct']}%) ngày {snap['date']} - {tag}")
            if snap["is_final"]:
                d = pd.to_datetime(snap["date"]).date()
                ex = self.df[(self.df["code"] == code) & (self.df["time"] == d) & (self.df["source"] == "yuanta_tradingview")]
                if ex.empty:
                    rows.append({"time": snap["date"], "code": code, "close": snap["close"], "ref": snap["ref"], "source": "yuanta_index_transaction"})
                else:  # đã có từ TradingView -> chỉ đối chiếu, không ghi đè
                    diff = abs(float(ex["close"].iloc[-1]) - snap["close"]) / snap["close"]
                    if diff > 0.001:
                        print(f"  [CẢNH BÁO] {code} {snap['date']}: TradingView={ex['close'].iloc[-1]} khác index_transaction={snap['close']} ({diff:.2%}). Giữ TradingView.")
        n_all = len(dict.fromkeys(str(c).upper() for c in codes))
        print(f"Tổng kết snapshot chỉ số: {len(self.snapshots)}/{n_all} thành công, {len(rows)} chỉ số được ghi vào lịch sử.")
        if not self.snapshots:
            print("[CẢNH BÁO] KHÔNG lấy được snapshot của chỉ số nào -> web sẽ không có điểm số. "
                  "Xem các dòng [YuantaClient] phía trên (HTTP status / body không phải JSON).")
        if rows:
            self.upsert(pd.DataFrame(rows))
            self.save()
        return self.snapshots

    def series(self, code: str) -> pd.DataFrame:
        """Chuỗi điểm đóng cửa [time, close] của một mã, kèm điểm tạm thời của phiên đang diễn ra (nếu có)."""
        code = str(code).upper()
        out = self.df[self.df["code"] == code][["time", "close"]].copy()
        live = self.provisional.get(code)
        snap = self.snapshots.get(code)
        if live is None and snap and not snap["is_final"]:
            live = {"date": pd.to_datetime(snap["date"]).date(), "close": snap["close"]}
        if live:
            out = out[out["time"] != live["date"]]
            out = pd.concat([out, pd.DataFrame({"time": [live["date"]], "close": [live["close"]]})], ignore_index=True)
        return out.sort_values("time").reset_index(drop=True)

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
