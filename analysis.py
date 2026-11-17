import argparse
import copy
import json
import os
import smtplib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as plotly_go
from plotly.subplots import make_subplots

from index_store import IndexStore, group_index_code
from yuanta_client import FALLBACK_HOSE_SYMBOLS, INDEX_GROUPS, YuantaClient

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Nạp cấu hình từ file .env (nếu có)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _load_symbols_file(path: str):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        raw = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
    return sorted({s for s in raw if len(s) == 3 and s.isalpha()})


def get_hose_symbols(
    client: YuantaClient | None = None,
    symbols_file: str = "hose_symbols.txt",
    cache_csv: str = "hose_6years_data.csv",
):
    """
    Lấy danh sách cổ phiếu HOSE hợp lệ (3 ký tự chữ cái viết hoa). Thứ tự ưu tiên:
      1. Yuanta API (list_stock_info?exchange=HSX)  -> thành công thì lưu lại vào hose_symbols.txt
      2. hose_symbols.txt (snapshot của lần chạy thành công gần nhất)
      3. Các mã đang có trong cache CSV (tránh tụt từ ~400 mã xuống 60 mã khi API danh sách lỗi)
      4. FALLBACK_HOSE_SYMBOLS (60 mã thanh khoản cao)
    """
    print("Bước 1: Lấy danh sách cổ phiếu HOSE...")
    if client is not None:
        for code in ("HOSE", "VNALL"):
            symbols = client.get_index_symbols(code)
            if symbols:
                print(f"Đã lấy {len(symbols)} mã HOSE từ Yuanta API (index_code={code}).")
                if code == "HOSE":
                    try:
                        with open(symbols_file, "w", encoding="utf-8") as f:
                            f.write(f"# Auto-generated {datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')):%Y-%m-%d} from Yuanta list_stock_info?exchange=HSX\n")
                            f.write("\n".join(symbols) + "\n")
                    except OSError as e:
                        print("Không ghi được hose_symbols.txt:", e)
                return symbols

    symbols = _load_symbols_file(symbols_file)
    if symbols:
        print(f"[CẢNH BÁO] API danh sách mã lỗi -> dùng {len(symbols)} mã từ {symbols_file}.")
        return symbols

    if os.path.exists(cache_csv):
        try:
            cached = pd.read_csv(cache_csv, usecols=["symbol"])
            symbols = sorted({str(s).upper() for s in cached["symbol"].dropna().unique() if len(str(s)) == 3 and str(s).isalpha()})
            if symbols:
                print(f"[CẢNH BÁO] API danh sách mã lỗi -> dùng {len(symbols)} mã đang có trong cache CSV.")
                return symbols
        except Exception as e:
            print("Không đọc được symbol từ cache CSV:", e)

    print(f"[CẢNH BÁO] Sử dụng danh sách mặc định ({len(FALLBACK_HOSE_SYMBOLS)} mã HOSE thanh khoản cao).")
    return sorted(FALLBACK_HOSE_SYMBOLS)


