"""資料抓取模組：FinMind TaiwanStockTradingDailyReport + TWSE 公用工具"""
import re
import time
import requests
from datetime import date

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"


# ──────────────────────────────────────────────
# TWSE 公用工具
# ──────────────────────────────────────────────

def get_top_stocks_by_volume(top_n: int = 200) -> list[dict]:
    """從 TWSE openapi 取得當日成交量前 N 大的上市普通股。回傳 list of {'code', 'name'}"""
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
# FinMind API
# ──────────────────────────────────────────────

def check_finmind_access(token: str) -> bool:
    """回傳 True 表示 token 有效且有 TaiwanStockTradingDailyReport 權限。"""
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
        return resp.json().get("status") == 200
    except Exception:
        return False


def fetch_broker_trades(stock_code: str, trade_date: date, token: str) -> list[dict]:
    """取得指定股票在指定日期所有分點的進出資料。
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
                "broker_id":   str(row.get("securities_trader_id", "")).strip(),
                "broker_name": str(row.get("securities_trader", "")).strip(),
                "buy_lots":  buy,
                "sell_lots": sell,
                "net_lots":  buy - sell,
            })
        except (ValueError, TypeError):
            continue
    return records


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
