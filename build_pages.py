"""
build_pages.py
==============
Post-processes output/market_breadth_chart.html → docs/index.html
for GitHub Pages deployment.

Adds:
  ① Browser tab title
  ② Top control bar:
       - Checkboxes to show/hide MA10 / MA20 / MA50 / MA200
       - User access time (UTC+7, computed in browser via JavaScript)
  ③ Footer: "📅 Data published at: ... (UTC+7) — © FTU-Kudo"

Usage:
    python build_pages.py
"""

import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SRC  = Path("output/market_breadth_chart.html")
DEST = Path("docs/index.html")
VN   = timezone(timedelta(hours=7))

PAGE_TITLE = (
    "Market Breadth: VN-Index and the Ratio of Stocks "
    "Trading Above Moving Averages (MA10, MA20, MA50, MA200)"
)


# ── Layout: full-viewport, topbar + chart + footer ────────────────────────
STYLE_BLOCK = """\
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100vh; overflow: hidden; }
  body { display: flex; flex-direction: column; }
  #vn-topbar  { flex-shrink: 0; }
  #main-content {
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden;
  }
  #main-content .js-plotly-plot,
  #main-content .plotly-graph-div {
    height: 100% !important;
    width:  100% !important;
  }
  .vn-footer { flex-shrink: 0; }
</style>
"""


# ── ① Top control bar (inserted right after <body ...>) ───────────────────
TOP_BAR = """\
<div id="vn-topbar" style="
    display:flex; justify-content:space-between; align-items:center;
    padding:10px 24px; background:#f7f7f7; border-bottom:1px solid #ddd;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
    font-size:13px; flex-wrap:wrap; gap:10px;">

  <!-- MA checkboxes -->
  <div style="display:flex; align-items:center; gap:18px; flex-wrap:wrap;">
    <span style="font-weight:600; color:#333;">&#x2705; ✅ Show MA lines:</span>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma10"  checked
             style="width:15px;height:15px;cursor:pointer;accent-color:#17becf;">
      <span style="color:#17becf;font-weight:700;">&#9644; MA10</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma20"  checked
             style="width:15px;height:15px;cursor:pointer;accent-color:#d62728;">
      <span style="color:#d62728;font-weight:700;">&#9644; MA20</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma50"  checked
             style="width:15px;height:15px;cursor:pointer;accent-color:#2ca02c;">
      <span style="color:#2ca02c;font-weight:700;">&#9644; MA50</span>
    </label>

    <label style="display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma200" checked
             style="width:15px;height:15px;cursor:pointer;accent-color:#ff7f0e;">
      <span style="color:#ff7f0e;font-weight:700;">&#9644; MA200</span>
    </label>
  </div>

  <!-- Access time -->
  <div style="color:#555; font-size:12.5px; white-space:nowrap;">
    &#128336;&nbsp;&#x1F550; 🕐 Accessed at:&nbsp;<strong><span id="vn-access-time">&#8211;</span></strong>
  </div>

</div>
"""