def _align_price_units(cached_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Cache cũ (thời vnstock) lưu giá theo nghìn VND (vd 20.45), API Yuanta trả VND (20450).
    Nếu để lẫn, MA200 sẽ sai hoàn toàn. So sánh phần ngày chồng lấn; nếu lệch ~1000 lần thì nhân cache lên 1000.
    """
    m = cached_df[["symbol", "time", "close"]].merge(
        new_df[["symbol", "time", "close"]], on=["symbol", "time"], suffixes=("_old", "_new")
    )
    m = m[(m["close_old"] > 0) & (m["close_new"] > 0)]
    if m.empty:
        return cached_df
    ratio = float((m["close_new"] / m["close_old"]).median())
    if 900 <= ratio <= 1100:
        print(f"[CẢNH BÁO] Cache lưu giá theo nghìn VND (tỷ lệ API/cache ≈ {ratio:.0f}). Đã nhân cache x1000 để đồng nhất đơn vị.")
        cached_df = cached_df.copy()
        for c in ("open", "high", "low", "close"):
            if c in cached_df.columns:
                cached_df[c] = cached_df[c] * 1000.0
    return cached_df


def get_historical_data(client: YuantaClient, symbols, start_date: str, end_date: str, max_workers: int = 5):
    """
    Tải dữ liệu OHLCV từ Yuanta API kết hợp cache CSV cục bộ.
    Sử dụng ThreadPoolExecutor để tối ưu tốc độ tải mà vẫn an toàn cho API.
    """
    csv_file = "hose_6years_data.csv"
    cached_df = None
    fetch_start = start_date

    if os.path.exists(csv_file):
        try:
            cached_df = pd.read_csv(csv_file)
            cached_df["time"] = pd.to_datetime(cached_df["time"]).dt.date
            last_date = cached_df["time"].max()
            # Lùi lại 14 ngày để bù các phiên nghỉ/giá điều chỉnh/lỗi cache, chỉ tải phần dữ liệu mới
            fetch_start = (pd.to_datetime(last_date) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
            print(f"Đã có cache đến {last_date}. Chỉ tải bổ sung từ {fetch_start}...")
        except Exception as e:
            print("Lỗi khi đọc file CSV cache, sẽ tải lại toàn bộ:", e)
            cached_df = None

    print(f"Bước 2: Tải dữ liệu OHLCV từ Yuanta API ({fetch_start} -> {end_date})...")
    all_data = []
    total = len(symbols)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_sym = {
            executor.submit(client.get_price_history, sym, fetch_start, end_date): sym
            for sym in symbols
        }
        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            completed += 1
            try:
                df = future.result()
                if df is not None and not df.empty:
                    all_data.append(df)
            except Exception as e:
                print(f"Lỗi khi tải dữ liệu mã {sym}: {e}")

            if completed % 10 == 0 or completed == total:
                print(f"Đã tải {completed}/{total} mã cổ phiếu...")

    print(
        f"Tổng kết tải giá: {len(all_data)}/{total} mã có dữ liệu | "
        f"request lỗi={client.failed_requests} | JSON rỗng={client.empty_payloads}"
    )
    if not all_data:
        # fetch_start luôn lùi 14 ngày so với cache => nếu API hoạt động thì CHẮC CHẮN có dữ liệu mới.
        # Không có nghĩa là API hỏng -> dừng hẳn, tránh xuất bản biểu đồ cũ như thể là mới.
        print("LỖI NGHIÊM TRỌNG: Không tải được dữ liệu mới từ Yuanta API (xem các dòng [YuantaClient] phía trên).")
        sys.exit(1)

    new_df = pd.concat(all_data, ignore_index=True)
    new_df["time"] = pd.to_datetime(new_df["time"]).dt.date

    if cached_df is not None:
        cached_df = _align_price_units(cached_df, new_df)
        full_df = pd.concat([cached_df, new_df], ignore_index=True)
    else:
        full_df = new_df

    # Loại bỏ trùng lặp (giữ dữ liệu mới nhất cho mỗi symbol+ngày), sắp xếp lại để chống look-ahead bias
    full_df = full_df.drop_duplicates(subset=["symbol", "time"], keep="last")
    full_df = full_df.sort_values(by=["symbol", "time"]).reset_index(drop=True)

    print(f"Lưu dữ liệu ra file {csv_file} (tổng {len(full_df)} dòng, từ {full_df['time'].min()} đến {full_df['time'].max()})...")
    full_df.to_csv(csv_file, index=False)
    return full_df


def process_data(full_df: pd.DataFrame) -> pd.DataFrame:
    """
    Tính toán các đường MA và Tỷ lệ Market Breadth.
    - Sắp xếp theo ['symbol', 'time'] trước khi rolling để đảm bảo không có look-ahead bias.
    - Lọc các phiên có volume > 0 khi tính Market Breadth để loại bỏ nhiễu từ cổ phiếu mất thanh khoản.
    """
    print("Bước 3 & 4: Tính toán các chỉ số MA và Tỷ lệ Market Breadth...")
    full_df = full_df.sort_values(by=["symbol", "time"]).reset_index(drop=True)

    # Tính các đường MA (backward-looking, min_periods=window mặc định)
    for w in (10, 20, 50, 200):
        full_df[f"MA{w}"] = full_df.groupby("symbol")["close"].transform(lambda x, win=w: x.rolling(win).mean())
        full_df[f">MA{w}"] = (full_df["close"] > full_df[f"MA{w}"]) & (full_df["volume"] > 0)

    # Chỉ tính trên các mã có giao dịch (volume > 0) và đã đủ dữ liệu tính MA
    active_df = full_df[full_df["volume"] > 0].copy()
    if active_df.empty:
        active_df = full_df.copy()

    daily_stats = active_df.groupby("time").agg(
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

    for w in (10, 20, 50, 200):
        daily_stats[f"pct_MA{w}"] = (
            daily_stats[f"gt_ma{w}"] / daily_stats[f"valid_ma{w}"].replace(0, pd.NA) * 100
        ).fillna(0.0)

    # Tính tín hiệu giao cắt MA20 vs MA50 (dùng shift(1) để chống look-ahead bias)
    daily_stats = daily_stats.sort_values("time").reset_index(drop=True)
    prev_ma20 = daily_stats["pct_MA20"].shift(1)
    prev_ma50 = daily_stats["pct_MA50"].shift(1)

    daily_stats["breadth_buy_cross"] = (prev_ma20 <= prev_ma50) & (daily_stats["pct_MA20"] > daily_stats["pct_MA50"])
    daily_stats["breadth_sell_cross"] = (prev_ma20 >= prev_ma50) & (daily_stats["pct_MA20"] < daily_stats["pct_MA50"])

    return daily_stats


INDEX_HOVER = (
    "<b>{label}</b> %{{y:,.2f}}<br>O %{{customdata[0]:,.2f}} · H %{{customdata[1]:,.2f}} · L %{{customdata[2]:,.2f}}"
    "<br>Vol %{{customdata[3]:,.0f}}<extra></extra>"
)


def _breadth_signals(stats: pd.DataFrame, index_by_time: pd.Series, foreign_by_time: pd.Series, has_foreign: bool):
    """Điểm Mua/Bán: MA20 cắt MA50 (breadth) + bộ lọc dòng tiền ngoại toàn thị trường (nếu có). Đặt tại điểm số của chỉ số."""
    df = stats[["time", "breadth_buy_cross", "breadth_sell_cross"]].copy()
    df["idx"] = df["time"].map(index_by_time)
    df["fn"] = df["time"].map(foreign_by_time).fillna(0.0)
    df = df.dropna(subset=["idx"])
    buy = df[df["breadth_buy_cross"] & ((not has_foreign) | (df["fn"] >= 0))]
    sell = df[df["breadth_sell_cross"] & ((not has_foreign) | (df["fn"] <= 0))]
    pack = lambda d: {"x": [t.strftime("%Y-%m-%d") for t in d["time"]], "y": [round(float(v), 2) for v in d["idx"]]}
    return pack(buy), pack(sell)


def export_all_groups_json(
    client: YuantaClient,
    full_df: pd.DataFrame,
    store: IndexStore,
    foreign_df: pd.DataFrame | None,
    plot_start_date: str = "2021-06-25",
    output_json: str = "output/breadth_groups.json",
):
    """
    Tính MA Ratio (MA10/20/50/200) + điểm số CHÍNH CỦA TỪNG CHỈ SỐ (VNINDEX, VN30, VNREAL...) cho mọi nhóm
    trong INDEX_GROUPS và xuất JSON để build_pages.py nhúng vào GitHub Pages.
    """
    print("Bước 5b: Tính toán MA Ratio + điểm số chỉ số cho tất cả các nhóm...")
    plot_start = pd.to_datetime(plot_start_date).date()
    hose_stats = process_data(full_df)
    base_dates = sorted(t for t in hose_stats["time"].unique() if t >= plot_start)
    if not base_dates:
        return
    base = pd.DataFrame({"time": base_dates})

    if foreign_df is not None and not foreign_df.empty:
        fmap = foreign_df.drop_duplicates("time").set_index("time")["foreign_net_val"]
    else:
        fmap = pd.Series(dtype=float)
    foreign_vals = base["time"].map(fmap).fillna(0.0)
    has_foreign = bool((foreign_vals != 0).any())

    def _points(code: str):
        ser = store.series(code)
        m = base.merge(ser, on="time", how="left")
        return m["close"], ser

    def _ohlcv(code: str):
        """open/high/low/volume căn theo trục ngày của biểu đồ (None nếu ngày đó chưa có OHLCV)."""
        m = base.merge(store.ohlcv_series(code), on="time", how="left")
        r = lambda col, nd: [round(float(v), nd) if pd.notna(v) else None for v in m[col]]
        return {"o": r("open", 2), "h": r("high", 2), "l": r("low", 2), "v": [int(v) if pd.notna(v) else None for v in m["volume"]]}

    hose_idx, _ = _points(group_index_code("HOSE"))
    payload = {
        "dates": [d.strftime("%Y-%m-%d") for d in base["time"]],
        "vnindex": [round(float(v), 2) if pd.notna(v) else None for v in hose_idx],
        "foreign_net_val": [round(float(v), 2) for v in foreign_vals],
        "generated_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d %H:%M:%S"),
        "groups": {},
    }

    for grp in INDEX_GROUPS:
        syms = sorted(full_df["symbol"].unique().tolist()) if grp == "HOSE" else client.get_index_symbols(grp)
        if not syms:
            continue
        sub_df = full_df[full_df["symbol"].isin(syms)]
        if sub_df.empty:
            continue

        stats = process_data(sub_df)
        merged = pd.merge(base[["time"]], stats, on="time", how="left")
        code = group_index_code(grp)
        pts, ser = _points(code)
        buy, sell = _breadth_signals(
            stats, ser.set_index("time")["close"] if not ser.empty else pd.Series(dtype=float),
            fmap, has_foreign,
        )
        r2 = lambda col: [round(float(v), 2) if pd.notna(v) else None for v in merged[col]]
        payload["groups"][grp] = {
            "count": len(syms),
            "index_code": code,
            "index_points": [round(float(v), 2) if pd.notna(v) else None for v in pts],
            "index_snapshot": store.snapshot_for(code),
            "index_ohlcv": _ohlcv(code),
            "pct_MA10": r2("pct_MA10"), "pct_MA20": r2("pct_MA20"),
            "pct_MA50": r2("pct_MA50"), "pct_MA200": r2("pct_MA200"),
            "buy": buy, "sell": sell,
        }
        n_pts = int(pd.Series(pts).notna().sum())
        print(f"  + {grp}: {len(syms)} mã | chỉ số {code}: {n_pts} điểm lịch sử")

    os.makedirs(os.path.dirname(output_json) or ".", exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"Đã xuất dữ liệu các nhóm ngành ra {output_json}")


def get_vnindex(store: IndexStore) -> pd.DataFrame:
    """VN-Index từ kho (OHLCV TradingView + snapshot) -> [time, VNINDEX, idx_open, idx_high, idx_low, idx_volume]."""
    ser = store.ohlcv_series(group_index_code("HOSE"))
    if ser.empty:
        print("[CẢNH BÁO] Chưa có điểm VN-Index nào (TradingView/index_transaction đều lỗi và kho rỗng).")
        return pd.DataFrame(columns=["time", "VNINDEX"])
    print(f"VN-Index: {len(ser)} phiên trong kho, từ {ser['time'].min()} đến {ser['time'].max()}, điểm gần nhất {ser['close'].iloc[-1]}.")
    return ser.rename(columns={"close": "VNINDEX", "open": "idx_open", "high": "idx_high", "low": "idx_low", "volume": "idx_volume"})


def get_vnindex_foreign(client: YuantaClient, start_date: str, end_date: str) -> pd.DataFrame:
    """Lấy dữ liệu giao dịch Khối ngoại của VNINDEX từ Yuanta API (dữ liệu từ 2025 - hiện tại)."""
    print("Lấy dữ liệu dòng tiền Khối ngoại VNINDEX từ Yuanta API...")
    foreign_start = max(start_date, "2025-01-01")
    if foreign_start > end_date:
        return pd.DataFrame()
    df = client.get_foreign_history("VNINDEX", foreign_start, end_date)
    if df.empty:
        return pd.DataFrame()
    return df[["time", "foreign_net_val", "foreign_net_vol"]]


def plot_market_breadth(
    daily_stats: pd.DataFrame,
    vnindex_df: pd.DataFrame,
    ma_lines,
    ma_label: str,
    output_file: str,
    plot_start_date: str = "2021-06-25",
    foreign_df: pd.DataFrame | None = None,
):
    title = f"📊 Market Breadth: VN-Index, {ma_label} & Foreign Flow"
    print(f"Vẽ biểu đồ và lưu ra file HTML/PDF: {output_file}...")

    # Nền là trục ngày của breadth; điểm VN-Index thiếu -> để trống (KHÔNG loại bỏ ngày breadth)
    df = pd.merge(daily_stats, vnindex_df, on="time", how="left")
    if "VNINDEX" not in df.columns:
        df["VNINDEX"] = float("nan")
    if foreign_df is not None and not foreign_df.empty:
        df = pd.merge(df, foreign_df, on="time", how="left")
    else:
        df["foreign_net_val"] = 0.0

    df["foreign_net_val"] = df["foreign_net_val"].fillna(0.0)
    has_foreign = (df["foreign_net_val"] != 0).any()

    plot_start = pd.to_datetime(plot_start_date).date()
    df = df[df["time"] >= plot_start].sort_values("time").reset_index(drop=True)

    if has_foreign:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            row_heights=[0.75, 0.25],
            specs=[[{"secondary_y": True}], [{"secondary_y": False}]],
        )
    else:
        fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Vẽ VNINDEX (line màu tím)
    fig.add_trace(
        plotly_go.Scatter(
            x=df["time"],
            y=df["VNINDEX"],
            name="VN-Index (Pts, left axis)",
            customdata=df[["idx_open", "idx_high", "idx_low", "idx_volume"]].to_numpy() if all(c in df.columns for c in ("idx_open", "idx_high", "idx_low", "idx_volume")) else None,
            hovertemplate=INDEX_HOVER.format(label="VN-Index"),
            mode="lines" if df["VNINDEX"].notna().sum() > 30 else "lines+markers",
            line=dict(color="purple", width=2),
        ),
        row=1 if has_foreign else None,
        col=1 if has_foreign else None,
        secondary_y=False,
    )

    # Tín hiệu Mua/Bán kết hợp Breadth + Foreign Flow
    has_idx = df["VNINDEX"].notna()
    buy_signals = df[has_idx & df["breadth_buy_cross"] & ((~has_foreign) | (df["foreign_net_val"] >= 0))]
    sell_signals = df[has_idx & df["breadth_sell_cross"] & ((~has_foreign) | (df["foreign_net_val"] <= 0))]

    if True:  # luôn thêm trace (kể cả rỗng) để trang web cập nhật khi đổi nhóm
        fig.add_trace(
            plotly_go.Scatter(
                x=buy_signals["time"],
                y=buy_signals["VNINDEX"],
                mode="markers",
                name="Buy Signal (MA20>MA50 + Foreign>=0)",
                marker=dict(symbol="triangle-up", size=10, color="green"),
            ),
            row=1 if has_foreign else None,
            col=1 if has_foreign else None,
            secondary_y=False,
        )

    if True:
        fig.add_trace(
            plotly_go.Scatter(
                x=sell_signals["time"],
                y=sell_signals["VNINDEX"],
                mode="markers",
                name="Sell Signal (MA20<MA50 + Foreign<=0)",
                marker=dict(symbol="triangle-down", size=10, color="crimson"),
            ),
            row=1 if has_foreign else None,
            col=1 if has_foreign else None,
            secondary_y=False,
        )

    colors = {
        "pct_MA10": "cyan",
        "pct_MA20": "red",
        "pct_MA50": "forestgreen",
        "pct_MA200": "orange",
    }
    names = {
        "pct_MA10": "MA10",
        "pct_MA20": "MA20",
        "pct_MA50": "MA50",
        "pct_MA200": "MA200",
    }

    for ma in ma_lines:
        fig.add_trace(
            plotly_go.Scatter(
                x=df["time"],
                y=df[ma],
                name=f"Percentage of Stocks > {names[ma]} (%, right axis)",
                line=dict(color=colors[ma], width=1),
            ),
            row=1 if has_foreign else None,
            col=1 if has_foreign else None,
            secondary_y=True,
        )

    if has_foreign:
        bar_colors = ["green" if v >= 0 else "red" for v in df["foreign_net_val"]]
        fig.add_trace(
            plotly_go.Bar(
                x=df["time"],
                y=df["foreign_net_val"],
                name="Foreign Net Value",
                marker_color=bar_colors,
                opacity=0.75,
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text="Foreign Net Val", row=2, col=1)

    fig.update_layout(
        title=title,
        title_font_size=22,
        font=dict(family="Inter, Roboto, Arial, sans-serif"),
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, xanchor="center", x=0.5),
        xaxis_title="Timeline",
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=50, r=50, t=80, b=150),
    )

    fig.update_yaxes(
        title_text="VN-Index (Points)",
        row=1 if has_foreign else None,
        col=1 if has_foreign else None,
        secondary_y=False,
        showgrid=False,
    )
    fig.update_yaxes(
        title_text="Percentage (%)",
        row=1 if has_foreign else None,
        col=1 if has_foreign else None,
        secondary_y=True,
        range=[0, 100],
        ticksuffix="%",
        showgrid=True,
        gridcolor="gray",
        gridwidth=0.5,
        griddash="dot",
    )

    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    fig.write_html(output_file)
    print(f"Đã lưu HTML (không credit): {output_file}")

    fig_pdf = copy.deepcopy(fig)
    current_time_str = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d %H:%M:%S")
    credit_text = "© Bản quyền thuộc về FTU-Kudo"
    fig_pdf.add_annotation(
        text=f"{credit_text} | Ngày cập nhật: {current_time_str}",
        xref="paper",
        yref="paper",
        x=1.0,
        y=-0.22,
        showarrow=False,
        font=dict(size=12, color="gray"),
        xanchor="right",
        yanchor="top",
    )

    pdf_file = output_file.replace(".html", ".pdf")
    try:
        fig_pdf.write_image(pdf_file, format="pdf", width=1200, height=850, engine="kaleido")
        print(f"Đã lưu PDF (có credit): {pdf_file}")
    except Exception as e:
        print(f"Lỗi khi lưu PDF (có thể do thiếu kaleido): {e}")


def send_email_with_pdfs(pdf_files):
    sender = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    receivers_raw = os.getenv("GMAIL_RECEIVERS") or os.getenv("GMAIL_RECEIVER")

    if not sender or not password or not receivers_raw:
        print("Không tìm thấy cấu hình Gmail trong biến môi trường. Bỏ qua gửi email.")
        return

    receivers = [r.strip() for r in receivers_raw.split(",") if r.strip()]
    if not receivers:
        print("Danh sách người nhận trống. Bỏ qua gửi email.")
        return

    print(f"Đang gửi email đính kèm biểu đồ tới {len(receivers)} người nhận (qua BCC)...")
    try:
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = sender
        msg["Subject"] = "Báo cáo phân tích Tương quan giữa VN-Index và Tỷ lệ mã cổ phiếu vượt các đường MA (Yuanta Data)"

        body = (
            "Xin chào Quý khách,\n\n"
            "Đây là báo cáo phân tích tương quan giữa VN-Index, Tỷ lệ mã vượt các đường MA (MA10, MA20, MA50, MA200) "
            "và Dòng tiền Khối ngoại (dữ liệu từ Yuanta Securities).\n"
            "Xin Quý khách vui lòng xem các file PDF đính kèm.\n\n"
            "Trân trọng,\nFTU-Kudo."
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for file in pdf_files:
            if os.path.exists(file):
                with open(file, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(file))
                part["Content-Disposition"] = f'attachment; filename="{os.path.basename(file)}"'
                msg.attach(part)

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(sender, password)
        server.sendmail(sender, [sender] + receivers, msg.as_string())
        server.quit()
        print(f"Đã gửi email thành công tới {len(receivers)} người nhận!")
    except Exception as e:
        print(f"Lỗi khi gửi email: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VN-Index & MA Ratio Analysis Pipeline (Yuanta API)")
    parser.add_argument("--build", action="store_true", help="Chỉ tải dữ liệu, tính toán & vẽ biểu đồ")
    parser.add_argument("--send-email", action="store_true", help="Chỉ gửi email với các PDF đã có sẵn")
    parser.add_argument(
        "--index-group",
        type=str,
        default="HOSE",
        help=f"Bộ lọc chỉ số/ngành (mặc định: HOSE). Hỗ trợ: {', '.join(INDEX_GROUPS)}",
    )
    args = parser.parse_args()

    no_flag = not (args.build or args.send_email)
    run_build = args.build or no_flag
    run_send = args.send_email or no_flag

    fetch_start_date = "2020-06-25"
    plot_start_date = "2021-06-25"
    end_date = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d")

    pdf_files = [
        "output/market_breadth_chart.pdf",
        "output/market_breadth_chart_ma10_ma20.pdf",
        "output/market_breadth_chart_ma50_ma200.pdf",
    ]

    if run_build:
        client = YuantaClient()
        group_code = args.index_group.strip().upper()
        if group_code == "HOSE":
            symbols = get_hose_symbols(client)
        else:
            symbols = client.get_index_symbols(group_code)
            if not symbols:
                print(f"Không lấy được danh sách mã cho nhóm {group_code}, fallback sang HOSE.")
                symbols = get_hose_symbols(client)

        if symbols:
            df = get_historical_data(client, symbols, fetch_start_date, end_date)
            if not df.empty:
                # Lọc dữ liệu đúng theo nhóm chỉ số/ngành đang chọn (nếu dùng cache chung)
                df_group = df[df["symbol"].isin(symbols)].copy()
                stats = process_data(df_group if not df_group.empty else df)
                print("Bước 4b: Lấy điểm số các chỉ số từ Yuanta index_transaction...")
                store = IndexStore()
                index_codes = [group_index_code(g) for g in INDEX_GROUPS]
                store.update_from_history(client, index_codes)   # nguồn chính: lịch sử ngày của mọi chỉ số
                store.update_from_api(client, index_codes)       # bổ sung: tăng/giảm phiên hiện tại + đối chiếu
                vnindex = get_vnindex(store)
                foreign_df = get_vnindex_foreign(client, fetch_start_date, end_date)

                print("Bước 5: Vẽ biểu đồ và lưu ra file HTML/PDF...")
                plot_market_breadth(
                    stats,
                    vnindex,
                    ["pct_MA10", "pct_MA20", "pct_MA50", "pct_MA200"],
                    "MA lines (MA10, MA20, MA50, MA200)",
                    "output/market_breadth_chart.html",
                    plot_start_date=plot_start_date,
                    foreign_df=foreign_df,
                )
                plot_market_breadth(
                    stats,
                    vnindex,
                    ["pct_MA10", "pct_MA20"],
                    "MA10 và MA20",
                    "output/market_breadth_chart_ma10_ma20.html",
                    plot_start_date=plot_start_date,
                    foreign_df=foreign_df,
                )
                plot_market_breadth(
                    stats,
                    vnindex,
                    ["pct_MA50", "pct_MA200"],
                    "MA50 và MA200",
                    "output/market_breadth_chart_ma50_ma200.html",
                    plot_start_date=plot_start_date,
                    foreign_df=foreign_df,
                )
                export_all_groups_json(
                    client,
                    df,
                    store,
                    foreign_df,
                    plot_start_date=plot_start_date,
                )
                print("Hoàn thành bước build!")
            else:
                print("LỖI NGHIÊM TRỌNG: Dữ liệu cổ phiếu trống.")
                sys.exit(1)
        else:
            print("LỖI NGHIÊM TRỌNG: Không có danh sách cổ phiếu hợp lệ.")
            sys.exit(1)

    if run_send:
        send_email_with_pdfs(pdf_files)
        print("Hoàn thành bước gửi email!")
