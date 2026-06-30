"""
資料抓取模組，支援兩種資料源：

免費模式（預設）：histock.tw branch.aspx
  - 每檔股票顯示買賣超前 30 名分點
  - 掃描 TWSE 成交量前 N 大股票（外資主要交易大型股）

付費模式：FinMind TaiwanStockTradingDailyReport
  - 完整覆蓋所有分點、所有股票
  - 需要 FinMind paid token
"""
import re
import time
import requests
from datetime import date

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
HISTOCK_BRANCH_URL = "https://histock.tw/stock/branch.aspx"
HISTOCK_BROKER_URL = "https://histock.tw/stock/broker.aspx"


# ──────────────────────────────────────────────
# TWSE 公用工具
# ──────────────────────────────────────────────

def get_top_stocks_by_volume(top_n: int = 200) -> list[dict]:
    """
    從 TWSE openapi 取得當日（或最近一日）成交量前 N 大的上市普通股。
    回傳 list of {'code': str, 'name': str}
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [get_top_stocks error] {e}")
        return []

    stocks = []
    for row in data:
        code = row.get("Code", "").strip()
        name = row.get("Name", "").strip()
        try:
            vol = int(row.get("TradeVolume", "0").replace(",", ""))
        except ValueError:
            vol = 0
        # 只保留 4 位數代碼的普通股
        if code and name and code.isdigit() and len(code) == 4:
            stocks.append({"code": code, "name": name, "volume": vol})

    stocks.sort(key=lambda x: x["volume"], reverse=True)
    return [{"code": s["code"], "name": s["name"]} for s in stocks[:top_n]]


def get_trading_dates_in_range(start: date, end: date) -> list[date]:
    """透過 TWSE STOCK_DAY 取得日期範圍內所有交易日。"""
    trading_days: set[date] = set()
    current = date(start.year, start.month, 1)

    while current <= end:
        try:
            resp = requests.get(
                "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                params={"response": "json", "date": current.strftime("%Y%m%d"), "stockNo": "2330"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            data = resp.json()
            if data.get("stat") == "OK":
                for row in data.get("data", []):
                    try:
                        parts = row[0].split("/")
                        d = date(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))
                        if start <= d <= end:
                            trading_days.add(d)
                    except (IndexError, ValueError):
                        continue
        except Exception as e:
            print(f"  [trading dates error] {current}: {e}")

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
        time.sleep(0.3)

    return sorted(trading_days)


# ──────────────────────────────────────────────
# 免費模式：histock.tw
# ──────────────────────────────────────────────

def _new_histock_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    # 先打首頁建立 Session cookie
    try:
        s.get("https://histock.tw/", timeout=10)
    except Exception:
        pass
    return s


_histock_session: requests.Session | None = None
_histock_rate_limited: bool = False

# histock 頁面有資料時至少 50000 bytes（實測約 78K，預留 buffer）
_HISTOCK_MIN_DATA_SIZE = 50_000


def _get_histock_session() -> requests.Session:
    global _histock_session
    if _histock_session is None:
        _histock_session = _new_histock_session()
    return _histock_session


def is_histock_available(trade_date: date) -> bool:
    """
    以台積電（2330）為探針，確認 histock.tw 當前是否能回傳分點資料。
    若 IP 被 rate-limit 會回傳 False。
    """
    global _histock_rate_limited
    s = _get_histock_session()
    date_str = trade_date.strftime("%Y%m%d")
    try:
        r = s.get(
            HISTOCK_BRANCH_URL,
            params={"no": "2330", "from": date_str, "to": date_str},
            timeout=15,
        )
        has_data = len(r.text) >= _HISTOCK_MIN_DATA_SIZE
        _histock_rate_limited = not has_data
        return has_data
    except Exception:
        _histock_rate_limited = True
        return False


def fetch_broker_trades_histock(stock_code: str, trade_date: date) -> list[dict]:
    """
    histock.tw 免費模式：取得指定股票在指定日期的前 30 名分點買賣資料。
    回傳 list of {'broker_id', 'broker_name', 'buy_lots', 'sell_lots', 'net_lots'}
    若頁面 < 85K（rate-limited 或無資料）回傳 []。
    """
    if _histock_rate_limited:
        return []
    date_str = trade_date.strftime("%Y%m%d")
    s = _get_histock_session()
    try:
        resp = s.get(
            HISTOCK_BRANCH_URL,
            params={"no": stock_code, "from": date_str, "to": date_str},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [histock error] {stock_code} {date_str}: {e}")
        return []

    if len(resp.text) < _HISTOCK_MIN_DATA_SIZE:
        return []

    return _parse_histock_table(resp.text)


def _parse_histock_table(html: str) -> list[dict]:
    """解析 histock.tw branch.aspx 的分點表格（每行 10 欄 = 左右各 5）"""
    records = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 8:
            continue
        for offset in (0, 5):
            if offset + 4 >= len(cells):
                break
            name_cell = cells[offset]
            bno_match = re.search(r"bno=([^&\"']+)", name_cell)
            if not bno_match:
                continue
            broker_id = bno_match.group(1).strip()
            broker_name = re.sub(r"<[^>]+>", "", name_cell).strip()
            if not broker_name:
                continue
            try:
                buy = int(cells[offset + 1].replace(",", "").strip())
                sell = int(cells[offset + 2].replace(",", "").strip())
                net = int(cells[offset + 3].replace(",", "").replace("+", "").strip())
            except (ValueError, IndexError):
                continue
            records.append({
                "broker_id": broker_id,
                "broker_name": broker_name,
                "buy_lots": buy,
                "sell_lots": sell,
                "net_lots": net,
            })
    return records


# ──────────────────────────────────────────────
# 免費模式：histock.tw（分點為主）
# ──────────────────────────────────────────────

def fetch_stocks_by_broker_histock(broker_id: str, trade_date: date, top_n: int = 10) -> list[dict]:
    """
    histock.tw 免費模式（分點為主）：取得指定分點在指定日期的前 top_n 大成交個股。
    回傳 list of {'stock_code', 'stock_name', 'buy_lots', 'sell_lots', 'net_lots'}
    已依總成交量（buy+sell）降冪排序，取前 top_n 筆。
    """
    if _histock_rate_limited:
        return []
    date_str = trade_date.strftime("%Y%m%d")
    s = _get_histock_session()
    try:
        resp = s.get(
            HISTOCK_BROKER_URL,
            params={"no": broker_id, "from": date_str, "to": date_str},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [histock broker error] {broker_id} {date_str}: {e}")
        return []

    records = _parse_histock_broker_table(resp.text)
    records.sort(key=lambda r: r["buy_lots"] + r["sell_lots"], reverse=True)
    return records[:top_n]


def _parse_histock_broker_table(html: str) -> list[dict]:
    """
    解析 histock.tw broker.aspx 的個股交易表格。
    每列格式：[排名] 股票代號/名稱  買進(張)  賣出(張)  買賣超(張)
    股票代號以 <a href="...no=XXXX..."> 或純文字 4 位數呈現。
    """
    records = []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(cells) < 4:
            continue

        # 找含股票代號的 cell（優先抓連結，備案抓純文字 4 位數）
        stock_code = None
        code_idx = -1
        for idx, cell in enumerate(cells):
            m = re.search(r'(?:no|stockno|stock_no)=(\d{4,6})', cell, re.IGNORECASE)
            if not m:
                plain = re.sub(r"<[^>]+>", "", cell).strip()
                m = re.fullmatch(r"(\d{4})", plain)
            if m:
                candidate = m.group(1)
                if candidate.isdigit() and len(candidate) == 4:
                    stock_code = candidate
                    code_idx = idx
                    break

        if not stock_code:
            continue

        # 股票名稱：先嘗試從同格取文字，若空則看下一格
        stock_name = re.sub(r"<[^>]+>", "", cells[code_idx]).strip()
        remaining = cells[code_idx + 1:]
        if not stock_name and remaining:
            candidate_name = re.sub(r"<[^>]+>", "", remaining[0]).strip()
            if not re.match(r"^\d", candidate_name):   # 名稱不以數字開頭
                stock_name = candidate_name
                remaining = remaining[1:]

        # 從剩餘 cell 依序讀買進、賣出、淨買賣
        nums = []
        for cell in remaining:
            text = re.sub(r"<[^>]+>", "", cell).strip().replace(",", "").replace("+", "")
            if re.match(r"^-?\d+$", text):
                nums.append(int(text))
            elif text in ("", "-", "—", "－"):
                nums.append(0)
            if len(nums) >= 3:
                break

        if len(nums) < 2:
            continue

        buy  = nums[0]
        sell = nums[1]
        net  = nums[2] if len(nums) > 2 else buy - sell

        if buy == 0 and sell == 0:
            continue

        records.append({
            "stock_code": stock_code,
            "stock_name": stock_name or stock_code,
            "buy_lots":   buy,
            "sell_lots":  sell,
            "net_lots":   net,
        })
    return records


# ──────────────────────────────────────────────
# 付費模式：FinMind
# ──────────────────────────────────────────────

def check_finmind_access(token: str) -> bool:
    """
    回傳 True 表示 token 有效且帳號有 TaiwanStockTradingDailyReport 權限。
    只需在啟動時呼叫一次。
    """
    try:
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockTradingDailyReport",
                "data_id": "2330",
                "start_date": "2026-06-10",
                "end_date": "2026-06-10",
                "token": token,
            },
            timeout=15,
        )
        data = resp.json()
        return data.get("status") == 200
    except Exception:
        return False


def fetch_broker_trades_finmind(stock_code: str, trade_date: date, token: str) -> list[dict]:
    """
    FinMind 付費模式：取得指定股票在指定日期所有分點的進出資料。
    回傳 list of {'broker_id', 'broker_name', 'buy_lots', 'sell_lots', 'net_lots'}
    """
    date_str = trade_date.isoformat()
    try:
        resp = requests.get(
            FINMIND_URL,
            params={
                "dataset": "TaiwanStockTradingDailyReport",
                "data_id": stock_code,
                "start_date": date_str,
                "end_date": date_str,
                "token": token,
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        print(f"  [FinMind error] {stock_code} {date_str}: {e}")
        return []

    if payload.get("status") != 200:
        msg = payload.get("msg", "")
        if "level" not in msg.lower():
            print(f"  [FinMind {stock_code}] {msg[:80]}")
        return []

    records = []
    for row in payload.get("data", []):
        try:
            buy = int(row.get("buy", 0) or 0)
            sell = int(row.get("sell", 0) or 0)
            records.append({
                "broker_id": str(row.get("securities_trader_id", "")).strip(),
                "broker_name": str(row.get("securities_trader", "")).strip(),
                "buy_lots": buy,
                "sell_lots": sell,
                "net_lots": buy - sell,
            })
        except (ValueError, TypeError):
            continue
    return records


# ──────────────────────────────────────────────
# 統一入口（根據 token 自動選擇模式）
# ──────────────────────────────────────────────

def fetch_broker_trades(stock_code: str, trade_date: date, token: str = "", use_finmind: bool = False) -> list[dict]:
    """
    自動選擇資料源：
    - use_finmind=True（啟動時已確認帳號有權限） → FinMind
    - 否則 → histock.tw（免費，前 30 分點）
    """
    if use_finmind and token:
        return fetch_broker_trades_finmind(stock_code, trade_date, token)
    return fetch_broker_trades_histock(stock_code, trade_date)


def discover_broker_ids(name_keywords: list[str]) -> dict[str, str]:
    """從 TaiwanSecuritiesTraderInfo（免費）搜尋目標分點，回傳 {id: name}。"""
    try:
        resp = requests.get(
            FINMIND_URL,
            params={"dataset": "TaiwanSecuritiesTraderInfo", "token": ""},
            timeout=15,
        )
        data = resp.json().get("data", [])
    except Exception:
        return {}

    pattern = "|".join(name_keywords)
    return {
        str(row["securities_trader_id"]): row["securities_trader"]
        for row in data
        if re.search(pattern, row.get("securities_trader", ""))
    }
