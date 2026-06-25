import pandas as pd
import time
import os
import sys
import argparse
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
from vnstock import Quote, Listing
try:
    from vnstock.core.utils.auth import change_api_key
except ImportError:
    change_api_key = None
import plotly.graph_objects as plotly_go

# Nạp cấu hình từ file .env (nếu có)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

vnstock_api_key = os.getenv("VNSTOCK_API_KEY")
if vnstock_api_key and change_api_key:
    try:
        change_api_key(vnstock_api_key)
        print("Đã nạp VNSTOCK_API_KEY thành công!")
    except Exception as e:
        print(f"Lỗi khi thiết lập API key: {e}")
from plotly.subplots import make_subplots
import math
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime

def get_hose_symbols():
    print("Bước 1: Lấy danh sách cổ phiếu HOSE...")
    try:
        listing = Listing(source="kbs")
        hose_df = listing.symbols_by_exchange(exchange="HOSE", to_df=True)
        # KBS symbols_by_exchange có thể trả về cả các sàn khác, nên cần filter thủ công
        if 'exchange' in hose_df.columns:
            hose_df = hose_df[hose_df['exchange'].str.upper() == 'HOSE']
            
        if 'type' in hose_df.columns:
            hose_df = hose_df[hose_df['type'].str.lower() == 'stock']
            
        symbols = hose_df['symbol'].tolist()
        symbols = [s for s in symbols if len(str(s)) == 3 and str(s).isalpha()]
        print(f"Tổng số cổ phiếu HOSE hợp lệ: {len(symbols)}")
        return symbols
    except Exception as e:
        print(f"Lỗi khi lấy danh sách cổ phiếu: {e}")
        return []

