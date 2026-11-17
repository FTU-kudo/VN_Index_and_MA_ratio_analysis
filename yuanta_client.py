"""
Yuanta Securities Market Data Client
-----------------------------------
Thay thế hoàn toàn nguồn vnstock bằng hệ thống API của Yuanta Securities:
1. Price History (OHLCV + Khối ngoại từ 2010 - hiện tại):
   https://ysradarapi.yuanta.com.vn/api/v3/market_data/market_watch/price_history/{SYMBOL}?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
2. Foreign Investor History (Giao dịch NĐTNN chi tiết từ 2025 - hiện tại):
   https://ysradarapi.yuanta.com.vn/api/v3/market_data/market_watch/price_history_foreign/{SYMBOL}?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://ysradarapi.yuanta.com.vn/api/v3/market_data/market_watch"
PRICE_HISTORY_URL = f"{BASE_URL}/price_history/{{symbol}}"
FOREIGN_HISTORY_URL = f"{BASE_URL}/price_history_foreign/{{symbol}}"
LIST_STOCK_INFO_URL = "https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/stock/list_stock_info"
# Host dự phòng (CHƯA được xác minh): cùng path nhưng trên host `ysradarapi` - host mà endpoint price_history
# đang chạy ổn định từ GitHub Actions. Nếu host này không có endpoint, request sẽ lỗi và bị bỏ qua êm.
LIST_STOCK_INFO_URLS = [
    LIST_STOCK_INFO_URL,
    "https://ysradarapi.yuanta.com.vn/api/v3/market_data/price_board/stock/list_stock_info",
]

# Snapshot phiên hiện tại của một chỉ số (điểm số, tham chiếu, tăng/giảm, khớp lệnh theo phút):
INDEX_TRANSACTION_URL = "https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/index/index_transaction/{code}"
# Lịch sử điểm số HÀNG NGÀY của mọi chỉ số (VNINDEX, VN30, VNREAL, VNHEAL, ...) - định dạng TradingView:
#   {"s": "ok", "bars": [{"t": epoch_giây (07:00 UTC+7 của ngày giao dịch), "o","h","l","c","v"}, ...]}
TRADINGVIEW_HISTORY_URL = "https://ysradar.yuanta.com.vn/v2/tradingview/history"

# Host dự phòng (CHƯA xác minh) - chỉ dùng khi host chính không trả dữ liệu
INDEX_TRANSACTION_URLS = [
    INDEX_TRANSACTION_URL,
    "https://ysradarapi.yuanta.com.vn/api/v3/market_data/price_board/index/index_transaction/{code}",
]
VN_TZ = timezone(timedelta(hours=7))

# Nhóm -> mã chỉ số dùng cho index_transaction (HOSE = toàn sàn = VNINDEX)
GROUP_TO_INDEX_CODE = {"HOSE": "VNINDEX", "HSX": "VNINDEX"}

# Danh sách các bộ lọc chỉ số & nhóm ngành trên VN-Index / HOSE
INDEX_GROUPS = [
    "HOSE",         # Toàn bộ ~400 mã HOSE (VN-Index)
    "VN30",
    "VN100",
    "VNALL",
    "VNMID",
    "VNSML",
    "VNDIAMOND",
    "VNSI",
    "VNX50",
    "VNXALL",
    "VNCOND",       # Hàng tiêu dùng không thiết yếu
    "VNCONS",       # Hàng tiêu dùng thiết yếu
    "VNENE",        # Năng lượng
    "VNFIN",        # Tài chính
    "VNFINLEAD",    # Tài chính dẫn đầu
    "VNFINSELECT",  # Tài chính chọn lọc
    "VNHEAL",       # Chăm sóc sức khỏe
    "VNIND",        # Công nghiệp
    "VNIT",         # Công nghệ thông tin
    "VNMAT",        # Nguyên vật liệu
    "VNREAL",       # Bất động sản
    "VNUTI",        # Tiện ích
]

# Header giống một lần mở URL trực tiếp trên trình duyệt (đúng cách bạn test thành công):
# KHÔNG có Origin/Referer. Hồ sơ "with_origin" chỉ dùng làm phương án dự phòng.
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
ORIGIN_HEADERS = {
    "Origin": "https://ysradar.yuanta.com.vn",
    "Referer": "https://ysradar.yuanta.com.vn/",
}

# Danh sách fallback các mã thanh khoản cao trên HOSE (VN30 + Midcap tiêu biểu)
# Dùng khi không có file hose_symbols.txt hoặc không thể fetch danh sách sàn
FALLBACK_HOSE_SYMBOLS = [
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "MBB", "MSN", "MWG", "PLX", "POW", "SAB", "SHB", "SSB", "SSI", "STB",
    "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
    "DGC", "DIG", "DXG", "EIB", "FRT", "GMD", "HCM", "HDG", "HSG", "KBC",
    "KDH", "LPB", "NLG", "NT2", "OCB", "PDR", "PNJ", "PVD", "REE", "SBT",
    "VCI", "VND", "VTP", "DPM", "DCM", "GEX", "KDC", "PC1", "PHR", "VGC",
]


def _build_session(retries: int = 3, backoff_factor: float = 0.8) -> requests.Session:
    """Khởi tạo requests.Session với cơ chế Retry (exponential backoff) cho lỗi mạng/429/5xx."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(DEFAULT_HEADERS)
    return session


