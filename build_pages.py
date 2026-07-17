"""
build_pages.py
==============
Post-processes output/market_breadth_chart.html → docs/index.html
cho GitHub Pages.

Bổ sung vào trang:
  ① Thanh điều khiển đầu trang:
       - Ô tick [✓] MA10  [✓] MA20  [✓] MA50  [✓] MA200  (click để ẩn/hiện từng đường)
       - Giờ truy cập web của người dùng (UTC+7, do JavaScript tính trong trình duyệt)
  ② Dòng cuối trang: "📅 Dữ liệu xuất bản lúc: dd/mm/yyyy HH:MM:SS (GMT+7) — © Bản quyền thuộc về FTU-Kudo"  ← baked-in từ CI

Chạy:
    python build_pages.py
"""

import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SRC  = Path("output/market_breadth_chart.html")
DEST = Path("docs/index.html")
VN   = timezone(timedelta(hours=7))


# ── ① Thanh điều khiển (chèn ngay sau thẻ <body ...>) ─────────────────────
TOP_BAR = """\
<div id="vn-topbar" style="
    display:flex; justify-content:space-between; align-items:center;
    padding:10px 24px; background:#f7f7f7; border-bottom:1px solid #ddd;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
    font-size:13px; flex-wrap:wrap; gap:10px;">

  <!-- Ô tick chọn đường MA -->
  <div style="display:flex; align-items:center; gap:18px; flex-wrap:wrap;">
    <span style="font-weight:600; color:#333;">Hiển thị đường MA:</span>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma10"  checked style="width:15px;height:15px;cursor:pointer;accent-color:#17becf;">
      <span style="color:#17becf; font-weight:700;">&#9644; MA10</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma20"  checked style="width:15px;height:15px;cursor:pointer;accent-color:#d62728;">
      <span style="color:#d62728; font-weight:700;">&#9644; MA20</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma50"  checked style="width:15px;height:15px;cursor:pointer;accent-color:#2ca02c;">
      <span style="color:#2ca02c; font-weight:700;">&#9644; MA50</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma200" checked style="width:15px;height:15px;cursor:pointer;accent-color:#ff7f0e;">
      <span style="color:#ff7f0e; font-weight:700;">&#9644; MA200</span>
    </label>
  </div>

  <!-- Giờ truy cập -->
  <div style="color:#555; font-size:12.5px; white-space:nowrap;">
    🕐&nbsp;Bạn truy cập lúc:&nbsp;<strong><span id="vn-access-time">–</span></strong>
  </div>

</div>
"""