def get_historical_data(symbols, start_date, end_date):
    csv_file = "hose_6years_data.csv"
    cached_df = None
    fetch_start = start_date

    if os.path.exists(csv_file):
        try:
            cached_df = pd.read_csv(csv_file)
            cached_df['time'] = pd.to_datetime(cached_df['time']).dt.date
            last_date = cached_df['time'].max()
            # Lùi lại 7 ngày để bù các phiên nghỉ/giá điều chỉnh, chỉ tải phần dữ liệu mới
            fetch_start = (pd.to_datetime(last_date) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            print(f"Đã có cache đến {last_date}. Chỉ tải bổ sung từ {fetch_start}...")
        except Exception as e:
            print("Lỗi khi đọc file CSV cache, sẽ tải lại toàn bộ:", e)
            cached_df = None

    print(f"Bước 2: Tải dữ liệu OHLC từ {fetch_start} đến {end_date}...")
    if vnstock_api_key:
        print(f"Lưu ý: Đã kích hoạt API Key. Tốc độ tải: ~60 requests/minute (sẽ mất khoảng 7 phút).")
    else:
        print(f"Lưu ý: Tài khoản Guest - Rate Limit (20 requests/minute), tiến trình sẽ tải từ từ (mất khoảng 20 phút).")
    all_data = []
    total = len(symbols)
    
    for i, symbol in enumerate(symbols):
        try:
            q = Quote(symbol=symbol)
            df = q.history(start=fetch_start, end=end_date, interval="1D")
            
            if df is not None and not df.empty:
                df['symbol'] = symbol
                all_data.append(df)
            
            # Print tiến độ
            if (i + 1) % 10 == 0 or (i + 1) == total:
                print(f"Đã tải {i + 1}/{total} mã cổ phiếu...")
                
            # Điều chỉnh tốc độ tải dựa trên việc có API key hay không
            if vnstock_api_key:
                time.sleep(1.1)  # 60 requests/phút (dư 0.1s an toàn)
            else:
                time.sleep(3.2)  # 20 requests/phút cho tài khoản Guest
        except Exception as e:
            print(f"Lỗi khi tải dữ liệu mã {symbol}: {e}")
            time.sleep(5) # Nghỉ dài hơn nếu gặp lỗi
            
    if not all_data:
        print("Không tải được dữ liệu mới.")
        return cached_df if cached_df is not None else pd.DataFrame()
        
    new_df = pd.concat(all_data, ignore_index=True)
    new_df['time'] = pd.to_datetime(new_df['time']).dt.date

    if cached_df is not None:
        full_df = pd.concat([cached_df, new_df], ignore_index=True)
    else:
        full_df = new_df

    # Loại bỏ trùng lặp (giữ dữ liệu mới nhất cho mỗi symbol+ngày), sắp xếp lại
    full_df = full_df.drop_duplicates(subset=['symbol', 'time'], keep='last')
    full_df = full_df.sort_values(by=['symbol', 'time']).reset_index(drop=True)
    
    print(f"Lưu dữ liệu ra file {csv_file} (tổng {len(full_df)} dòng)...")
    full_df.to_csv(csv_file, index=False)
    return full_df

def process_data(full_df):
    print("Bước 3 & 4: Tính toán các chỉ số MA và Tỷ lệ market breadth...")
    # Sắp xếp để đảm bảo tính rolling chính xác
    full_df = full_df.sort_values(by=['symbol', 'time'])
    
    # Tính các đường MA
    print("Đang tính MA10, 20, 50, 200...")
    full_df['MA10'] = full_df.groupby('symbol')['close'].transform(lambda x: x.rolling(10).mean())
    full_df['MA20'] = full_df.groupby('symbol')['close'].transform(lambda x: x.rolling(20).mean())
    full_df['MA50'] = full_df.groupby('symbol')['close'].transform(lambda x: x.rolling(50).mean())
    full_df['MA200'] = full_df.groupby('symbol')['close'].transform(lambda x: x.rolling(200).mean())
    
    # Xác định cổ phiếu có giá > MA10, MA20, MA50, MA200
    full_df['>MA10'] = full_df['close'] > full_df['MA10']
    full_df['>MA20'] = full_df['close'] > full_df['MA20']
    full_df['>MA50'] = full_df['close'] > full_df['MA50']
    full_df['>MA200'] = full_df['close'] > full_df['MA200']
    
    # Gom nhóm theo ngày để thống kê tỷ lệ
    print("Đang tính toán tỷ lệ tỷ lệ cổ phiếu lớn hơn MA...")
    daily_stats = full_df.groupby('time').agg(
        total_stocks=('symbol', 'count'),
        valid_ma10=('MA10', lambda x: x.notna().sum()),    # Số cổ phiếu đủ data MA10
        valid_ma20=('MA20', lambda x: x.notna().sum()),    # Số cổ phiếu đủ data MA20
        valid_ma50=('MA50', lambda x: x.notna().sum()),    # Số cổ phiếu đủ data MA50
        valid_ma200=('MA200', lambda x: x.notna().sum()),  # Số cổ phiếu đủ data MA200
        gt_ma10=('>MA10', 'sum'),
        gt_ma20=('>MA20', 'sum'),
        gt_ma50=('>MA50', 'sum'),
        gt_ma200=('>MA200', 'sum')
    ).reset_index()
    
    # Chỉ tính tỷ lệ trên những cổ phiếu ĐÃ CÓ đủ dữ liệu để tạo đường MA
    daily_stats['pct_MA10'] = (daily_stats['gt_ma10'] / daily_stats['valid_ma10'] * 100).fillna(0)
    daily_stats['pct_MA20'] = (daily_stats['gt_ma20'] / daily_stats['valid_ma20'] * 100).fillna(0)
    daily_stats['pct_MA50'] = (daily_stats['gt_ma50'] / daily_stats['valid_ma50'] * 100).fillna(0)
    daily_stats['pct_MA200'] = (daily_stats['gt_ma200'] / daily_stats['valid_ma200'] * 100).fillna(0)
    
    return daily_stats

def get_vnindex(start_date, end_date):
    print("Lấy dữ liệu chỉ số VNINDEX...")
    try:
        q = Quote(symbol="VNINDEX")
        vnindex = q.history(start=start_date, end=end_date, interval="1D")
        vnindex['time'] = pd.to_datetime(vnindex['time']).dt.date
        return vnindex[['time', 'close']].rename(columns={'close': 'VNINDEX'})
    except Exception as e:
        print(f"Lỗi khi lấy dữ liệu VNINDEX: {e}")
        return pd.DataFrame()

def plot_market_breadth(daily_stats, vnindex_df, ma_lines, ma_label, output_file, plot_start_date="2021-06-25"):
    title = f"VN-Index và Tỷ lệ cổ phiếu vượt {ma_label}"
    print(f"Vẽ biểu đồ và lưu ra file HTML/PDF: {output_file}...")
    # Kết hợp dữ liệu
    df = pd.merge(vnindex_df, daily_stats, on='time', how='inner')
    
    # Lọc dữ liệu từ ngày bắt đầu vẽ
    plot_start = pd.to_datetime(plot_start_date).date()
    df = df[df['time'] >= plot_start]

    # Tạo subplot với 2 trục y (trục trái cho VNINDEX, trục phải cho Tỷ lệ %)
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Vẽ VNINDEX (line mầu tím)
    fig.add_trace(
        plotly_go.Scatter(
            x=df['time'], y=df['VNINDEX'], name="VNINDEX (điểm, cột trái)",
            line=dict(color='purple', width=2)
        ),
        secondary_y=False,
    )

    colors = {
        'pct_MA10': 'cyan',
        'pct_MA20': 'red',
        'pct_MA50': 'green',
        'pct_MA200': 'orange'
    }
    
    names = {
        'pct_MA10': 'MA10',
        'pct_MA20': 'MA20',
        'pct_MA50': 'MA50',
        'pct_MA200': 'MA200'
    }

    for ma in ma_lines:
        fig.add_trace(
            plotly_go.Scatter(
                x=df['time'], y=df[ma], name=f"Tỷ lệ mã > {names[ma]} (%, cột phải)",
                line=dict(color=colors[ma], width=1)
            ),
            secondary_y=True,
        )

    # Tùy chỉnh Layout
    fig.update_layout(
        title=title,
        title_font_size=24,
        font=dict(family="Inter, Roboto, Arial, sans-serif"),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.2,
            xanchor="center",
            x=0.5
        ),
        xaxis_title="Thời gian",
        hovermode="x unified",
        template="plotly_white",
        margin=dict(l=50, r=50, t=80, b=150)
    )

    # Tùy chỉnh các trục
    fig.update_yaxes(title_text="VN-Index (điểm)", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="Tỷ lệ (%)", secondary_y=True, range=[0, 100], ticksuffix="%", showgrid=True, gridcolor="gray", gridwidth=0.5, griddash="dot")

    # Thêm Annotation
    from datetime import datetime
    from zoneinfo import ZoneInfo 

    # Chủ động lấy giờ theo múi giờ Việt Nam
    current_time_str = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%Y-%m-%d %H:%M:%S")
    credit_text = "© Bản quyền thuộc về FTU-Kudo"
    
    fig.add_annotation(
        text=f"{credit_text} | Ngày cập nhật: {current_time_str}",
        xref="paper", yref="paper",
        x=1.0, y=-0.22,
        showarrow=False,
        font=dict(size=12, color="gray"),
        xanchor="right", yanchor="top"
    )

    fig.write_html(output_file)
    pdf_file = output_file.replace('.html', '.pdf')
    try:
        fig.write_image(pdf_file, format="pdf", width=1200, height=800, engine="kaleido")
    except Exception as e:
        print(f"Lỗi khi lưu PDF (có thể do thiếu kaleido): {e}")
        
    print(f"Đã lưu biểu đồ thành công vào: {output_file} và {pdf_file}")