# Yuanta đặt lớp chống bot phía trước API: với một số IP (vd. máy chủ GitHub Actions) nó trả HTTP 200 + một trang HTML
# rất ngắn "Attention Required" chỉ chứa  window.location.href="<cùng URL>&jskey=<token>"  - trình duyệt chạy JS và tự
# gọi lại URL đó nên bạn thấy JSON, còn requests thì dừng ở trang này (-> "Expecting value: line 1 column 1").
_CHALLENGE_RE = re.compile(r'window\.location\.href\s*=\s*"(https://[^"]+)"')
MAX_CHALLENGE_HOPS = 2


def challenge_url(text: str) -> Optional[str]:
    """Trả về URL chuyển hướng (có jskey) nếu `text` là trang chống bot của Yuanta, ngược lại None."""
    if not text or len(text) > 2000 or "jskey=" not in text:
        return None
    m = _CHALLENGE_RE.search(text)
    if not m:
        return None
    href = m.group(1)
    host = (urlparse(href).hostname or "").lower()
    # Chỉ đi theo URL thuộc miền của Yuanta (không đi theo chuyển hướng tới nơi khác)
    return href if host == "yuanta.com.vn" or host.endswith(".yuanta.com.vn") else None


def _validate_date(date_str: str) -> str:
    """Đảm bảo định dạng ngày chuẩn YYYY-MM-DD."""
    return datetime.strptime(date_str.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")


_RECORD_KEYS = ("response", "data", "items", "records", "result", "results", "list", "rows")


def _extract_records(payload: Any, _depth: int = 0) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách bản ghi (list[dict]) từ JSON của Yuanta.
    Định dạng thực tế của Yuanta: {"success": true, "response": [ {...}, {...} ]}
    Hàm tìm đệ quy theo các key phổ biến, cuối cùng quét mọi value dạng list[dict].
    """
    if _depth > 4:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in _RECORD_KEYS:
            if key in payload:
                found = _extract_records(payload[key], _depth + 1)
                if found:
                    return found
        for val in payload.values():
            if isinstance(val, (list, dict)):
                found = _extract_records(val, _depth + 1)
                if found:
                    return found
    return []


def _pick(item: Dict[str, Any], *names: str) -> Any:
    """Lấy giá trị theo tên key KHÔNG phân biệt hoa/thường (API trả PascalCase: StockCode, SymbolType...)."""
    lowered = {str(k).lower(): v for k, v in item.items()}
    for n in names:
        if n.lower() in lowered and lowered[n.lower()] not in (None, ""):
            return lowered[n.lower()]
    return None


class YuantaClient:
    """Client giao tiếp với hệ thống API Yuanta Securities."""

    def __init__(self, timeout: tuple[int, int] = (5, 20), rate_limit_delay: float = 0.05):
        self.session = _build_session()
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self._lock = threading.Lock()
        self.history_url = TRADINGVIEW_HISTORY_URL   # có thể đổi bởi scripts/fetch_index_ohlcv.py nếu probe tìm ra URL/host chạy được
        self.failed_requests = 0   # số request lỗi mạng/HTTP/không phải JSON
        self.empty_payloads = 0    # số request trả JSON hợp lệ nhưng không có bản ghi
        self.challenges_followed = 0   # số lần phải đi theo trang chống bot

    def _count(self, attr: str) -> None:
        with self._lock:
            setattr(self, attr, getattr(self, attr) + 1)

    def _get_json(self, url: str, params: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None,
                  timeout: Optional[tuple] = None) -> Any:
        try:
            resp = self.session.get(url, params=params, headers=extra_headers, timeout=timeout or self.timeout)
            resp.raise_for_status()
            for _ in range(MAX_CHALLENGE_HOPS):
                href = challenge_url(resp.text)
                if not href:
                    break
                self._count("challenges_followed")
                if self.challenges_followed == 1:
                    print("[YuantaClient] Gặp trang chống bot (jskey) -> tự chuyển hướng theo JS như trình duyệt.")
                resp = self.session.get(href, headers=extra_headers, timeout=timeout or self.timeout)
                resp.raise_for_status()
        except requests.exceptions.Timeout:
            self._count("failed_requests")
            print(f"[YuantaClient] TIMEOUT khi gọi {url} params={params}")
            return None
        except requests.exceptions.HTTPError as exc:
            self._count("failed_requests")
            status = exc.response.status_code if exc.response is not None else "?"
            body = (exc.response.text[:150].replace("\n", " ") if exc.response is not None else "")
            print(f"[YuantaClient] HTTP {status} khi gọi {url} params={params}: {body}")
            return None
        except requests.exceptions.RequestException as exc:
            self._count("failed_requests")
            print(f"[YuantaClient] Lỗi kết nối khi gọi {url}: {exc}")
            return None
        finally:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

        # Parse JSON RIÊNG để phân biệt "lỗi mạng" với "server trả body rỗng/HTML" (WAF, chặn IP, geo-block...)
        try:
            return resp.json()
        except ValueError:
            self._count("failed_requests")
            body = resp.text[:150].replace("\n", " ")
            print(
                f"[YuantaClient] Body KHÔNG phải JSON | url={resp.url} | HTTP {resp.status_code} | "
                f"content-type={resp.headers.get('Content-Type')} | len={len(resp.content)} | body[:150]={body!r}"
            )
            return None

    def get_index_symbols(self, index_code: str) -> List[str]:
        """
        Lấy danh sách mã cổ phiếu thuộc một sàn/chỉ số/nhóm ngành từ Yuanta API:
        - Toàn sàn HOSE (HSX): ?exchange=HSX
        - Chỉ số / nhóm ngành: ?index_code={INDEX_CODE}
        Thử lần lượt: host chính -> host dự phòng, header kiểu trình duyệt -> header có Origin/Referer.
        """
        code = str(index_code).strip().upper()
        is_exchange = code in ("HOSE", "HSX")
        params = {"exchange": "HSX"} if is_exchange else {"index_code": code}

        records: List[Dict[str, Any]] = []
        for url in LIST_STOCK_INFO_URLS:
            for extra in (None, ORIGIN_HEADERS):
                payload = self._get_json(url, params, extra_headers=extra)
                records = _extract_records(payload)
                if records:
                    break
                if payload is not None:
                    self._count("empty_payloads")
                    print(f"[YuantaClient] JSON hợp lệ nhưng không có bản ghi cho {code} (params={params}). Sample: {str(payload)[:200]}")
            if records:
                break
        if not records:
            return []

        symbols: List[str] = []
        for item in records:
            sym = _pick(item, "StockCode", "symbol", "stock_code", "code", "ticker")
            if not sym:
                continue
            # Chỉ lấy cổ phiếu thường (SymbolType = "ST"); loại CW, ETF, trái phiếu...
            sym_type = _pick(item, "SymbolType")
            if sym_type is not None and str(sym_type).strip().upper() != "ST":
                continue
            # Với toàn sàn HOSE, loại mã không thuộc HSX (phòng khi API trả lẫn)
            exch = _pick(item, "Exchange")
            if is_exchange and exch is not None and str(exch).strip().upper() not in ("HSX", "HOSE"):
                continue
            sym_str = str(sym).strip().upper()
            if len(sym_str) == 3 and sym_str.isalpha():
                symbols.append(sym_str)
        return sorted(set(symbols))

    def get_index_history(self, code: str, from_date: str = "2010-01-01", to_date: Optional[str] = None) -> pd.DataFrame:
        """
        Lấy lịch sử điểm số HÀNG NGÀY của một chỉ số từ endpoint TradingView của Yuanta (từ 2010 hoặc từ ngày
        chỉ số đó ra đời). Trả về DataFrame [time(date), open, high, low, close, volume, code].
        1 request lấy toàn bộ lịch sử (limit=100000), không cần chia khoảng.
        """
        code = str(code).strip().upper()
        # Giống URL bạn đã test thành công: from/to là nửa đêm UTC (vd from=1262304000, to=1794873600),
        # `to` là nửa đêm UTC kế tiếp (không đẩy xa tương lai).
        from_ts = int(datetime.strptime(_validate_date(from_date), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        if to_date:
            to_ts = int((datetime.strptime(_validate_date(to_date), "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp())
        else:
            to_ts = (int(datetime.now(timezone.utc).timestamp()) // 86400 + 1) * 86400
        params = {"symbol": code, "resolution": "1D", "from": from_ts, "to": to_ts, "limit": 100000}
        payload = None
        for extra in (None, ORIGIN_HEADERS):
            payload = self._get_json(self.history_url, params, extra_headers=extra, timeout=(10, 90))
            if isinstance(payload, dict) and payload.get("s") == "ok":
                break
        if not isinstance(payload, dict) or payload.get("s") != "ok":
            if payload is not None:
                self._count("empty_payloads")
                print(f"[YuantaClient] tradingview/history {code}: s={payload.get('s') if isinstance(payload, dict) else '?'} | {str(payload)[:150]}")
            return pd.DataFrame()

        bars = payload.get("bars")
        if isinstance(bars, list) and bars and isinstance(bars[0], dict):
            raw = pd.DataFrame(bars)                      # dạng Yuanta: list các object {t,o,h,l,c,v}
        elif isinstance(payload.get("t"), list):          # dạng TradingView chuẩn: các mảng song song
            raw = pd.DataFrame({k: payload.get(k) for k in ("t", "o", "h", "l", "c", "v") if isinstance(payload.get(k), list)})
        else:
            return pd.DataFrame()
        if raw.empty or "t" not in raw or "c" not in raw:
            return pd.DataFrame()

        df = pd.DataFrame({
            "time": [datetime.fromtimestamp(int(t), VN_TZ).date() for t in raw["t"]],
            "open": pd.to_numeric(raw.get("o"), errors="coerce").round(2) if "o" in raw else float("nan"),
            "high": pd.to_numeric(raw.get("h"), errors="coerce").round(2) if "h" in raw else float("nan"),
            "low": pd.to_numeric(raw.get("l"), errors="coerce").round(2) if "l" in raw else float("nan"),
            "close": pd.to_numeric(raw["c"], errors="coerce").round(2),
            "volume": pd.to_numeric(raw.get("v"), errors="coerce") if "v" in raw else float("nan"),
            "code": code,
        })
        df = df[df["close"] > 0].dropna(subset=["close"])
        return df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)

    def get_index_snapshot(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Lấy điểm số chỉ số của phiên GẦN NHẤT từ endpoint index_transaction/{code}
        (VNINDEX, VN30, VNREAL, ...). Endpoint này chỉ có dữ liệu trong phiên (từng phút),
        KHÔNG có lịch sử nhiều ngày -> lịch sử phải được tích luỹ bằng index_store.py.

        Trả về dict: code, date, close (CurrIndex), ref, change, change_pct, up, down, no_change,
        ceiling, floor, total_vol, total_val, n_records, session_id, last_tx_time, is_final.
        """
        code = str(code).strip().upper()
        resp: Optional[Dict[str, Any]] = None
        for url_tpl in INDEX_TRANSACTION_URLS:
            url = url_tpl.format(code=code)
            for extra in (None, ORIGIN_HEADERS):
                payload = self._get_json(url, {}, extra_headers=extra)
                body = payload.get("response") if isinstance(payload, dict) else None
                if isinstance(body, dict) and body:
                    resp = body
                    break
                if payload is not None:
                    self._count("empty_payloads")
                    print(f"[YuantaClient] index_transaction/{code}: không có 'response' dạng object. Sample: {str(payload)[:200]}")
            if resp is not None:
                break
        if resp is None:
            return None

        txs = [t for t in (resp.get("ListTransaction") or []) if isinstance(t, dict)]
        last_tx = max(txs, key=lambda t: t.get("UnixTime") or 0) if txs else None
        last_dt = None
        if last_tx and last_tx.get("UnixTime"):
            last_dt = datetime.fromtimestamp(int(last_tx["UnixTime"]), VN_TZ)

        def _num(v):
            try:
                f = float(v)
                return f if f == f else None
            except (TypeError, ValueError):
                return None

        close = _num(_pick(resp, "CurrIndex"))
        if not close and last_tx:
            close = _num(last_tx.get("Index"))
        ref = _num(_pick(resp, "RefIndex"))
        if not close or close <= 0:
            print(f"[YuantaClient] index_transaction/{code}: CurrIndex không hợp lệ ({close}).")
            return None

        now = datetime.now(VN_TZ)
        session_date = (last_dt or now).date()
        # Chốt phiên: dữ liệu của ngày trước đó, hoặc đã qua 15:00 (UTC+7) của chính ngày hôm nay.
        is_final = session_date < now.date() or (session_date == now.date() and now.time() >= datetime.strptime("15:00", "%H:%M").time())
        change = (close - ref) if ref else None
        return {
            "code": str(_pick(resp, "IndexCode") or code).upper(),
            "date": session_date.strftime("%Y-%m-%d"),
            "close": round(close, 2),
            "ref": round(ref, 2) if ref else None,
            "change": round(change, 2) if change is not None else None,
            "change_pct": round(change / ref * 100, 2) if ref else None,
            "up": _num(_pick(resp, "Up")),
            "down": _num(_pick(resp, "Down")),
            "no_change": _num(_pick(resp, "NoChange")),
            "ceiling": _num(_pick(resp, "Ceiling")),
            "floor": _num(_pick(resp, "Floor")),
            "total_vol": _num(_pick(resp, "TotalVolTrade")),
            "total_val": _num(_pick(resp, "TotalValTrade")),
            "n_records": len(txs),
            "session_id": str(_pick(resp, "CurrTradingSessionID") or ""),
            "last_tx_time": last_dt.strftime("%H:%M:%S") if last_dt else None,
            "is_final": bool(is_final),
        }

    def get_all_index_groups(self, groups: Optional[List[str]] = None) -> Dict[str, List[str]]:
        """Lấy danh sách mã cổ phiếu cho toàn bộ các bộ lọc chỉ số & nhóm ngành."""
        target_groups = groups or INDEX_GROUPS
        result: Dict[str, List[str]] = {}
        for grp in target_groups:
            syms = self.get_index_symbols(grp)
            if syms:
                result[grp] = syms
        return result

    def get_price_history(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        chunk_days: int = 365,
    ) -> pd.DataFrame:
        """
        Lấy dữ liệu OHLCV lịch sử từ API Yuanta (từ 2010 - hiện tại).
        Tự động chia nhỏ theo từng khoảng `chunk_days` (mặc định 365 ngày) để tránh
        giới hạn số bản ghi/request của API.
        Chuẩn hóa cột về: ['time', 'open', 'high', 'low', 'close', 'volume', 'symbol']
        """
        sym = str(symbol).strip().upper()
        start_dt = datetime.strptime(_validate_date(from_date), "%Y-%m-%d").date()
        end_dt = datetime.strptime(_validate_date(to_date), "%Y-%m-%d").date()

        if start_dt > end_dt:
            return pd.DataFrame()

        url = PRICE_HISTORY_URL.format(symbol=sym)
        dfs: List[pd.DataFrame] = []
        cur_start = start_dt

        while cur_start <= end_dt:
            cur_end = min(cur_start + timedelta(days=chunk_days - 1), end_dt)
            payload = self._get_json(
                url,
                {
                    "from_date": cur_start.strftime("%Y-%m-%d"),
                    "to_date": cur_end.strftime("%Y-%m-%d"),
                },
            )
            records = _extract_records(payload)
            if records:
                chunk_df = self._normalize_ohlcv_columns(pd.DataFrame(records), sym)
                if not chunk_df.empty:
                    dfs.append(chunk_df)
            cur_start = cur_end + timedelta(days=1)

        if not dfs:
            return pd.DataFrame()

        full_df = pd.concat(dfs, ignore_index=True)
        return (
            full_df.sort_values("time")
            .drop_duplicates(subset=["time"], keep="last")
            .reset_index(drop=True)
        )

    def get_foreign_history(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """
        Lấy dữ liệu giao dịch Nhà đầu tư nước ngoài chi tiết (từ 2025 - hiện tại).
        Chuẩn hóa cột về: ['time', 'symbol', 'foreign_buy_vol', 'foreign_sell_vol',
                           'foreign_net_vol', 'foreign_buy_val', 'foreign_sell_val', 'foreign_net_val']
        """
        sym = str(symbol).strip().upper()
        start = _validate_date(from_date)
        end = _validate_date(to_date)

        url = FOREIGN_HISTORY_URL.format(symbol=sym)
        payload = self._get_json(url, {"from_date": start, "to_date": end})
        records = _extract_records(payload)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame(records)
        df = self._normalize_foreign_columns(df, sym)
        return df

    @staticmethod
    def _normalize_ohlcv_columns(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Chuẩn hóa tên cột OHLCV từ response của Yuanta."""
        col_map = {}
        lower_cols = {c.lower(): c for c in df.columns}

        date_candidates = ["tradingdate", "trading_date", "date", "time", "datetime", "t", "ngay"]
        for cand in date_candidates:
            if cand in lower_cols:
                col_map[lower_cols[cand]] = "time"
                break

        for target, cands in {
            "open": ["open", "openprice", "open_price", "o"],
            "high": ["high", "highest", "highprice", "high_price", "h"],
            "low": ["low", "lowest", "lowprice", "low_price", "l"],
            "close": ["close", "closeprice", "close_price", "adjclose", "adj_close", "matchprice", "c"],
            "volume": ["volume", "totalvolume", "total_volume", "vol", "matchvolume", "totalvol", "v"],
        }.items():
            for cand in cands:
                if cand in lower_cols:
                    col_map[lower_cols[cand]] = target
                    break

        df = df.rename(columns=col_map)
        if "time" not in df.columns or "close" not in df.columns:
            return pd.DataFrame()

        # Hỗ trợ cả chuỗi ngày YYYY-MM-DD, ISO datetime, và Unix timestamp (giây / mili-giây)
        if pd.api.types.is_numeric_dtype(df["time"]):
            unit = "ms" if df["time"].dropna().median() > 1e11 else "s"
            df["time"] = pd.to_datetime(df["time"], unit=unit, errors="coerce").dt.date
        else:
            df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.date
        df = df.dropna(subset=["time"])

        for num_col in ["open", "high", "low", "close", "volume"]:
            if num_col in df.columns:
                df[num_col] = pd.to_numeric(df[num_col], errors="coerce")
            else:
                df[num_col] = 0.0

        df["symbol"] = symbol
        keep_cols = ["time", "open", "high", "low", "close", "volume", "symbol"]
        return df[keep_cols].sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    @staticmethod
    def _normalize_foreign_columns(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Chuẩn hóa tên cột giao dịch khối ngoại từ response của Yuanta."""
        lower_cols = {c.lower(): c for c in df.columns}
        col_map = {}

        for cand in ["tradingdate", "trading_date", "date", "time"]:
            if cand in lower_cols:
                col_map[lower_cols[cand]] = "time"
                break

        mapping_candidates = {
            "foreign_buy_vol": ["foreignbuyvol", "foreignvolumebuy", "foreign_buy_vol", "buyvol", "buy_vol", "f_buy_vol"],
            "foreign_sell_vol": ["foreignsellvol", "foreignvolumesell", "foreign_sell_vol", "sellvol", "sell_vol", "f_sell_vol"],
            "foreign_net_vol": ["foreignnetvol", "foreignvolumenet", "foreign_net_vol", "netvol", "net_vol", "f_net_vol"],
            "foreign_buy_val": ["foreignbuyval", "foreignvaluebuy", "foreignbuyvalue", "foreign_buy_val", "buyval", "buy_val", "f_buy_val"],
            "foreign_sell_val": ["foreignsellval", "foreignvaluesell", "foreignsellvalue", "foreign_sell_val", "sellval", "sell_val", "f_sell_val"],
            "foreign_net_val": ["foreignnetval", "foreignvaluenet", "foreignnetvalue", "foreign_net_val", "netval", "net_val", "f_net_val"],
        }
        for target, cands in mapping_candidates.items():
            for cand in cands:
                if cand in lower_cols:
                    col_map[lower_cols[cand]] = target
                    break

        df = df.rename(columns=col_map)
        if "time" not in df.columns:
            return pd.DataFrame()

        df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.date
        df = df.dropna(subset=["time"])

        for col in [
            "foreign_buy_vol", "foreign_sell_vol", "foreign_net_vol",
            "foreign_buy_val", "foreign_sell_val", "foreign_net_val",
        ]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            else:
                df[col] = 0.0

        # Tự tính net nếu API chỉ trả về buy & sell
        if (df["foreign_net_vol"] == 0).all() and (
            df["foreign_buy_vol"].ne(0).any() or df["foreign_sell_vol"].ne(0).any()
        ):
            df["foreign_net_vol"] = df["foreign_buy_vol"] - df["foreign_sell_vol"]

        if (df["foreign_net_val"] == 0).all() and (
            df["foreign_buy_val"].ne(0).any() or df["foreign_sell_val"].ne(0).any()
        ):
            df["foreign_net_val"] = df["foreign_buy_val"] - df["foreign_sell_val"]

        df["symbol"] = symbol
        keep_cols = [
            "time", "symbol",
            "foreign_buy_vol", "foreign_sell_vol", "foreign_net_vol",
            "foreign_buy_val", "foreign_sell_val", "foreign_net_val",
        ]
        return df[keep_cols].sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
