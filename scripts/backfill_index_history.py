"""
Nạp LỊCH SỬ điểm số các chỉ số vào data/index_history.csv (chạy 1 lần, trên máy cá nhân).

Vì sao cần: endpoint Yuanta `index_transaction/{code}` chỉ có phiên gần nhất, nên các phiên trước đó phải
được nạp từ nguồn khác một lần; sau đó workflow tự tích luỹ mỗi ngày.

Cách dùng
  # 0) KHUYÊN DÙNG - từ chính API TradingView của Yuanta (toàn bộ lịch sử từ 2010, mọi chỉ số, 1 request/chỉ số).
  #    Workflow tự làm việc này mỗi ngày; chỉ cần chạy tay nếu GitHub runner bị chặn:
  python scripts/backfill_index_history.py --source yuanta

  # 1) Từ vnstock (Unified UI: Market().index(code).ohlcv) - cần VNSTOCK_API_KEY trong .env nếu có
  python scripts/backfill_index_history.py --source vnstock --start 2020-06-25

  # 2) Từ file CSV bạn tự xuất (Vietstock, Yuanta, ...). Hai dạng được chấp nhận:
  #    dài : time,code,close          rộng : time,VNINDEX,VN30,VNREAL,...
  python scripts/backfill_index_history.py --source csv --csv my_indices.csv

Không bao giờ ghi đè các dòng đã lấy từ Yuanta (source = yuanta_index_transaction).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from index_store import IndexStore, group_index_code  # noqa: E402
from yuanta_client import INDEX_GROUPS  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _pick_col(df: pd.DataFrame, names):
    low = {str(c).lower(): c for c in df.columns}
    for n in names:
        if n in low:
            return low[n]
    return None


def from_vnstock(codes, start: str, end: str) -> pd.DataFrame:
    from vnstock.ui import Market  # theo docs/vnstock/unified-ui/02-market.md
    mkt = Market()
    out = []
    for code in codes:
        try:
            raw = mkt.index(code).ohlcv(start=start, end=end)
            df = raw.reset_index() if not isinstance(raw, pd.DataFrame) else raw.reset_index(drop=False)
            tcol = _pick_col(df, ["time", "date", "datetime", "trading_date", "index"])
            ccol = _pick_col(df, ["close", "close_price", "c"])
            if tcol is None or ccol is None or df.empty:
                print(f"  {code}: KHÔNG có cột time/close (cột: {list(df.columns)})")
                continue
            part = pd.DataFrame({"time": pd.to_datetime(df[tcol]).dt.date, "code": code, "close": pd.to_numeric(df[ccol], errors="coerce")})
            part = part.dropna(subset=["close"])
            part["source"] = "vnstock"
            print(f"  {code}: {len(part)} phiên ({part['time'].min()} -> {part['time'].max()}), điểm cuối {part['close'].iloc[-1]}")
            out.append(part)
        except Exception as e:  # mã chỉ số có thể không được nguồn hỗ trợ
            print(f"  {code}: LỖI {type(e).__name__}: {e}")
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    tcol = _pick_col(df, ["time", "date", "ngay"])
    if tcol is None:
        raise SystemExit("CSV cần cột time/date.")
    ccode, cclose = _pick_col(df, ["code"]), _pick_col(df, ["close"])
    if ccode and cclose:  # dạng dài
        out = pd.DataFrame({"time": pd.to_datetime(df[tcol]).dt.date, "code": df[ccode].astype(str).str.upper(), "close": pd.to_numeric(df[cclose], errors="coerce")})
    else:  # dạng rộng
        wide = df.rename(columns={tcol: "time"}).melt(id_vars="time", var_name="code", value_name="close")
        wide["code"] = wide["code"].astype(str).str.upper()
        wide["time"] = pd.to_datetime(wide["time"]).dt.date
        wide["close"] = pd.to_numeric(wide["close"], errors="coerce")
        out = wide
    out = out.dropna(subset=["close"])
    out["source"] = "csv_import"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["yuanta", "vnstock", "csv"], required=True)
    ap.add_argument("--csv")
    ap.add_argument("--start", default="2020-06-25")
    ap.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()

    codes = sorted({group_index_code(g) for g in INDEX_GROUPS})
    if args.source == "yuanta":
        from yuanta_client import YuantaClient
        store = IndexStore()
        store.update_from_history(YuantaClient(), codes, full_from=args.start if args.start != "2020-06-25" else "2010-01-01", force_full=True)
        print(f"Kho OHLCV hiện có {len(store.ohl)} dòng, {store.ohl['code'].nunique()} chỉ số -> {store.ohlcv_path}")
        return
    rows = from_vnstock(codes, args.start, args.end) if args.source == "vnstock" else from_csv(args.csv)
    if rows.empty:
        raise SystemExit("Không nạp được dòng nào.")

    store = IndexStore()
    # Bảo vệ dữ liệu Yuanta: bỏ các dòng nạp trùng (time, code) với dòng Yuanta đã có
    y = store.df[store.df["source"] == "yuanta_index_transaction"][["time", "code"]].assign(_y=1)
    rows = rows.merge(y, on=["time", "code"], how="left")
    rows = rows[rows["_y"].isna()].drop(columns="_y")
    store.upsert(rows)
    store.save()
    print(f"Đã ghi {len(rows)} dòng. Kho hiện có {len(store.df)} dòng, {store.df['code'].nunique()} chỉ số -> {store.path}")


if __name__ == "__main__":
    main()
