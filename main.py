#!/usr/bin/env python3
"""
外資分點持股追蹤主程式

邏輯：以「分點」為主，掃成交量前 N 大股票，找出目標分點當日買賣的所有標的。
  - watch_side=buy  → 追蹤囤貨分點當日買進張數
  - watch_side=sell → 追蹤出貨分點當日賣出張數
  - watch_side=net  → 追蹤綜合贏家淨買賣張數

用法：
  python main.py                      # 抓今日資料並發信
  python main.py --date 2026-06-10    # 抓指定日期並發信
  python main.py --backfill           # 從 config start_date 回補至今
  python main.py --report             # 僅寄今日報告，不抓新資料
  python main.py --no-email           # 抓資料但不寄信（存 HTML）
  python main.py --list-brokers       # 印出可用的外資分點代碼
"""
import argparse
import time
from datetime import date
from pathlib import Path

import yaml

import fetcher
import storage
import reporter
import emailer


def load_config() -> dict:
    import os
    cfg_path = Path(__file__).parent / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 環境變數優先（Zeabur / Docker 部署用）
    if os.getenv("FINMIND_TOKEN"):
        cfg["finmind_token"] = os.getenv("FINMIND_TOKEN")
    if os.getenv("GMAIL_SENDER"):
        cfg["email"]["sender"] = os.getenv("GMAIL_SENDER")
    if os.getenv("GMAIL_APP_PASSWORD"):
        cfg["email"]["app_password"] = os.getenv("GMAIL_APP_PASSWORD")
    if os.getenv("GMAIL_RECIPIENTS"):
        cfg["email"]["recipients"] = os.getenv("GMAIL_RECIPIENTS").split(",")
    return cfg


def build_broker_meta(branches: list[dict]) -> dict[str, dict]:
    """將 config branches 轉為 {id: {name, emoji, labels, watch_side, group, desc}}。"""
    return {
        b["id"]: {
            "name":       b["name"],
            "emoji":      b.get("emoji", ""),
            "labels":     b.get("labels", []),
            "watch_side": b.get("watch_side", "net"),
            "group":      b.get("group", ""),
            "desc":       b.get("desc", ""),
        }
        for b in branches
    }


def sync_date(
    d: date,
    token: str,
    use_finmind: bool,
    target_ids: set[str],
    broker_meta: dict[str, dict],
    top_n: int = 10,
    delay: float = 2.0,
) -> bool:
    """
    histock 模式：以分點為主，每分點抓前 top_n 大成交個股（broker-first）。
    FinMind 模式：以個股為主，掃成交量前 top_n 大股票（stock-first）。
    回傳 False 表示資料源不可用或同步失敗。
    """
    if use_finmind:
        return _sync_stock_first(d, token, target_ids, broker_meta, top_n, delay)
    return _sync_broker_first(d, target_ids, broker_meta, top_n, delay)


def _sync_broker_first(
    d: date,
    target_ids: set[str],
    broker_meta: dict[str, dict],
    top_n: int = 10,
    delay: float = 2.0,
) -> bool:
    """histock 免費模式：以分點為主，每分點抓前 top_n 大成交個股。"""
    unsynced = [bid for bid in sorted(target_ids)
                if not storage.is_synced(d, f"_b_{bid}")]

    if not unsynced:
        print(f"  所有 {len(target_ids)} 個分點今日已同步")
        return True

    print("  確認 histock.tw 可用性...", end=" ", flush=True)
    if not fetcher.is_histock_available(d):
        print("❌ 暫時無法存取（IP rate-limited 或資料尚未更新），請稍後再試")
        return False
    print("OK")

    print(f"  掃描 {len(unsynced)} 個分點，每分點取前 {top_n} 大成交個股")
    total_records = 0

    for broker_id in unsynced:
        meta    = broker_meta[broker_id]
        ws_tag  = {"buy": "囤", "sell": "出", "net": "贏"}.get(meta["watch_side"], "")
        records = fetcher.fetch_stocks_by_broker_histock(broker_id, d, top_n=top_n)

        for r in records:
            if r["buy_lots"] == 0 and r["sell_lots"] == 0:
                continue
            r["broker_id"]   = broker_id
            r["broker_name"] = meta["name"]
            r["watch_side"]  = meta["watch_side"]
            storage.save_trades(d, r["stock_code"], r["stock_name"], [r], mark_synced=False)
            sign = "+" if r["net_lots"] > 0 else ""
            print(f"    [{ws_tag}]{meta['name']}({broker_id}) "
                  f"{r['stock_code']} {r['stock_name']}: "
                  f"買{r['buy_lots']} 賣{r['sell_lots']} 淨{sign}{r['net_lots']}")
            total_records += 1

        if not records:
            print(f"    [{ws_tag}]{meta['name']}({broker_id}): 今日無交易記錄")

        storage.mark_synced(d, f"_b_{broker_id}")
        time.sleep(delay)

    print(f"  掃描完成：共 {total_records} 筆目標分點交易記錄")
    return True


