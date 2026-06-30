#!/usr/bin/env python3
"""
外資分點持股追蹤主程式（histock.tw 分點資料）

邏輯：掃成交量前 N 大股票，找出目標分點當日買賣的所有標的。
  - watch_side=buy  → 追蹤囤貨分點當日買進張數
  - watch_side=sell → 追蹤出貨分點當日賣出張數
  - watch_side=net  → 追蹤綜合贏家淨買賣張數

注意：histock 資料約於每日 21:00 後上傳，系統排程設於 22:00。

用法：
  python main.py                      # 抓今日資料並發信
  python main.py --date 2026-06-10    # 抓指定日期並發信
  python main.py --backfill           # 從 config start_date 回補至今
  python main.py --report             # 僅寄今日報告，不抓新資料
  python main.py --no-email           # 抓資料但不寄信（存 HTML）
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
    target_ids: set[str],
    broker_meta: dict[str, dict],
    top_n: int = 200,
    delay: float = 1.0,
) -> bool:
    """掃成交量前 top_n 大股票，找出目標分點在指定日期的交易記錄。
    回傳 False 表示無法取得股票清單。
    """
    all_stocks = fetcher.get_top_stocks_by_volume(top_n)
    if not all_stocks:
        print("  [warn] 無法取得股票清單")
        return False

    print(f"  掃描前 {len(all_stocks)} 大成交量股票（histock 前 30 分點）")
    fetcher.init_session()
    matched = 0

    for i, stock in enumerate(all_stocks):
        code = stock["code"]
        name = stock["name"]

        if storage.is_synced(d, code):
            continue

        records = fetcher.fetch_broker_trades(code, d)
        filtered = []
        for r in records:
            bid = r.get("broker_id", "")
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
                sign   = "+" if r["net_lots"] > 0 else ""
                print(f"    {code} {name} | [{ws_tag}]{r['broker_name']}({r['broker_id']}): "
                      f"買{r['buy_lots']} 賣{r['sell_lots']} 淨{sign}{r['net_lots']}")

        if (i + 1) % 50 == 0:
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
    trades     = storage.get_today_trades(report_date, target_ids)
    positions  = storage.get_positions(target_ids)

    html = reporter.build_html_report(
        report_date=report_date,
        trades=trades,
        positions=positions,
        broker_meta=broker_meta,
        threshold_wan=threshold,
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
    args = parser.parse_args()

    cfg = load_config()
    storage.init_db()

    broker_meta = build_broker_meta(cfg["branches"])
    target_ids  = set(broker_meta.keys())

    email_cfg = cfg["email"]
    threshold = cfg.get("alert_threshold_wan", 2000)
    top_n     = cfg.get("scan_top_n", 200)
    delay     = float(cfg.get("request_delay_seconds", 1.0))

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
            ok = sync_date(d, target_ids, broker_meta, top_n, delay)
            if not ok:
                print("  中止回補，請確認資料源狀態後重試")
                break
        else:
            print("回補完成。")
        return

    if not args.report:
        print(f"[{target_date}] 掃描資料...")
        sync_date(target_date, target_ids, broker_meta, top_n, delay)

    build_and_send(
        report_date=target_date,
        broker_meta=broker_meta,
        email_cfg=email_cfg,
        threshold=threshold,
        no_email=args.no_email,
    )


if __name__ == "__main__":
    main()
