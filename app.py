"""
持續運行服務：Flask 保持 container 存活，APScheduler 定時觸發。

排程：每個交易日 17:30 台灣時間（09:30 UTC）自動執行。
手動觸發測試：POST /run  或  GET /run
健康檢查：GET /health
"""
import logging
import os
import threading
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, jsonify

import fetcher
import main as tracker
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")


def run_daily():
    """每日執行邏輯，等同 python main.py。"""
    cfg = tracker.load_config()
    storage.init_db()

    broker_meta = tracker.build_broker_meta(cfg["branches"])
    target_ids  = set(broker_meta.keys())
    token       = cfg.get("finmind_token", "").strip()

    use_finmind = False
    if token:
        logging.info("確認 FinMind 帳號權限...")
        use_finmind = fetcher.check_finmind_access(token)

    threshold = cfg.get("alert_threshold_lots", 100)
    top_n     = cfg.get("scan_top_n", 200)
    delay     = float(cfg.get("request_delay_seconds", 2.0 if not use_finmind else 0.3))

    run_date_env = os.getenv("RUN_DATE", "").strip()
    target_date  = date.fromisoformat(run_date_env) if run_date_env else date.today()

    logging.info(f"開始抓取 {target_date}...")
    try:
        tracker.sync_date(target_date, token, use_finmind, target_ids, broker_meta, top_n, delay)
        tracker.build_and_send(
            report_date=target_date,
            broker_meta=broker_meta,
            email_cfg=cfg["email"],
            threshold=threshold,
            no_email=False,
        )
        logging.info(f"{target_date} 執行完成")
    except Exception:
        logging.exception("執行失敗")


# ── Flask endpoints ───────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "date": str(date.today())})


@app.route("/run", methods=["GET", "POST"])
def trigger():
    """手動觸發（測試用），背景執行不阻塞 HTTP 回應。"""
    t = threading.Thread(target=run_daily, daemon=True)
    t.start()
    run_date = os.getenv("RUN_DATE") or str(date.today())
    logging.info(f"/run 觸發，日期：{run_date}")
    return jsonify({"status": "triggered", "date": run_date})


# ── 啟動 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_daily,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=30, timezone=TAIPEI),
        id="daily_report",
        coalesce=True,          # 多次 misfire 只補跑一次
        misfire_grace_time=60,  # 啟動時距預定時間超過 60 秒則不補跑
    )
    scheduler.start()
    logging.info("排程已啟動：每個交易日 17:30（台灣時間）自動執行")

    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
