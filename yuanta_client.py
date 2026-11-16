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
import threading
import time
from datetime import datetime, timedelta
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
        self.failed_requests = 0   # số request lỗi mạng/HTTP/không phải JSON
        self.empty_payloads = 0    # số request trả JSON hợp lệ nhưng không có bản ghi

    def _count(self, attr: str) -> None:
        with self._lock:
            setattr(self, attr, getattr(self, attr) + 1)

    def _get_json(self, url: str, params: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> Any:
        try:
            resp = self.session.get(url, params=params, headers=extra_headers, timeout=self.timeout)
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
