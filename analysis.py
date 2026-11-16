import argparse
import copy
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

from yuanta_client import FALLBACK_HOSE_SYMBOLS, INDEX_GROUPS, YuantaClient

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Nạp cấu hình từ file .env (nếu có)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_hose_symbols(client: YuantaClient | None = None, symbols_file: str = "hose_symbols.txt"):
    """
    Lấy danh sách ~400 cổ phiếu HOSE hợp lệ (3 ký tự chữ cái viết hoa).
    Ưu tiên lấy trực tiếp từ Yuanta API (`HOSE` / `VNALL`), sau đó mới fallback
    sang `hose_symbols.txt` hoặc `FALLBACK_HOSE_SYMBOLS`.
    """
    print("Bước 1: Lấy danh sách cổ phiếu HOSE...")
    if client is not None:
        for code in ("HOSE", "VNALL"):
            symbols = client.get_index_symbols(code)
            if symbols:
                print(f"Đã lấy {len(symbols)} mã HOSE từ Yuanta API (index_code={code}).")
                return symbols

    if os.path.exists(symbols_file):
        with open(symbols_file, "r", encoding="utf-8") as f:
            raw = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
        symbols = sorted({s for s in raw if len(s) == 3 and s.isalpha()})
        if symbols:
            print(f"Đã nạp {len(symbols)} mã HOSE từ {symbols_file}.")
            return symbols

    print(f"Sử dụng danh sách mặc định ({len(FALLBACK_HOSE_SYMBOLS)} mã HOSE thanh khoản cao).")
    return sorted(FALLBACK_HOSE_SYMBOLS)


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
            # Lùi lại 7 ngày để bù các phiên nghỉ/giá điều chỉnh, chỉ tải phần dữ liệu mới
            fetch_start = (pd.to_datetime(last_date) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
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

    if not all_data:
        print("Không tải được dữ liệu mới từ Yuanta API.")
        return cached_df if cached_df is not None else pd.DataFrame()

    new_df = pd.concat(all_data, ignore_index=True)
    new_df["time"] = pd.to_datetime(new_df["time"]).dt.date

    if cached_df is not None:
        full_df = pd.concat([cached_df, new_df], ignore_index=True)
    else:
        full_df = new_df

    # Loại bỏ trùng lặp (giữ dữ liệu mới nhất cho mỗi symbol+ngày), sắp xếp lại để chống look-ahead bias
    full_df = full_df.drop_duplicates(subset=["symbol", "time"], keep="last")
    full_df = full_df.sort_values(by=["symbol", "time"]).reset_index(drop=True)

    print(f"Lưu dữ liệu ra file {csv_file} (tổng {len(full_df)} dòng)...")
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


def get_vnindex(client: YuantaClient, start_date: str, end_date: str) -> pd.DataFrame:
    """Lấy dữ liệu chỉ số VNINDEX từ Yuanta API."""
    print("Lấy dữ liệu chỉ số VNINDEX từ Yuanta API...")
    df = client.get_price_history("VNINDEX", start_date, end_date)
    if df.empty:
        return pd.DataFrame()
    return df[["time", "close"]].rename(columns={"close": "VNINDEX"})


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

    df = pd.merge(vnindex_df, daily_stats, on="time", how="inner")
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
            line=dict(color="purple", width=2),
        ),
        row=1 if has_foreign else None,
        col=1 if has_foreign else None,
        secondary_y=False,
    )

    # Tín hiệu Mua/Bán kết hợp Breadth + Foreign Flow
    buy_signals = df[df["breadth_buy_cross"] & ((~has_foreign) | (df["foreign_net_val"] >= 0))]
    sell_signals = df[df["breadth_sell_cross"] & ((~has_foreign) | (df["foreign_net_val"] <= 0))]

    if not buy_signals.empty:
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

    if not sell_signals.empty:
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
        title_text="VN-Index (Index Points)",
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
                vnindex = get_vnindex(client, fetch_start_date, end_date)
                foreign_df = get_vnindex_foreign(client, fetch_start_date, end_date)

                if not vnindex.empty:
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
                    print("Hoàn thành bước build!")
                else:
                    print("Không có dữ liệu VNINDEX để vẽ biểu đồ.")
            else:
                print("Dữ liệu cổ phiếu trống.")
        else:
            print("Không có danh sách cổ phiếu hợp lệ.")

    if run_send:
        send_email_with_pdfs(pdf_files)
        print("Hoàn thành bước gửi email!")
