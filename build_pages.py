"""
build_pages.py
==============
Post-processes output/market_breadth_chart.html (+ output/breadth_groups.json)
→ docs/index.html for GitHub Pages deployment.

Features:
  ① Browser tab title
  ② Top control bar:
       - Sector / Index group selector (HOSE, VN30, VN100, VNREAL, VNFIN, ...)
       - Checkboxes to show/hide MA10 / MA20 / MA50 / MA200
       - Light / Dark mode toggle
       - Language toggle: Tiếng Việt (mặc định) / English
       - User access time (UTC+7, computed in browser via JavaScript)
  ③ Footer: "📅 Data published at: ... (UTC+7) — © FTU-Kudo"

Usage:
    python build_pages.py
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path("output/market_breadth_chart.html")
GROUPS_JSON = Path("output/breadth_groups.json")
DEST = Path("docs/index.html")
VN = timezone(timedelta(hours=7))

PAGE_TITLE = (
    "Market Breadth: VN-Index & MA Ratio Analysis "
    "(MA10, MA20, MA50, MA200)"
)

STYLE_BLOCK = """\
<style>
  :root {
    --bg-page: #ffffff;
    --bg-bar: #f7f7f7;
    --bg-footer: #fafafa;
    --border-color: #dddddd;
    --text-main: #222222;
    --text-muted: #555555;
    --text-footer: #888888;
    --control-bg: #ffffff;
    --control-border: #cccccc;
  }
  body.dark-mode {
    --bg-page: #111827;
    --bg-bar: #1f2937;
    --bg-footer: #1f2937;
    --border-color: #374151;
    --text-main: #f3f4f6;
    --text-muted: #d1d5db;
    --text-footer: #9ca3af;
    --control-bg: #374151;
    --control-border: #4b5563;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; height: 100vh; overflow: hidden;
    background: var(--bg-page); color: var(--text-main);
    transition: background 0.2s, color 0.2s;
  }
  body { display: flex; flex-direction: column; }
  #vn-topbar {
    flex-shrink: 0;
    display: flex; justify-content: space-between; align-items: center;
    padding: 8px 20px; background: var(--bg-bar);
    border-bottom: 1px solid var(--border-color);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
    font-size: 13px; flex-wrap: wrap; gap: 10px;
  }
  .vn-ctrl-group { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .vn-select, .vn-btn {
    padding: 4px 10px; border-radius: 6px;
    border: 1px solid var(--control-border);
    background: var(--control-bg); color: var(--text-main);
    font-size: 12.5px; cursor: pointer;
  }
  .vn-btn:hover { opacity: 0.85; }
  #main-content {
    flex: 1 1 auto; min-height: 0; overflow: hidden;
  }
  #main-content .js-plotly-plot,
  #main-content .plotly-graph-div {
    height: 100% !important; width: 100% !important;
  }
  .vn-footer {
    flex-shrink: 0; text-align: center; padding: 8px 0 14px;
    font-size: 11.5px; color: var(--text-footer);
    background: var(--bg-footer); border-top: 1px solid var(--border-color);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
  }
