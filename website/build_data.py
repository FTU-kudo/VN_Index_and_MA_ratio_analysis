import json
import os
import pandas as pd

CSV_FILE = "hose_6years_data.csv"
OUT_FILE = os.path.join("website", "data", "market_breadth_daily.json")


def process_data(full_df: pd.DataFrame) -> pd.DataFrame:
    full_df = full_df.sort_values(by=["symbol", "time"])

    # MA
    full_df["MA10"] = full_df.groupby("symbol")["close"].transform(lambda x: x.rolling(10).mean())
    full_df["MA20"] = full_df.groupby("symbol")["close"].transform(lambda x: x.rolling(20).mean())
    full_df["MA50"] = full_df.groupby("symbol")["close"].transform(lambda x: x.rolling(50).mean())
    full_df["MA200"] = full_df.groupby("symbol")["close"].transform(lambda x: x.rolling(200).mean())

    full_df[">MA10"] = full_df["close"] > full_df["MA10"]
    full_df[">MA20"] = full_df["close"] > full_df["MA20"]
    full_df[">MA50"] = full_df["close"] > full_df["MA50"]
    full_df[">MA200"] = full_df["close"] > full_df["MA200"]

    daily_stats = full_df.groupby("time").agg(
        total_stocks=("symbol", "count"),
        valid_ma10=("MA10", lambda x: x.notna().sum()),
        valid_ma20=("MA20", lambda x: x.notna().sum()),
        valid_ma50=("MA50", lambda x: x.notna().sum()),
        valid_ma200=("MA200", lambda x: x.notna().sum()),
        gt_ma10=(">MA10", "sum"),
        gt_ma20=(">MA20", "sum"),
        gt_ma50=(">MA50", "sum"),
        gt_ma200=(">MA200", "sum"),
    ).reset_index()

    daily_stats["pct_MA10"] = (daily_stats["gt_ma10"] / daily_stats["valid_ma10"] * 100).fillna(0)
    daily_stats["pct_MA20"] = (daily_stats["gt_ma20"] / daily_stats["valid_ma20"] * 100).fillna(0)
    daily_stats["pct_MA50"] = (daily_stats["gt_ma50"] / daily_stats["valid_ma50"] * 100).fillna(0)
    daily_stats["pct_MA200"] = (daily_stats["gt_ma200"] / daily_stats["valid_ma200"] * 100).fillna(0)

    return daily_stats[["time", "pct_MA10", "pct_MA20", "pct_MA50", "pct_MA200"]]


def main():
    if not os.path.exists(CSV_FILE):
        raise FileNotFoundError(
            f"Missing {CSV_FILE}. Run analysis.py first to generate the cache CSV."
        )

    df = pd.read_csv(CSV_FILE)
    df["time"] = pd.to_datetime(df["time"]).dt.date

    # Compute breadth ratios from CSV data
    daily_stats = process_data(df)

    # Load VNINDEX series from the pre-generated HTML? Not available.
    # For website usage, we re-compute VNINDEX by reading from analysis cache if present.
    # Expected: you should also generate a VNINDEX series JSON separately.
    # Fallback: if vnindex.csv exists, load it.
    vn_csv = "vnindex.csv"
    if os.path.exists(vn_csv):
        vn = pd.read_csv(vn_csv)
        vn["time"] = pd.to_datetime(vn["time"]).dt.date
        vn = vn.rename(columns={"close": "VNINDEX"})[["time", "VNINDEX"]]
    else:
        # If no vnindex.csv, create placeholder series (chart still loads, but VNINDEX missing)
        # This keeps build deterministic.
        vn = daily_stats[["time"]].copy()
        vn["VNINDEX"] = 0

    merged = pd.merge(vn, daily_stats, on="time", how="inner")
    merged = merged.sort_values("time")

    # Convert date to string
    merged["time"] = merged["time"].astype(str)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged.to_dict(orient="records"), f, ensure_ascii=False)

    print(f"Wrote {OUT_FILE} ({len(merged)} rows).")


if __name__ == "__main__":
    main()