def _sync_stock_first(
    d: date,
    token: str,
    target_ids: set[str],
    broker_meta: dict[str, dict],
    top_n: int = 200,
    delay: float = 0.3,
) -> bool:
    """FinMind 付費模式：以個股為主，掃成交量前 top_n 大股票的完整分點資料。"""
    all_stocks = fetcher.get_top_stocks_by_volume(top_n)
    if not all_stocks:
        print("  [warn] 無法取得股票清單")
        return False

    print(f"  掃描前 {len(all_stocks)} 大成交量股票（FinMind 完整分點）")
    matched = 0

    for i, stock in enumerate(all_stocks):
        code = stock["code"]
        name = stock["name"]

        if storage.is_synced(d, code):
            continue

        records = fetcher.fetch_broker_trades_finmind(code, d, token)
        filtered = []
        for r in records:
            bid = r.get("broker_id") or r.get("broker_code", "")
            if bid not in target_ids:
                continue
            if r["buy_lots"] == 0 and r["sell_lots"] == 0:
                continue
            meta = broker_meta[bid]
            r["broker_name"] = meta["name"]
            r["broker_id"]   = bid
            r["watch_side"]  = meta["watch_side"]
            filtered.append(r)

        storage.save_trades(d, code, name, filtered)
        if filtered:
            matched += 1
            for r in filtered:
                ws_tag = {"buy": "囤", "sell": "出", "net": "贏"}.get(r["watch_side"], "")
                sign = "+" if r["net_lots"] > 0 else ""
                print(f"    {code} {name} | [{ws_tag}]{r['broker_name']}({r['broker_id']}): "
                      f"買{r['buy_lots']} 賣{r['sell_lots']} 淨{sign}{r['net_lots']}")

        if (i + 1) % 100 == 0:
            print(f"  ... 已掃 {i+1}/{len(all_stocks)} 檔，目前 {matched} 檔有目標分點")
        time.sleep(delay)

    print(f"  掃描完成：{matched} 檔股票有目標分點交易記錄")
    return True


def build_and_send(
    report_date: date,
    broker_meta: dict[str, dict],
    email_cfg: dict,
    threshold: int,
    no_email: bool,
):
    target_ids = list(broker_meta.keys())
    trades    = storage.get_today_trades(report_date, target_ids)
    positions = storage.get_positions(target_ids)

    html = reporter.build_html_report(
        report_date=report_date,
        trades=trades,
        positions=positions,
        broker_meta=broker_meta,
        threshold_lots=threshold,
    )

    if no_email:
        out_path = Path(__file__).parent / f"report_{report_date}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"報告已存至 {out_path}")
    else:
        subject = f"{email_cfg['subject_prefix']} {report_date} 外資分點日報"
        emailer.send_report(
            sender=email_cfg["sender"],
            app_password=email_cfg["app_password"].strip(),
            recipients=email_cfg["recipients"],
            subject=subject,
            html_body=html,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",         help="指定抓取日期（YYYY-MM-DD），預設為今日")
    parser.add_argument("--backfill",     action="store_true", help="從 start_date 回補至今")
    parser.add_argument("--report",       action="store_true", help="僅寄報告，不抓新資料")
    parser.add_argument("--no-email",     action="store_true", help="不寄信，存 HTML 檔")
    parser.add_argument("--list-brokers", action="store_true", help="列出可用外資分點代碼")
    args = parser.parse_args()

    cfg = load_config()
    storage.init_db()

    token       = cfg.get("finmind_token", "").strip()
    broker_meta = build_broker_meta(cfg["branches"])
    target_ids  = set(broker_meta.keys())

    if args.list_brokers:
        print("從 FinMind TaiwanSecuritiesTraderInfo 查詢外資分點：")
        found = fetcher.discover_broker_ids(
            ["花旗", "摩根", "高盛", "瑞銀", "美林", "麥格理", "匯豐", "法銀", "野村"]
        )
        for bid, bname in sorted(found.items()):
            marker = " ← 已追蹤" if bid in target_ids else ""
            print(f"  {bid}: {bname}{marker}")
        return

    use_finmind = False
    if token:
        print("確認 FinMind 帳號權限...", end=" ", flush=True)
        use_finmind = fetcher.check_finmind_access(token)
        print("OK（完整分點資料）" if use_finmind else "免費層，改用 histock.tw")

    email_cfg = cfg["email"]
    threshold = cfg.get("alert_threshold_lots", 100)
    top_n     = cfg.get("scan_top_n", 10)
    delay     = float(cfg.get("request_delay_seconds", 2.0 if not use_finmind else 0.3))

    import os
    run_date_env = os.getenv("RUN_DATE", "").strip()
    target_date = (date.fromisoformat(args.date) if args.date
                   else date.fromisoformat(run_date_env) if run_date_env
                   else date.today())

    if args.backfill:
        start = date.fromisoformat(cfg["start_date"])
        end   = target_date
        print(f"回補 {start} ~ {end} 的交易資料...")
        trading_days = fetcher.get_trading_dates_in_range(start, end)
        print(f"共 {len(trading_days)} 個交易日")
        for d in trading_days:
            print(f"\n[{d}]")
            ok = sync_date(d, token, use_finmind, target_ids, broker_meta, top_n, delay)
            if not ok:
                print("  中止回補，請確認資料源狀態後重試")
                break
        else:
            print("回補完成。")
        return

    if not args.report:
        print(f"[{target_date}] 掃描資料...")
        sync_date(target_date, token, use_finmind, target_ids, broker_meta, top_n, delay)

    build_and_send(
        report_date=target_date,
        broker_meta=broker_meta,
        email_cfg=email_cfg,
        threshold=threshold,
        no_email=args.no_email,
    )


if __name__ == "__main__":
    main()