</style>
"""

TOP_BAR = """\
<div id="vn-topbar">
  <!-- Left: Sector/Index selector + MA checkboxes -->
  <div class="vn-ctrl-group">
    <label for="vn-group-select" style="font-weight:600; color:var(--text-main);" id="lbl-group">Nhóm ngành:</label>
    <select id="vn-group-select" class="vn-select"></select>

    <span style="font-weight:600; color:var(--text-main); margin-left:6px;" id="lbl-ma">&#x2705; Đường MA:</span>

    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma10" checked style="width:15px;height:15px;cursor:pointer;accent-color:#17becf;">
      <span style="color:#17becf;font-weight:700;">&#9644; MA10</span>
    </label>

    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma20" checked style="width:15px;height:15px;cursor:pointer;accent-color:#d62728;">
      <span style="color:#d62728;font-weight:700;">&#9644; MA20</span>
    </label>

    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma50" checked style="width:15px;height:15px;cursor:pointer;accent-color:#2ca02c;">
      <span style="color:#2ca02c;font-weight:700;">&#9644; MA50</span>
    </label>

    <label style="display:flex;align-items:center;gap:4px;cursor:pointer;user-select:none;">
      <input type="checkbox" id="cb-ma200" checked style="width:15px;height:15px;cursor:pointer;accent-color:#ff7f0e;">
      <span style="color:#ff7f0e;font-weight:700;">&#9644; MA200</span>
    </label>
  </div>

  <!-- Right: Theme toggle + Language toggle + Access time -->
  <div class="vn-ctrl-group">
    <button id="vn-theme-toggle" class="vn-btn" type="button">&#127769; Dark</button>
    <button id="vn-lang-toggle" class="vn-btn" type="button">&#127760; VI</button>
    <div style="color:var(--text-muted); font-size:12.5px; white-space:nowrap;">
      &#128336;&nbsp;Accessed at:&nbsp;<strong><span id="vn-access-time">&#8211;</span></strong>
    </div>
  </div>
</div>
"""

BOTTOM_SNIPPET = """\
<!-- ── Injected by build_pages.py ── -->
<div class="vn-footer">
  &#128197;&nbsp;Data published at:&nbsp;<strong>{published}</strong>&nbsp;(UTC+7)
  &nbsp;&mdash;&nbsp;&#169;&nbsp;FTU-Kudo
</div>

<script>
window.BREADTH_DATA = {groups_json};

/* ── 1. Access time in UTC+7 ── */
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

