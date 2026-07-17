"""
build_pages.py
==============
Post-processes output/market_breadth_chart.html → docs/index.html
cho GitHub Pages.

Bổ sung vào trang:
  ① Thanh điều khiển đầu trang:
       - Ô tick [✓] MA10  [✓] MA20  [✓] MA50  [✓] MA200  (click để ẩn/hiện từng đường)
       - Giờ truy cập web của người dùng (UTC+7, do JavaScript tính trong trình duyệt)
  ② Dòng cuối trang: "📅 Dữ liệu được xuất bản lúc: dd/mm/yyyy HH:MM:SS (UTC+7) — © Bản quyền thuộc về FTU-Kudo"

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


# ── Style để trang vừa vặn màn hình, footer luôn hiển thị ────────────────
STYLE_BLOCK = """\
<style>
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    padding: 0;
    height: 100vh;
    overflow: hidden;
  }
  body {
    display: flex;
    flex-direction: column;
  }
  #vn-topbar {
    flex-shrink: 0;
  }
  #main-content {
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
  }
  #main-content .js-plotly-plot,
  #main-content .plotly-graph-div {
    height: 100% !important;
    width: 100% !important;
  }
  .vn-footer {
    flex-shrink: 0;
  }
</style>
"""


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
    🕐&nbsp;Đã truy cập lúc:&nbsp;<strong><span id="vn-access-time">–</span></strong>
  </div>

</div>
"""

# ── ② Script + dòng xuất bản (chèn trước </body>) ─────────────────────────
BOTTOM_SNIPPET = """\
<!-- ── Injected by build_pages.py ── -->
<div class="vn-footer" style="
    text-align:center; padding:8px 0 18px; margin-top:4px;
    font-size:11.5px; color:#888; background:#fafafa; border-top:1px solid #eee;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  📅&nbsp;Dữ liệu được xuất bản lúc:&nbsp;<strong>{published}</strong>&nbsp;(UTC+7) — © Bản quyền thuộc về FTU-Kudo
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
         + ' (UTC+7)';
  }
  var el = document.getElementById('vn-access-time');
  if (el) el.textContent = toVN(new Date());
})();

/* ── 2. Gắn ô tick MA → Plotly.restyle (ẩn/hiện trace) ── */
(function () {
  console.log('🔍 Script MA checkbox đang khởi tạo...');

  var MA_MAP = [
    { id: 'cb-ma10',  kw: 'ma10'  },
    { id: 'cb-ma20',  kw: 'ma20'  },
    { id: 'cb-ma50',  kw: 'ma50'  },
    { id: 'cb-ma200', kw: 'ma200' }
  ];

  function bindCheckboxes(gdiv) {
    console.log('✅ Đã tìm thấy biểu đồ, bắt đầu gắn sự kiện.');
    console.log('📊 Danh sách trace names:');
    if (gdiv.data && gdiv.data.length > 0) {
      (gdiv.data).forEach(function (trace, i) {
        console.log('  ' + i + ': ' + (trace.name || '(không tên)'));
      });
    } else {
      console.warn('⚠️ Biểu đồ không có dữ liệu');
    }

    MA_MAP.forEach(function (ma) {
      var cb = document.getElementById(ma.id);
      if (!cb) {
        console.warn('⚠️ Không tìm thấy checkbox #' + ma.id);
        return;
      }

      cb.addEventListener('change', function () {
        console.log('🔄 Checkbox ' + ma.id + ' changed -> checked = ' + cb.checked);

        if (typeof Plotly === 'undefined') {
          console.warn('⚠️ Plotly chưa sẵn sàng.');
          return;
        }

        // SỬA LỖI: dùng regex với word boundary (\\b) để chỉ match đúng từ khóa
        // Python string: 8 dấu backslash -> JS nhận được '\\b' + kw + '\\b'
        var regex = new RegExp('\\\\\\\\b' + ma.kw + '\\\\\\\\b', 'i');
        var indices = [];
        (gdiv.data || []).forEach(function (trace, i) {
          if (trace.name && regex.test(trace.name)) {
            indices.push(i);
          }
        });

        console.log('🔎 Tìm thấy ' + indices.length + ' trace cho từ khóa "' + ma.kw + '":', indices);

        if (indices.length > 0) {
          Plotly.restyle(gdiv, { visible: [cb.checked ? true : 'legendonly'] }, indices);
        } else {
          console.warn('⚠️ Không tìm thấy trace nào cho "' + ma.kw + '"');
        }
      });
    });
  }

  /* Poll để đợi Plotly render */
  var tries = 0;
  var timer = setInterval(function () {
    var gd = document.querySelector('.js-plotly-plot') || document.querySelector('div.plotly-graph-div') || document.querySelector('.plotly');
    if (gd && gd.data && gd.data.length > 0) {
      clearInterval(timer);
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

    # 1. Chèn STYLE_BLOCK, TOP_BAR và mở div#main-content ngay sau thẻ <body>
    html = re.sub(
        r"(<body[^>]*>)",
        r"\1\n" + STYLE_BLOCK + TOP_BAR + '<div id="main-content">',
        html,
        count=1,
        flags=re.IGNORECASE,
    )

    # 2. Đóng div#main-content và chèn footer + script trước </body>
    bottom = "</div>" + BOTTOM_SNIPPET.replace("{published}", published)
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(r"</body>", bottom, html, count=1, flags=re.IGNORECASE)
    else:
        html += "\n" + bottom.replace("</body>", "")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(html, encoding="utf-8")
    print(f"✅  docs/index.html đã tạo  —  xuất bản: {published} (UTC+7)")


if __name__ == "__main__":
    main()
