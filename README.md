# 📊 VN-Index & MA Ratio Analysis

> Tự động phân tích tương quan giữa VN-Index và tỷ lệ cổ phiếu vượt các đường trung bình động (MA10/20/50/200) trên sàn HOSE — cập nhật hàng tuần, gửi báo cáo PDF qua email tự động.

## Giới thiệu

Project theo dõi **"market breadth"** (độ rộng thị trường) của HOSE:

1. Lấy dữ liệu giá lịch sử của toàn bộ ~400 mã cổ phiếu niêm yết trên HOSE qua [vnstock](https://github.com/thinh-vu/vnstock)
2. Tính các đường MA10, MA20, MA50, MA200 cho từng mã
3. Tính tỷ lệ % số mã đang giao dịch **trên** mỗi đường MA tại từng thời điểm
4. Vẽ biểu đồ tương quan giữa VN-Index và các tỷ lệ này (Plotly, xuất HTML tương tác + PDF)
5. Tự động gửi báo cáo PDF qua email hàng tuần

## 🖼️ Output mẫu

<img width="1466" height="987" alt="image" src="https://github.com/user-attachments/assets/fcd8f7bc-86b2-4b63-994b-630bfb13adbd" />


## 🏗️ Kiến trúc

```
vnstock API → pandas (tính MA & tỷ lệ %) → Plotly (vẽ chart HTML/PDF) → Gmail SMTP (gửi email)
                                                      │
                              GitHub Actions: job "build" → job "send_email"
                                                      │
              cron-job.org ──(POST /repos/.../dispatches)──► mỗi Chủ nhật 20:00 GMT+7
```

Pipeline tách thành 2 bước độc lập, có thể chạy riêng hoặc nối tiếp:

```bash
python analysis.py --build         # tải dữ liệu, tính toán, vẽ biểu đồ
python analysis.py --send-email    # gửi email với PDF đã build
```

**Vì sao dùng cron-job.org** thay vì `schedule:` có sẵn của GitHub Actions? GitHub có thể delay hoặc bỏ qua scheduled job (đặc biệt với repo ít hoạt động). Dùng dịch vụ cron ngoài gọi GitHub Dispatches API (`repository_dispatch`) đảm bảo workflow được kích hoạt đúng giờ.

**Vì sao cache bằng GitHub Actions cache, không commit CSV vào git?** Dữ liệu OHLC (`hose_6years_data.csv`, ~20MB+) cần tồn tại giữa các lần chạy để chỉ tải bổ sung phần dữ liệu mới (incremental fetch) thay vì tải lại toàn bộ 6 năm mỗi tuần (~20 phút) — nhưng commit file này vào git sẽ làm phình lịch sử repo theo thời gian. `actions/cache` giải quyết được cả hai: vừa giữ tốc độ, vừa giữ repo sạch.

## ⚙️ Cài đặt & chạy local

```bash
git clone https://github.com/FTU-kudo/VN_Index_and_MA_ratio_analysis.git
cd VN_Index_and_MA_ratio_analysis
pip install -r requirements.txt
cp .env.example .env   # điền VNSTOCK_API_KEY / Gmail App Password của bạn
python analysis.py     # chạy full: build + gửi email
```

## 📁 Cấu trúc thư mục

| Đường dẫn | Nội dung |
|---|---|
| `analysis.py` | Script chính: tải data, tính MA & market breadth, vẽ chart, gửi email |
| `.github/workflows/` | GitHub Actions workflow (job `build` + job `send_email`) |
| `notebooks/` | Notebook khám phá dữ liệu / thử nghiệm thư viện vnstock |
| `test_*.py` | Script thử nhanh các API của vnstock trong lúc dev (không phải unit test chính thức) |
| `docs/`, `AGENTS.md`, `CLAUDE.md`, `.agents/` | Tài liệu vendor từ [vnstock-agent-guide](https://github.com/vnstock-hq/vnstock-agent-guide) — giúp AI coding agent (Claude, Copilot, Cursor...) dùng đúng API của thư viện vnstock khi hỗ trợ code. **Không phải tài liệu mô tả project này.** |

## 🛠️ Công nghệ sử dụng

`Python` · `pandas` · `vnstock` · `Plotly` + `kaleido` (xuất PDF) · `GitHub Actions` · `cron-job.org` · `Gmail SMTP`

## 📄 License

© FTU-Kudo
