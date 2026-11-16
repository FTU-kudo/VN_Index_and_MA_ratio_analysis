"""
Kho lịch sử điểm số các chỉ số (VNINDEX, VN30, VNREAL, ...)
------------------------------------------------------------
Endpoint Yuanta `index_transaction/{code}` chỉ trả dữ liệu của PHIÊN GẦN NHẤT, nên lịch sử điểm số được
tích luỹ vào `data/index_history.csv` (commit vào repo, không dùng actions/cache vì cache có thể bị xoá).
Mỗi lần workflow chạy: upsert điểm đóng cửa của phiên mới nhất. Lịch sử cũ được nạp một lần bằng
`scripts/backfill_index_history.py`.

Schema: time (YYYY-MM-DD), code, close, ref, source   -- khoá duy nhất (time, code)
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, Optional

import pandas as pd

from yuanta_client import GROUP_TO_INDEX_CODE, YuantaClient

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
                rows.append({"time": snap["date"], "code": code, "close": snap["close"], "ref": snap["ref"], "source": "yuanta_index_transaction"})
        if rows:
            self.upsert(pd.DataFrame(rows))
            self.save()
        return self.snapshots

    def series(self, code: str) -> pd.DataFrame:
        """Chuỗi điểm đóng cửa [time, close] của một mã, kèm điểm tạm thời của phiên đang diễn ra (nếu có)."""
        code = str(code).upper()
        out = self.df[self.df["code"] == code][["time", "close"]].copy()
        snap = self.snapshots.get(code)
        if snap and not snap["is_final"]:
            d = pd.to_datetime(snap["date"]).date()
            out = out[out["time"] != d]
            out = pd.concat([out, pd.DataFrame({"time": [d], "close": [snap["close"]]})], ignore_index=True)
        return out.sort_values("time").reset_index(drop=True)
