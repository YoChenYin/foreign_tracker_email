"""
持續運行服務：Flask 保持 container 存活，background thread 定時觸發。

排程：每個交易日 17:30 台灣時間自動執行。
手動觸發：GET /run
健康檢查：GET / 或 GET /health
診斷查詢：GET /trades
"""
import logging
import os
import threading
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import Flask, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)

app    = Flask(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")

_last_run: dict = {}


def run_daily():
    global _last_run
    import main as tracker
    import storage

    logging.info("=== 開始每日外資追蹤 ===")
    _last_run = {"status": "running", "started_at": str(datetime.now(TAIPEI))}

    try:
        cfg         = tracker.load_config()
        storage.init_db()
        broker_meta = tracker.build_broker_meta(cfg["branches"])
        target_ids  = set(broker_meta.keys())
        threshold   = cfg.get("alert_threshold_wan", 2000)
        top_n       = cfg.get("scan_top_n", 200)
        delay       = float(cfg.get("request_delay_seconds", 1.0))

        run_date_env = os.getenv("RUN_DATE", "").strip()
        target_date  = date.fromisoformat(run_date_env) if run_date_env else date.today()

        ok = tracker.sync_date(target_date, target_ids, broker_meta, top_n, delay)
        if not ok:
            logging.warning("資料同步失敗，略過發信")
            _last_run = {"status": "sync_failed", "date": str(target_date),
                         "at": str(datetime.now(TAIPEI))}
            return

        tracker.build_and_send(
            report_date=target_date,
            broker_meta=broker_meta,
            email_cfg=cfg["email"],
            threshold=threshold,
            no_email=False,
        )

        _last_run = {"status": "ok", "date": str(target_date), "at": str(datetime.now(TAIPEI))}
        logging.info(f"=== 執行完成：{target_date} ===")

    except Exception as e:
        logging.exception("執行失敗")
        _last_run = {"status": "error", "error": str(e), "at": str(datetime.now(TAIPEI))}


def scheduler_loop():
    """每 30 秒檢查一次，到了台灣時間週一到週五 17:30 就執行。"""
    logging.info("排程執行緒已啟動，每 30 秒檢查時間")
    last_run_date = None

    while True:
        try:
            now   = datetime.now(TAIPEI)
            today = now.date()
            if (now.weekday() < 5
                    and now.hour == 22
                    and now.minute == 0
                    and last_run_date != today):
                last_run_date = today
                run_daily()
        except Exception:
            logging.exception("排程執行緒發生錯誤")
        time.sleep(30)


# ── Flask endpoints ───────────────────────────────────────────

@app.route("/")
@app.route("/health")
def health():
    return jsonify({"status": "ok", "date": str(date.today()), "last_run": _last_run})


@app.route("/probe-histock")
def probe_histock():
    """診斷用：測試 histock branch.aspx 能否正常抓到今日分點資料（用台積電 2330）。"""
    import fetcher
    stock_code = "2330"
    result = fetcher.probe_histock(stock_code, date.today())
    return jsonify(result)


@app.route("/trades")
def trades():
    """診斷用：列出今日 DB 裡的所有交易記錄（不套門檻）。"""
    import storage
    import main as tracker
    cfg        = tracker.load_config()
    broker_meta = tracker.build_broker_meta(cfg["branches"])
    rows = storage.get_today_trades(date.today(), list(broker_meta.keys()))
    return jsonify({"date": str(date.today()), "count": len(rows), "trades": rows})


@app.route("/run", methods=["GET", "POST"])
def trigger():
    """手動觸發（不阻塞 HTTP 回應）。"""
    t = threading.Thread(target=run_daily, daemon=True)
    t.start()
    run_date = os.getenv("RUN_DATE") or str(date.today())
    logging.info(f"/run 觸發：{run_date}")
    return jsonify({"status": "triggered", "date": run_date})


# ── 啟動 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.daemon = True
    t.start()

    port = int(os.getenv("PORT", 8080))
    logging.info(f"Flask 啟動 port={port}，排程執行緒已就緒")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
