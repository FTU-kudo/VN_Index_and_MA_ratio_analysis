"""
build_pages.py
==============
Post-processes output/market_breadth_chart.html thành docs/index.html
để phục vụ qua GitHub Pages.

Bổ sung vào trang:
  - "Dữ liệu xuất bản lúc ..."  (baked-in từ CI/CD, tính theo UTC+7)
  - "Bạn truy cập lúc ..."       (JavaScript động, hiển thị UTC+7 trong trình duyệt)

Chạy:
    python build_pages.py
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SRC  = Path("output/market_breadth_chart.html")
DEST = Path("docs/index.html")
VN   = timezone(timedelta(hours=7))   # UTC+7


# --------------------------------------------------------------------------- #
#  HTML snippet: timestamp bar + JS access time                               #
# --------------------------------------------------------------------------- #
TIMESTAMP_SNIPPET = """\
<!-- ── GitHub Pages timestamp bar ── -->
<div id="vn-timestamp-bar" style="
    margin: 0; padding: 10px 0 20px;
    text-align: center;
    font-size: 12.5px;
    color: #777;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    border-top: 1px solid #eee;
    background: #fafafa;
">
  📅&nbsp;Dữ liệu xuất bản:&nbsp;<strong>{published}</strong>&nbsp;(GMT+7)
  &emsp;|&emsp;
  🕐&nbsp;Bạn truy cập lúc:&nbsp;<strong><span id="vn-access-time">…</span></strong>
</div>
<script>
/* Hiển thị giờ truy cập theo UTC+7, độc lập với timezone máy người dùng */
(function () {{
  function toVN(d) {{
    var ms  = d.getTime() + d.getTimezoneOffset() * 60000 + 7 * 3600000;
    var vn  = new Date(ms);
    var pad = function (n) {{ return ('0' + n).slice(-2); }};
    return pad(vn.getDate()) + '/' + pad(vn.getMonth() + 1) + '/' + vn.getFullYear()
         + ' ' + pad(vn.getHours()) + ':' + pad(vn.getMinutes()) + ':' + pad(vn.getSeconds())
         + ' (GMT+7)';
  }}
  var el = document.getElementById('vn-access-time');
  if (el) el.textContent = toVN(new Date());
}})();
</script>
<!-- ── end timestamp bar ── -->
</body>"""


def main():
    if not SRC.exists():
        print(f"LỖI: {SRC} chưa tồn tại — hãy chạy `python analysis.py --build` trước.")
        sys.exit(1)

    published = datetime.now(VN).strftime("%d/%m/%Y %H:%M:%S")
    html      = SRC.read_text(encoding="utf-8")

    snippet = TIMESTAMP_SNIPPET.format(published=published)

    if "</body>" in html:
        html = html.replace("</body>", snippet, 1)
    else:
        # Fallback: HTML không có </body> (hiếm gặp với Plotly)
        html += "\n" + snippet.replace("</body>", "")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(html, encoding="utf-8")
    print(f"✅  docs/index.html đã tạo  —  xuất bản: {published} (GMT+7)")


if __name__ == "__main__":
    main()