# ── ② Script + dòng xuất bản (chèn trước </body>) ─────────────────────────
BOTTOM_SNIPPET = """\
<!-- ── Injected by build_pages.py ── -->
<div style="
    text-align:center; padding:8px 0 18px; margin-top:4px;
    font-size:11.5px; color:#888; background:#fafafa; border-top:1px solid #eee;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  📅&nbsp;Dữ liệu xuất bản lúc:&nbsp;<strong>{published}</strong>&nbsp;(GMT+7) — © Bản quyền thuộc về FTU-Kudo
</div>

<script>
/* ── 1. Hiển thị giờ truy cập theo UTC+7 ── */
(function () {
  function toVN(d) {
    var ms = d.getTime() + d.getTimezoneOffset() * 60000 + 7 * 3600000;
    var v  = new Date(ms);
    var p  = function (n) { return ('0' + n).slice(-2); };
    return p(v.getDate()) + '/' + p(v.getMonth() + 1) + '/' + v.getFullYear()
         + ' ' + p(v.getHours()) + ':' + p(v.getMinutes()) + ':' + p(v.getSeconds())
         + ' (GMT+7)';
  }
  var el = document.getElementById('vn-access-time');
  if (el) el.textContent = toVN(new Date());
})();

/* ── 2. Gắn ô tick MA → Plotly.restyle (ẩn/hiện trace) ── */
(function () {
  // Định nghĩa từ khóa tìm kiếm trong tên trace
  var MA_MAP = [
    { id: 'cb-ma10',  kw: 'ma10'  },
    { id: 'cb-ma20',  kw: 'ma20'  },
    { id: 'cb-ma50',  kw: 'ma50'  },
    { id: 'cb-ma200', kw: 'ma200' }
  ];

  // Hàm gán sự kiện cho checkbox
  function bindCheckboxes(gdiv) {
    // In ra tên các trace để debug
    console.log('Danh sách trace names trong biểu đồ:');
    (gdiv.data || []).forEach(function (trace, i) {
      console.log(i + ': ' + (trace.name || '(không có tên)'));
    });

    MA_MAP.forEach(function (ma) {
      var cb = document.getElementById(ma.id);
      if (!cb) {
        console.warn('⚠️ Không tìm thấy checkbox #' + ma.id);
        return;
      }

      cb.addEventListener('change', function () {
        console.log('Checkbox ' + ma.id + ' thay đổi -> checked =', cb.checked);

        if (typeof Plotly === 'undefined') {
          console.warn('⚠️ Plotly chưa được định nghĩa.');
          return;
        }

        // Tìm các trace có tên chứa từ khóa (không phân biệt hoa thường)
        // Dùng regex với word boundary để tránh nhầm MA200 với MA20
        var regex = new RegExp('\\\\b' + ma.kw + '\\\\b', 'i');
        var indices = [];
        (gdiv.data || []).forEach(function (trace, i) {
          if (trace.name && regex.test(trace.name)) {
            indices.push(i);
          }
        });

        console.log('Tìm thấy ' + indices.length + ' trace cho từ khóa "' + ma.kw + '" : indices =', indices);

        if (indices.length > 0) {
          Plotly.restyle(gdiv, { visible: [cb.checked ? true : 'legendonly'] }, indices);
        } else {
          console.warn('⚠️ Không tìm thấy trace nào cho "' + ma.kw + '". Kiểm tra lại tên trace.');
        }
      });
    });
  }

  /* Poll 100 ms cho đến khi Plotly render xong và gdiv.data có dữ liệu (tối đa 10 s) */
  var tries = 0;
  var timer = setInterval(function () {
    var gd = document.querySelector('.js-plotly-plot');
    if (gd && gd.data && gd.data.length > 0) {
      clearInterval(timer);
      console.log('✅ Biểu đồ đã sẵn sàng, tiến hành gắn sự kiện checkbox.');
      bindCheckboxes(gd);
      return;
    }
    if (++tries > 100) {
      clearInterval(timer);
      console.warn('⚠️ Không tìm thấy biểu đồ Plotly sau 10 giây.');
    }
  }, 100);
})();
</script>
<!-- ── end injection ── -->
</body>"""


def main():
    if not SRC.exists():
        print(f"LỖI: {SRC} chưa tồn tại — hãy chạy `python analysis.py --build` trước.")
        sys.exit(1)

    published = datetime.now(VN).strftime("%d/%m/%Y %H:%M:%S")
    html      = SRC.read_text(encoding="utf-8")

    # 1. Chèn TOP_BAR ngay sau thẻ <body ...> (hỗ trợ <body> và <body class="..."> v.v.)
    html = re.sub(
        r"(<body[^>]*>)",
        r"\1\n" + TOP_BAR,
        html,
        count=1,
        flags=re.IGNORECASE,
    )

    # 2. Thay placeholder {published} rồi chèn trước </body>
    bottom = BOTTOM_SNIPPET.replace("{published}", published)
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(r"</body>", bottom, html, count=1, flags=re.IGNORECASE)
    else:
        # Fallback: HTML không có </body>
        html += "\n" + bottom.replace("</body>", "")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(html, encoding="utf-8")
    print(f"✅  docs/index.html đã tạo  —  xuất bản: {published} (GMT+7)")


if __name__ == "__main__":
    main()
