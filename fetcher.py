"""資料抓取模組：histock.tw 分點 + TWSE 公用工具

histock branch.aspx 每檔股票顯示當日買賣最多的前 30 名分點（HTML table）。
資料約於收盤後 7-8 小時上傳（21:00 後可用），排程請設 22:00。
"""
import re
import time
import requests
from bs4 import BeautifulSoup
from datetime import date

_HISTOCK_BASE = "https://histock.tw"
_HISTOCK_BRANCH = _HISTOCK_BASE + "/stock/branch.aspx"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# module-level session；由 init_session() 初始化
_session: requests.Session | None = None


def init_session():
    """建立 histock session（拿 cookie），程式啟動時呼叫一次即可。"""
    global _session
    s = requests.Session()
    s.headers.update(_HEADERS)
    s.get(_HISTOCK_BASE, timeout=10)
    _session = s


def _get_session() -> requests.Session:
    if _session is None:
        init_session()
    return _session


# ──────────────────────────────────────────────
# TWSE 公用工具
# ──────────────────────────────────────────────

def get_top_stocks_by_volume(top_n: int = 200) -> list[dict]:
    """從 TWSE openapi 取得當日成交量前 N 大的上市普通股。回傳 list of {'code', 'name'}"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=20)
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
                headers=_HEADERS,
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
# histock 分點資料
# ──────────────────────────────────────────────

def fetch_broker_trades(stock_code: str, trade_date: date) -> list[dict]:
    """從 histock branch.aspx 抓取指定股票、日期的前 30 名分點進出資料。
    回傳 list of {'broker_id', 'broker_name', 'buy_lots', 'sell_lots', 'net_lots'}
    """
    date_str = trade_date.strftime("%Y%m%d")
    url = f"{_HISTOCK_BRANCH}?no={stock_code}&d={date_str}"

    try:
        r = _get_session().get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  [histock error] {stock_code} {date_str}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="tbChip")
    if not table:
        return []

    records = []
    for row in table.find_all("tr")[1:]:  # 略過表頭
        cells = row.find_all("td")
        if len(cells) < 10:
            continue

        links = row.find_all("a", href=re.compile(r"bno="))
        bnos = [re.search(r"bno=(\w+)", a["href"]).group(1) for a in links
                if re.search(r"bno=", a["href"])]

        def _parse_lots(cell) -> int:
            txt = cell.get_text(strip=True).replace(",", "")
            try:
                return int(txt)
            except ValueError:
                return 0

        def _parse_price(cell) -> float:
            txt = cell.get_text(strip=True).replace(",", "")
            try:
                return float(txt)
            except ValueError:
                return 0.0

        def _value(lots: int, price: float) -> int:
            """成交金額 (NT$)：張數 × 1000 股/張 × 均價"""
            return round(lots * 1000 * price)

        # 左欄分點（cells[0..4]）
        if len(bnos) >= 1:
            buy   = _parse_lots(cells[1])
            sell  = _parse_lots(cells[2])
            price = _parse_price(cells[4])
            records.append({
                "broker_id":   bnos[0],
                "broker_name": cells[0].get_text(strip=True),
                "buy_lots":    buy,
                "sell_lots":   sell,
                "net_lots":    buy - sell,
                "avg_price":   price,
                "buy_value":   _value(buy,  price),
                "sell_value":  _value(sell, price),
                "net_value":   _value(buy - sell, price),
            })

        # 右欄分點（cells[5..9]）
        if len(bnos) >= 2:
            buy   = _parse_lots(cells[6])
            sell  = _parse_lots(cells[7])
            price = _parse_price(cells[9])
            records.append({
                "broker_id":   bnos[1],
                "broker_name": cells[5].get_text(strip=True),
                "buy_lots":    buy,
                "sell_lots":   sell,
                "net_lots":    buy - sell,
                "avg_price":   price,
                "buy_value":   _value(buy,  price),
                "sell_value":  _value(sell, price),
                "net_value":   _value(buy - sell, price),
            })

    return records


def probe_histock(stock_code: str, trade_date: date) -> dict:
    """診斷用：回傳 histock 原始抓取狀況。"""
    date_str = trade_date.strftime("%Y%m%d")
    url = f"{_HISTOCK_BRANCH}?no={stock_code}&d={date_str}"
    try:
        r = _get_session().get(url, timeout=15)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="tbChip")
        rows = table.find_all("tr")[1:] if table else []
        all_bnos = re.findall(r"bno=(\w+)", html)
        return {
            "url": url,
            "status_code": r.status_code,
            "response_bytes": len(html),
            "table_found": table is not None,
            "data_rows": len(rows),
            "broker_count": len(set(all_bnos)),
            "bnos": sorted(set(all_bnos)),
        }
    except Exception as e:
        return {"url": url, "error": str(e)}