def send_email_with_pdfs(pdf_files):
    sender = os.getenv("GMAIL_USER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    receiver = os.getenv("GMAIL_RECEIVER")
    
    if not sender or not password or not receiver:
        print("Không tìm thấy cấu hình Gmail trong biến môi trường. Bỏ qua gửi email.")
        return
        
    print(f"Đang gửi email đính kèm biểu đồ tới {receiver}...")
    try:
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = receiver
        msg['Subject'] = "Báo cáo phân tích Tương quan giữa VN-Index và Tỷ lệ mã cổ phiếu vượt các đường MA (cập nhật hàng tuần)"
        
        body = "Xin chào Quý khách,\n\nĐây là báo cáo phân tích tương quan giữa VN-Index và Tỷ lệ mã vượt các đường MA (MA10, MA20, MA50, MA200).\nXin Quý khách vui lòng xem các file PDF đính kèm.\n\nTrân trọng,\nFTU-Kudo."
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        
        for file in pdf_files:
            if os.path.exists(file):
                with open(file, "rb") as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(file))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(file)}"'
                msg.attach(part)
                
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        print("Đã gửi email thành công!")
    except Exception as e:
        print(f"Lỗi khi gửi email: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VN-Index & MA Ratio Analysis Pipeline")
    parser.add_argument("--build", action="store_true", help="Chỉ tải dữ liệu, tính toán & vẽ biểu đồ")
    parser.add_argument("--send-email", action="store_true", help="Chỉ gửi email với các PDF đã có sẵn")
    args = parser.parse_args()

    # Không truyền cờ nào -> chạy full pipeline (tiện cho test ở local)
    no_flag = not (args.build or args.send_email)
    run_build = args.build or no_flag
    run_send = args.send_email or no_flag

    fetch_start_date = "2020-06-25" # Lấy dữ liệu sớm hơn 1 năm để tính được MA200 từ 2021-06-25
    plot_start_date = "2021-06-25"
    end_date = datetime.now().strftime("%Y-%m-%d")  # Luôn lấy đến ngày chạy thực tế, không hard-code

    pdf_files = [
        "market_breadth_chart.pdf",
        "market_breadth_chart_ma10_ma20.pdf",
        "market_breadth_chart_ma50_ma200.pdf"
    ]

    if run_build:
        # 1. Lấy danh sách mã
        symbols = get_hose_symbols()

        if symbols:
            # 2. Lấy dữ liệu OHLC
            df = get_historical_data(symbols, fetch_start_date, end_date)

            if not df.empty:
                # 3 & 4. Xử lý dữ liệu
                stats = process_data(df)

                # 5. Lấy VNINDEX
                vnindex = get_vnindex(fetch_start_date, end_date)

                if not vnindex.empty:
                    # Vẽ biểu đồ
                    print("Bước 5: Vẽ biểu đồ và lưu ra file HTML/PDF...")
                    plot_market_breadth(stats, vnindex, ['pct_MA10', 'pct_MA20', 'pct_MA50', 'pct_MA200'], "các đường MA (MA10, MA20, MA50, MA200)", "market_breadth_chart.html", plot_start_date=plot_start_date)
                    plot_market_breadth(stats, vnindex, ['pct_MA10', 'pct_MA20'], "MA10 và MA20", "market_breadth_chart_ma10_ma20.html", plot_start_date=plot_start_date)
                    plot_market_breadth(stats, vnindex, ['pct_MA50', 'pct_MA200'], "MA50 và MA200", "market_breadth_chart_ma50_ma200.html", plot_start_date=plot_start_date)
                    print("Hoàn thành bước build!")
                else:
                    print("Không có dữ liệu VNINDEX để vẽ biểu đồ.")
            else:
                print("Dữ liệu cổ phiếu trống.")
        else:
            print("Không có danh sách cổ phiếu hợp lệ.")

    if run_send:
        # Gửi email
        send_email_with_pdfs(pdf_files)
        print("Hoàn thành bước gửi email!")
