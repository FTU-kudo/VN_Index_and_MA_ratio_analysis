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

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
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


def _extract_records(payload: Any) -> List[Dict[str, Any]]:
    """Trích xuất danh sách bản ghi từ nhiều dạng cấu trúc JSON trả về của API."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "records", "result", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                for sub_key in ("items", "records", "data", "list"):
                    if isinstance(val.get(sub_key), list):
                        return val[sub_key]
    return []


class YuantaClient:
    """Client giao tiếp với hệ thống API Yuanta Securities."""

    def __init__(self, timeout: tuple[int, int] = (5, 20), rate_limit_delay: float = 0.05):
        self.session = _build_session()
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay

    def _get_json(self, url: str, params: Dict[str, Any]) -> Any:
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"[YuantaClient] TIMEOUT khi gọi {url} params={params}")
            return None
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            print(f"[YuantaClient] HTTP {status} khi gọi {url} params={params}: {exc}")
            return None
        except requests.exceptions.RequestException as exc:
            print(f"[YuantaClient] Lỗi kết nối khi gọi {url}: {exc}")
            return None
        except ValueError as exc:
            print(f"[YuantaClient] Lỗi parse JSON từ {url}: {exc}")
            return None
        finally:
            if self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

    def get_index_symbols(self, index_code: str) -> List[str]:
        """
        Lấy danh sách mã cổ phiếu thuộc một chỉ số hoặc nhóm ngành từ Yuanta API:
        https://ysradar.yuanta.com.vn/api/v3/market_data/price_board/stock/list_stock_info?index_code={INDEX_CODE}
        """
        code = str(index_code).strip().upper()
        payload = self._get_json(LIST_STOCK_INFO_URL, {"index_code": code})
        records = _extract_records(payload)
        if not records:
            return []

        symbols: List[str] = []
        for item in records:
            if not isinstance(item, dict):
                continue
            sym = (
                item.get("symbol")
                or item.get("stockCode")
                or item.get("stock_code")
                or item.get("code")
                or item.get("ticker")
            )
            if sym:
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

        date_candidates = ["tradingdate", "trading_date", "date", "time", "datetime"]
        for cand in date_candidates:
            if cand in lower_cols:
                col_map[lower_cols[cand]] = "time"
                break

        for target, cands in {
            "open": ["open", "openprice", "open_price"],
            "high": ["high", "highprice", "high_price"],
            "low": ["low", "lowprice", "low_price"],
            "close": ["close", "closeprice", "close_price", "adjclose", "adj_close"],
            "volume": ["volume", "totalvolume", "total_volume", "vol", "matchvolume"],
        }.items():
            for cand in cands:
                if cand in lower_cols:
                    col_map[lower_cols[cand]] = target
                    break

        df = df.rename(columns=col_map)
        if "time" not in df.columns or "close" not in df.columns:
            return pd.DataFrame()

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
            "foreign_buy_vol": ["foreignbuyvol", "foreign_buy_vol", "buyvol", "buy_vol", "f_buy_vol"],
            "foreign_sell_vol": ["foreignsellvol", "foreign_sell_vol", "sellvol", "sell_vol", "f_sell_vol"],
            "foreign_net_vol": ["foreignnetvol", "foreign_net_vol", "netvol", "net_vol", "f_net_vol"],
            "foreign_buy_val": ["foreignbuyval", "foreign_buy_val", "buyval", "buy_val", "f_buy_val"],
            "foreign_sell_val": ["foreignsellval", "foreign_sell_val", "sellval", "sell_val", "f_sell_val"],
            "foreign_net_val": ["foreignnetval", "foreign_net_val", "netval", "net_val", "f_net_val"],
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