# ── ② Footer + JavaScript (inserted before </body>) ───────────────────────
# NOTE: {published} is replaced via str.replace(), NOT .format(),
# so JavaScript curly braces need no escaping.
#
# IMPORTANT: This string is passed to re.sub() via a lambda (not directly),
# so sequences like \d from JavaScript will NOT trigger re.error.
BOTTOM_SNIPPET = """\
<!-- ── Injected by build_pages.py ── -->
<div class="vn-footer" style="
    text-align:center; padding:8px 0 18px; margin-top:4px;
    font-size:11.5px; color:#888; background:#fafafa; border-top:1px solid #eee;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  &#128197;&nbsp;&#x1F4C5; 📅 Data published at:&nbsp;<strong>{published}</strong>&nbsp;(UTC+7)
  &nbsp;&mdash;&nbsp;&#169;&nbsp;FTU-Kudo
</div>

<script>
/* ── 1. Access time in UTC+7 (independent of user's local timezone) ── */
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

/* ── 2. Bind MA checkboxes → Plotly.restyle ── */
(function () {
  var MA_MAP = [
    { id: 'cb-ma10',  kw: 'MA10'  },
    { id: 'cb-ma20',  kw: 'MA20'  },
    { id: 'cb-ma50',  kw: 'MA50'  },
    { id: 'cb-ma200', kw: 'MA200' }
  ];

  function findTraceIndices(gdiv, kw) {
    /*
     * Match traces whose name contains the keyword (e.g. "MA20") but is NOT
     * immediately followed by another digit.
     * This distinguishes "MA20" from "MA200" without any regex escaping.
     */
    var indices = [];
    (gdiv.data || []).forEach(function (trace, i) {
      if (!trace.name) return;
      var name  = trace.name.toUpperCase();
      var pos   = name.indexOf(kw);
      if (pos === -1) return;
      var after = name[pos + kw.length];
      if (!after || !/\d/.test(after)) {
        indices.push(i);
      }
    });
    return indices;
  }

  function bindCheckboxes(gdiv) {
    MA_MAP.forEach(function (ma) {
      var cb = document.getElementById(ma.id);
      if (!cb) return;
      cb.addEventListener('change', function () {
        if (typeof Plotly === 'undefined') return;
        var indices = findTraceIndices(gdiv, ma.kw);
        if (indices.length > 0) {
          Plotly.restyle(gdiv, { visible: [cb.checked ? true : 'legendonly'] }, indices);
        }
      });
    });
  }

  /* Poll every 100 ms until Plotly has rendered (max 10 s) */
  var tries = 0;
  var timer = setInterval(function () {
    var gd = document.querySelector('.js-plotly-plot');
    if (gd && gd.data && gd.data.length > 0) {
      clearInterval(timer);
      bindCheckboxes(gd);
    }
    if (++tries > 100) clearInterval(timer);
  }, 100);
})();
</script>
<!-- ── end injection ── -->
</body>"""


def main():
    if not SRC.exists():
        print(f"ERROR: {SRC} not found — run `python analysis.py --build` first.")
        sys.exit(1)

    published = datetime.now(VN).strftime("%d/%m/%Y %H:%M:%S")
    html      = SRC.read_text(encoding="utf-8")

    # 0. Update (or add) <title> tag
    if re.search(r"<title>", html, re.IGNORECASE):
        html = re.sub(
            r"<title>[^<]*</title>",
            lambda m: f"<title>{PAGE_TITLE}</title>",    # lambda: tránh re xử lý replacement
            html, count=1, flags=re.IGNORECASE,
        )
    else:
        html = re.sub(
            r"(<head[^>]*>)",
            lambda m: m.group(1) + f"\n  <title>{PAGE_TITLE}</title>",
            html, count=1, flags=re.IGNORECASE,
        )

    # 1. Insert STYLE + TOP_BAR + open #main-content right after <body ...>
    # ── FIX: dùng lambda thay vì string trực tiếp ──────────────────────────
    # re.sub() xử lý replacement string như regex template (\1, \d, \n …),
    # lambda trả về string thuần → tránh hoàn toàn mọi bad-escape error.
    inject_top = STYLE_BLOCK + TOP_BAR + '<div id="main-content">'
    html = re.sub(
        r"(<body[^>]*>)",
        lambda m: m.group(1) + "\n" + inject_top,        # lambda ← FIX
        html, count=1, flags=re.IGNORECASE,
    )

    # 2. Close #main-content, insert footer + script before </body>
    # ── FIX: lambda tránh \d trong JS bị re.sub hiểu nhầm ─────────────────
    bottom = "</div>\n" + BOTTOM_SNIPPET.replace("{published}", published)
    if re.search(r"</body>", html, re.IGNORECASE):
        html = re.sub(
            r"</body>",
            lambda m: bottom,                             # lambda ← FIX
            html, count=1, flags=re.IGNORECASE,
        )
    else:
        html += "\n" + bottom.replace("</body>", "")

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(html, encoding="utf-8")
    print(f"OK  docs/index.html created  —  published: {published} (UTC+7)")


if __name__ == "__main__":
    main()