/* ── 2. Sector/Index Selector, Theme (Light/Dark), Language (VI default / EN), MA Toggles ── */
(function () {
  var currentLang = 'vi';
  var isDark = false;
  var currentGroup = 'HOSE';

  var GROUP_LABELS = {
    HOSE:        { vi: 'HOSE (VN-Index ~400 mã)', en: 'HOSE (VN-Index ~400 stocks)' },
    VN30:        { vi: 'VN30 (Vốn hóa lớn)', en: 'VN30 (Large Cap)' },
    VN100:       { vi: 'VN100', en: 'VN100' },
    VNALL:       { vi: 'VNALL (Toàn bộ HOSE)', en: 'VNALL (All Share)' },
    VNMID:       { vi: 'VNMID (Vốn hóa vừa)', en: 'VNMID (Mid Cap)' },
    VNSML:       { vi: 'VNSML (Vốn hóa nhỏ)', en: 'VNSML (Small Cap)' },
    VNDIAMOND:   { vi: 'VNDIAMOND', en: 'VNDIAMOND' },
    VNSI:        { vi: 'VNSI (Phát triển bền vững)', en: 'VNSI (Sustainability)' },
    VNX50:       { vi: 'VNX50', en: 'VNX50' },
    VNXALL:      { vi: 'VNXALL', en: 'VNXALL' },
    VNCOND:      { vi: 'VNCOND (Hàng tiêu dùng không thiết yếu)', en: 'VNCOND (Consumer Discretionary)' },
    VNCONS:      { vi: 'VNCONS (Hàng tiêu dùng thiết yếu)', en: 'VNCONS (Consumer Staples)' },
    VNENE:       { vi: 'VNENE (Năng lượng)', en: 'VNENE (Energy)' },
    VNFIN:       { vi: 'VNFIN (Tài chính)', en: 'VNFIN (Financials)' },
    VNFINLEAD:   { vi: 'VNFINLEAD (Tài chính dẫn đầu)', en: 'VNFINLEAD (Leading Financials)' },
    VNFINSELECT: { vi: 'VNFINSELECT (Tài chính chọn lọc)', en: 'VNFINSELECT (Select Financials)' },
    VNHEAL:      { vi: 'VNHEAL (Chăm sóc sức khỏe)', en: 'VNHEAL (Health Care)' },
    VNIND:       { vi: 'VNIND (Công nghiệp)', en: 'VNIND (Industrials)' },
    VNIT:        { vi: 'VNIT (Công nghệ thông tin)', en: 'VNIT (Information Tech)' },
    VNMAT:       { vi: 'VNMAT (Nguyên vật liệu)', en: 'VNMAT (Materials)' },
    VNREAL:      { vi: 'VNREAL (Bất động sản)', en: 'VNREAL (Real Estate)' },
    VNUTI:       { vi: 'VNUTI (Tiện ích)', en: 'VNUTI (Utilities)' }
  };

  var I18N = {
    vi: {
      lblGroup: 'Nhóm ngành:',
      lblMa: '&#x2705; Đường MA:',
      titlePrefix: '📊 Độ rộng thị trường: VN-Index & Tỷ lệ cổ phiếu vượt MA',
      xAxis: 'Thời gian',
      yLeft: 'VN-Index (Điểm)',
      yRight: 'Tỷ lệ (%)',
      langBtn: '🌐 VI'
    },
    en: {
      lblGroup: 'Sector / Index:',
      lblMa: '&#x2705; Show MA lines:',
      titlePrefix: '📊 Market Breadth: VN-Index & Ratio of Stocks Above MA',
      xAxis: 'Timeline',
      yLeft: 'VN-Index (Index Points)',
      yRight: 'Percentage (%)',
      langBtn: '🌐 EN'
    }
  };

  function populateGroups() {
    var sel = document.getElementById('vn-group-select');
    if (!sel) return;
    var groups = (window.BREADTH_DATA && window.BREADTH_DATA.groups)
      ? Object.keys(window.BREADTH_DATA.groups)
      : Object.keys(GROUP_LABELS);
    sel.innerHTML = '';
    groups.forEach(function (g) {
      var opt = document.createElement('option');
      opt.value = g;
      var lbl = (GROUP_LABELS[g] && GROUP_LABELS[g][currentLang]) ? GROUP_LABELS[g][currentLang] : g;
      opt.textContent = lbl;
      if (g === currentGroup) opt.selected = true;
      sel.appendChild(opt);
    });
  }

  function findTraceIndices(gdiv, kw) {
    var indices = [];
    (gdiv.data || []).forEach(function (trace, i) {
      if (!trace.name) return;
      var name = trace.name.toUpperCase();
      var pos = name.indexOf(kw);
      if (pos === -1) return;
      var after = name[pos + kw.length];
      if (!after || !/\\d/.test(after)) indices.push(i);
    });
    return indices;
  }

  function updateGroupChart(gdiv, groupCode) {
    if (!window.BREADTH_DATA || !window.BREADTH_DATA.groups || !window.BREADTH_DATA.groups[groupCode]) return;
    var gData = window.BREADTH_DATA.groups[groupCode];
    var dates = window.BREADTH_DATA.dates;
    var maKeys = [
      { kw: 'MA10',  key: 'pct_MA10'  },
      { kw: 'MA20',  key: 'pct_MA20'  },
      { kw: 'MA50',  key: 'pct_MA50'  },
      { kw: 'MA200', key: 'pct_MA200' }
    ];
    maKeys.forEach(function (m) {
      var idxs = findTraceIndices(gdiv, m.kw);
      if (idxs.length > 0 && gData[m.key]) {
        Plotly.restyle(gdiv, { x: [dates], y: [gData[m.key]] }, idxs);
      }
    });
    applyLayout(gdiv);
  }

  function applyLayout(gdiv) {
    var t = I18N[currentLang];
    var grpName = (GROUP_LABELS[currentGroup] && GROUP_LABELS[currentGroup][currentLang]) || currentGroup;
    var bg = isDark ? '#111827' : '#ffffff';
    var fg = isDark ? '#f3f4f6' : '#222222';
    var grid = isDark ? '#374151' : '#cccccc';
    Plotly.relayout(gdiv, {
      'title.text': t.titlePrefix + ' — ' + grpName,
      'title.font.color': fg,
      'paper_bgcolor': bg,
      'plot_bgcolor': bg,
      'font.color': fg,
      'xaxis.title.text': t.xAxis,
      'xaxis.gridcolor': grid,
      'yaxis.title.text': t.yLeft,
      'yaxis2.title.text': t.yRight,
      'yaxis2.gridcolor': grid
    });
  }

  function applyUI(gdiv) {
    var t = I18N[currentLang];
    var lg = document.getElementById('lbl-group');
    var lm = document.getElementById('lbl-ma');
    var lb = document.getElementById('vn-lang-toggle');
    var tb = document.getElementById('vn-theme-toggle');
    if (lg) lg.textContent = t.lblGroup;
    if (lm) lm.innerHTML = t.lblMa;
    if (lb) lb.textContent = t.langBtn;
    if (tb) tb.textContent = isDark ? '☀️ Light' : '🌙 Dark';
    document.body.classList.toggle('dark-mode', isDark);
    populateGroups();
    if (gdiv) applyLayout(gdiv);
  }

  var tries = 0;
  var timer = setInterval(function () {
    var gd = document.querySelector('.js-plotly-plot');
    if (gd && gd.data && gd.data.length > 0) {
      clearInterval(timer);

      [
        { id: 'cb-ma10',  kw: 'MA10'  },
        { id: 'cb-ma20',  kw: 'MA20'  },
        { id: 'cb-ma50',  kw: 'MA50'  },
        { id: 'cb-ma200', kw: 'MA200' }
      ].forEach(function (ma) {
        var cb = document.getElementById(ma.id);
        if (!cb) return;
        cb.addEventListener('change', function () {
          var indices = findTraceIndices(gd, ma.kw);
          if (indices.length > 0) {
            Plotly.restyle(gd, { visible: [cb.checked ? true : 'legendonly'] }, indices);
          }
        });
      });

      var sel = document.getElementById('vn-group-select');
      if (sel) {
        sel.addEventListener('change', function () {
          currentGroup = sel.value;
          updateGroupChart(gd, currentGroup);
        });
      }

      var themeBtn = document.getElementById('vn-theme-toggle');
      if (themeBtn) {
        themeBtn.addEventListener('click', function () {
          isDark = !isDark;
          applyUI(gd);
        });
      }

      var langBtn = document.getElementById('vn-lang-toggle');
      if (langBtn) {
        langBtn.addEventListener('click', function () {
          currentLang = (currentLang === 'vi') ? 'en' : 'vi';
          applyUI(gd);
        });
      }

      applyUI(gd);
    } else if (++tries >= 100) {
      clearInterval(timer);
    }
  }, 100);
})();
</script>
"""


def main():
    if not SRC.exists():
        print(f"ERROR: {SRC} not found. Run 'python analysis.py --build' first.", file=sys.stderr)
        sys.exit(1)

    html = SRC.read_text(encoding="utf-8")

    # 1. Inject / update <title> and STYLE_BLOCK in <head>
    if "<title>" in html:
        html = re.sub(r"<title>.*?</title>", f"<title>{PAGE_TITLE}</title>", html, count=1, flags=re.DOTALL)
    else:
        html = html.replace("<head>", f"<head>\n<title>{PAGE_TITLE}</title>", 1)

    html = html.replace("</head>", f"{STYLE_BLOCK}\n</head>", 1)

    # 2. Wrap chart inside #main-content and inject TOP_BAR
    html = re.sub(r"(<body[^>]*>)", lambda m: f"{m.group(1)}\n{TOP_BAR}\n<div id=\"main-content\">", html, count=1)

    # 3. Load groups JSON if available
    groups_json_str = "null"
    if GROUPS_JSON.exists():
        groups_json_str = GROUPS_JSON.read_text(encoding="utf-8")

    published_str = datetime.now(VN).strftime("%d/%m/%Y %H:%M:%S")
    bottom = (
        BOTTOM_SNIPPET
        .replace("{published}", published_str)
        .replace("{groups_json}", groups_json_str)
    )

    html = re.sub(r"</body>", lambda _: f"</div>\n{bottom}\n</body>", html, count=1)

    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_text(html, encoding="utf-8")
    print(f"Successfully built {DEST} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
