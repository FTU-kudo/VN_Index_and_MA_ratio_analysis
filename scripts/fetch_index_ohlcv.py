"""
Tải OHLCV ngày của TẤT CẢ các chỉ số từ Yuanta TradingView -> data/index_ohlcv.csv
và tự GHI BÁO CÁO CHẨN ĐOÁN -> data/index_fetch_report.json (commit vào repo để đọc được mà không cần log Actions).

Quy trình
  1. PROBE: thử endpoint TradingView với nhiều kiểu header/host (và 2 endpoint đối chứng đã biết là chạy được:
     list_stock_info, index_transaction) -> ghi lại HTTP status, content-type, 300 ký tự đầu của body...
  2. Chọn kiểu request đầu tiên trả {"s": "ok", "bars": [...]} làm "winner".
  3. Tải toàn bộ OHLCV của mọi chỉ số bằng kiểu request winner (lần đầu: từ 2010; các lần sau: tải bù ~10 ngày).
  4. Ghi báo cáo. Mã thoát: 0 nếu có ít nhất 1 chỉ số tải được, 2 nếu không có gì.

Chạy tay (trên máy cá nhân, nơi trình duyệt đã gọi được API):
  python scripts/fetch_index_ohlcv.py --full
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from index_store import OHLCV_CSV, IndexStore, group_index_code  # noqa: E402
from yuanta_client import INDEX_GROUPS, VN_TZ, YuantaClient  # noqa: E402

REPORT_JSON = "data/index_fetch_report.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
ORIGIN = "https://ysradar.yuanta.com.vn"

# Các kiểu header sẽ thử. None = để requests tự đặt (User-Agent: python-requests/x.y)
PROFILES = {
    "browser_basic": {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7"},
    "browser_full": {"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                     "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7", "Accept-Encoding": "gzip, deflate",
                     "Referer": ORIGIN + "/", "Origin": ORIGIN,
                     "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"},
    "plain_requests": None,
    "curl_like": {"User-Agent": "curl/8.5.0", "Accept": "*/*"},
}
HISTORY_URLS = {
    "ysradar": "https://ysradar.yuanta.com.vn/v2/tradingview/history",
    "ysradarapi": "https://ysradarapi.yuanta.com.vn/v2/tradingview/history",   # phỏng đoán, chỉ để thử
}
# URL y hệt cái bạn đã mở được trong trình duyệt (from/to cố định)
BROWSER_PARAMS = {"symbol": "VNINDEX", "resolution": "1D", "from": 1262304000, "to": 1794873600, "limit": 100000}
SHORT_PARAMS = {"symbol": "VNINDEX", "resolution": "1D", "from": 1794000000, "to": 1794873600, "limit": 100000}


def probe(name: str, url: str, params=None, headers=None, timeout=(8, 30)) -> dict:
    """Gọi 1 request THÔ (không retry, không nuốt lỗi) và trả về mọi thứ cần để chẩn đoán."""
    info = {"name": name, "url": url, "params": params}
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        info.update({
            "status": r.status_code, "content_type": r.headers.get("Content-Type"),
            "server": r.headers.get("Server"), "cf_ray": r.headers.get("CF-RAY"), "via": r.headers.get("Via"),
            "redirects": [h.status_code for h in r.history], "bytes": len(r.content),
            "elapsed_s": round(time.time() - t0, 2), "body_head": r.text[:300].replace("\n", " "),
        })
        try:
            j = r.json()
            if isinstance(j, dict):
                info["json_s"] = j.get("s")
                bars = j.get("bars")
                info["n_bars"] = len(bars) if isinstance(bars, list) else (len(j["t"]) if isinstance(j.get("t"), list) else 0)
            else:
                info["json_s"] = f"json_{type(j).__name__}"
        except ValueError:
            info["json_s"] = "NOT_JSON"
    except Exception as e:  # noqa: BLE001 - cần ghi lại MỌI loại lỗi mạng
        info.update({"error": f"{type(e).__name__}: {e}", "elapsed_s": round(time.time() - t0, 2)})
    return info


def is_good(p: dict) -> bool:
    return p.get("json_s") == "ok" and (p.get("n_bars") or 0) > 0


def run_probes() -> tuple[list[dict], dict | None]:
    results, winner = [], None
    # Đối chứng: 2 endpoint đã biết là chạy được từ runner
    results.append(probe("control_list_stock_info", "https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/stock/list_stock_info",
                         {"exchange": "HSX"}, PROFILES["browser_basic"]))
    results.append(probe("control_index_transaction", "https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/index/index_transaction/VNINDEX",
                         None, PROFILES["browser_basic"]))
    for host, url in HISTORY_URLS.items():
        for prof_name, headers in PROFILES.items():
            for pname, params in (("short", SHORT_PARAMS), ("browser_url", BROWSER_PARAMS)):
                p = probe(f"tv_{host}_{prof_name}_{pname}", url, params, headers)
                p.update({"host": host, "profile": prof_name, "range": pname})
                results.append(p)
                print(f"  probe {p['name']}: status={p.get('status')} s={p.get('json_s')} bars={p.get('n_bars')} err={p.get('error')}")
                if winner is None and is_good(p):
                    winner = {"host": host, "url": url, "profile": prof_name}
    return results, winner


def egress_info() -> dict:
    """Quốc gia/nhà mạng của runner (KHÔNG ghi địa chỉ IP) - để kiểm tra giả thuyết chặn theo vùng."""
    try:
        j = requests.get("https://ipinfo.io/json", timeout=10).json()
        return {"country": j.get("country"), "region": j.get("region"), "org": j.get("org")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="Tải lại toàn bộ lịch sử từ 2010")
    ap.add_argument("--report", default=REPORT_JSON)
    ap.add_argument("--no-probe", action="store_true", help="Bỏ qua bước thăm dò, dùng mặc định (nhanh, dùng trên máy cá nhân)")
    args = ap.parse_args()

    report = {"generated_at": datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M:%S %z"), "runner": egress_info()}
    print("Bước 1: thăm dò endpoint TradingView...")
    if args.no_probe:
        probes, winner = [], {"host": "ysradar", "url": HISTORY_URLS["ysradar"], "profile": "browser_basic"}
    else:
        probes, winner = run_probes()
    report["probes"], report["winner"] = probes, winner

    codes = sorted({group_index_code(g) for g in INDEX_GROUPS})
    report["codes"] = codes
    exit_code = 2
    if winner is None:
        print("[LỖI] Không có kiểu request nào lấy được dữ liệu TradingView từ môi trường này. Xem báo cáo:", args.report)
        report["status"] = "no_working_request"
    else:
        print(f"Bước 2: tải OHLCV cho {len(codes)} chỉ số bằng kiểu '{winner['profile']}' trên host '{winner['host']}'...")
        client = YuantaClient(rate_limit_delay=0.2)
        client.history_url = winner["url"]
        headers = PROFILES[winner["profile"]]
        client.session.headers.clear()
        client.session.headers.update(headers if headers is not None else requests.utils.default_headers())
        store = IndexStore()
        stats = store.update_from_history(client, codes, force_full=args.full)
        got = [c for c, s in stats.items() if s.get("fetched")]
        report["per_code"] = stats
        report["summary"] = {"ok": len(got), "total": len(codes), "rows_total": int(len(store.ohl)),
                             "failed": [c for c in codes if c not in got]}
        report["status"] = "ok" if got else "fetch_failed"
        exit_code = 0 if got else 2

    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"Đã ghi báo cáo {args.report} (status={report['status']}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
