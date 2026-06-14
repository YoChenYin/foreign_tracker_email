"""產生每日 HTML 報告（囤貨 / 出貨 / 贏家 三區塊 + 分點特性頁腳）"""
from datetime import date


# ── 小工具 ────────────────────────────────────────────────────

def _badge(label: str) -> str:
    colors = {
        "囤": ("#dcfce7", "#16a34a"),
        "出": ("#fee2e2", "#dc2626"),
        "贏": ("#fef9c3", "#ca8a04"),
        "作": ("#f3f4f6", "#6b7280"),
    }
    bg, fg = colors.get(label, ("#f3f4f6", "#374151"))
    return (f'<span style="background:{bg};color:{fg};padding:1px 5px;border-radius:3px;'
            f'font-size:11px;font-weight:bold;margin-right:2px">【{label}】</span>')


# ── 交易區塊（按分點分組）────────────────────────────────────

def _broker_stock_rows(rows: list[dict], metric_col: str, dynamic_color: bool) -> str:
    html = ""
    for t in sorted(rows, key=lambda x: x[metric_col], reverse=True):
        val = t[metric_col]
        if dynamic_color:
            color   = "#16a34a" if val >= 0 else "#dc2626"
            val_str = f"+{val:,}" if val > 0 else f"{val:,}"
        else:
            color   = "#16a34a" if metric_col == "buy_lots" else "#dc2626"
            val_str = f"{val:,}"
        html += (
            f'<tr style="border-bottom:1px solid #f3f4f6">'
            f'<td style="padding:4px 8px;color:#374151">{t["stock_code"]} {t["stock_name"]}</td>'
            f'<td style="padding:4px 8px;text-align:right">{t["buy_lots"]:,}</td>'
            f'<td style="padding:4px 8px;text-align:right">{t["sell_lots"]:,}</td>'
            f'<td style="padding:4px 8px;text-align:right;color:{color};font-weight:bold">{val_str}</td>'
            f'</tr>'
        )
    return html


def _trade_section(title: str, trades: list[dict], metric_col: str,
                   metric_label: str, broker_meta: dict,
                   dynamic_color: bool = False) -> str:
    if not trades:
        empty = ('<p style="color:#9ca3af;font-size:12px;margin:4px 0 16px">'
                 '今日無達門檻記錄</p>')
        return f'<h3 style="margin:22px 0 4px;font-size:14px">{title}</h3>{empty}'

    # 按分點分組，分點本身以「該組 metric 合計」排序
    by_broker: dict[str, list[dict]] = {}
    for t in trades:
        by_broker.setdefault(t["broker_code"], []).append(t)

    broker_order = sorted(
        by_broker.keys(),
        key=lambda bid: sum(r[metric_col] for r in by_broker[bid]),
        reverse=True,
    )

    body = ""
    for bid in broker_order:
        rows  = by_broker[bid]
        meta  = broker_meta.get(bid, {})
        emoji = meta.get("emoji", "")
        name  = meta.get("name", bid)
        total = sum(r[metric_col] for r in rows)
        t_color = "#16a34a" if (dynamic_color and total >= 0) or metric_col == "buy_lots" else "#dc2626"
        t_str   = f"+{total:,}" if dynamic_color and total > 0 else f"{total:,}"

        body += (
            f'<tr style="background:#f9fafb">'
            f'<td colspan="3" style="padding:6px 8px;font-weight:600;font-size:12px;color:#374151">'
            f'{emoji} {name}（{bid}）</td>'
            f'<td style="padding:6px 8px;text-align:right;font-weight:700;font-size:12px;color:{t_color}">'
            f'{t_str} 張</td>'
            f'</tr>'
        )
        body += _broker_stock_rows(rows, metric_col, dynamic_color)

    return f"""
  <h3 style="margin:22px 0 5px;font-size:14px">{title}</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead>
      <tr style="background:#e5e7eb;border-bottom:2px solid #d1d5db">
        <th style="padding:6px 8px;text-align:left">個股</th>
        <th style="padding:6px 8px;text-align:right">買進（張）</th>
        <th style="padding:6px 8px;text-align:right">賣出（張）</th>
        <th style="padding:6px 8px;text-align:right">{metric_label}</th>
      </tr>
    </thead>
    <tbody>{body}</tbody>
  </table>"""


# ── 累積持倉 ──────────────────────────────────────────────────

def _positions_html(positions: list[dict], broker_meta: dict) -> str:
    by_broker: dict[str, list[dict]] = {}
    for p in positions:
        by_broker.setdefault(p["broker_code"], []).append(p)

    if not by_broker:
        return '<p style="color:#9ca3af;font-size:13px">目前無累積持倉記錄</p>'

    html = ""
    for bid, plist in by_broker.items():
        meta  = broker_meta.get(bid, {})
        ws    = meta.get("watch_side", "net")
        emoji = meta.get("emoji", "")
        name  = meta.get("name", bid)

        if ws == "buy":
            col, col_label = "total_buy",  "累積買進（張）"
            color_fn = lambda _: "#16a34a"
        elif ws == "sell":
            col, col_label = "total_sell", "累積賣出（張）"
            color_fn = lambda _: "#dc2626"
        else:
            col, col_label = "total_net",  "累積淨部位（張）"
            color_fn = lambda v: "#16a34a" if v >= 0 else "#dc2626"

        html += (
            f'<h4 style="margin:14px 0 3px;font-size:13px;color:#374151">'
            f'{emoji} {name}（{bid}）</h4>'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px">'
            f'<thead><tr style="background:#f9fafb">'
            f'<th style="padding:4px 6px;text-align:left">個股</th>'
            f'<th style="padding:4px 6px;text-align:right">{col_label}</th>'
            f'<th style="padding:4px 6px;text-align:right;color:#9ca3af">最後異動</th>'
            f'</tr></thead><tbody>'
        )
        for p in plist:
            val = p[col]
            c   = color_fn(val)
            html += (
                f'<tr style="border-bottom:1px solid #f3f4f6">'
                f'<td style="padding:4px 6px">{p["stock_code"]} {p["stock_name"]}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:{c};font-weight:bold">{val:,}</td>'
                f'<td style="padding:4px 6px;text-align:right;color:#9ca3af">{p["last_date"]}</td>'
                f'</tr>'
            )
        html += "</tbody></table>"
    return html


# ── 分點特性頁腳 ──────────────────────────────────────────────

def _characteristics_html(broker_meta: dict) -> str:
    groups: dict[str, list] = {}
    for bid, meta in broker_meta.items():
        groups.setdefault(meta.get("group", "其他"), []).append((bid, meta))

    html = ('<hr style="margin:28px 0 12px;border:none;border-top:1px solid #e5e7eb">'
            '<p style="font-size:11px;color:#6b7280;margin:0 0 6px;font-weight:600;letter-spacing:.3px">'
            '分點特性說明</p>')

    for group, items in groups.items():
        html += (f'<p style="font-size:10px;color:#9ca3af;margin:10px 0 3px;'
                 f'font-weight:600;text-transform:uppercase;letter-spacing:.5px">'
                 f'{group}券商</p>')
        for bid, meta in items:
            badges = "".join(_badge(lb) for lb in meta.get("labels", []))
            html += (
                f'<p style="font-size:10px;color:#4b5563;margin:3px 0;line-height:1.7">'
                f'<span style="color:#111;font-weight:600">'
                f'{meta.get("emoji","")} {bid} {meta.get("name", bid)}</span> '
                f'{badges} '
                f'<span style="color:#6b7280">{meta.get("desc","")}</span>'
                f'</p>'
            )
    return html


# ── 主入口 ────────────────────────────────────────────────────

def build_html_report(
    report_date: date,
    trades: list[dict],
    positions: list[dict],
    broker_meta: dict[str, dict],
    threshold_lots: int = 0,
) -> str:
    date_str = report_date.strftime("%Y-%m-%d")

    buy_trades = sorted(
        [t for t in trades
         if broker_meta.get(t["broker_code"], {}).get("watch_side") == "buy"
         and t["buy_lots"] >= threshold_lots],
        key=lambda x: x["buy_lots"], reverse=True,
    )
    sell_trades = sorted(
        [t for t in trades
         if broker_meta.get(t["broker_code"], {}).get("watch_side") == "sell"
         and t["sell_lots"] >= threshold_lots],
        key=lambda x: x["sell_lots"], reverse=True,
    )
    net_trades = sorted(
        [t for t in trades
         if broker_meta.get(t["broker_code"], {}).get("watch_side") == "net"
         and abs(t["net_lots"]) >= threshold_lots],
        key=lambda x: abs(x["net_lots"]), reverse=True,
    )

    buy_section  = _trade_section(
        f"📈 囤貨分點 今日買進（≥ {threshold_lots} 張）",
        buy_trades, "buy_lots", "買進（張）", broker_meta)
    sell_section = _trade_section(
        f"📉 出貨分點 今日賣出（≥ {threshold_lots} 張）",
        sell_trades, "sell_lots", "賣出（張）", broker_meta)
    net_section  = _trade_section(
        f"👑 綜合贏家 今日淨買賣（≥ {threshold_lots} 張）",
        net_trades, "net_lots", "淨買賣（張）", broker_meta, dynamic_color=True)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>外資分點追蹤 {date_str}</title>
</head>
<body style="font-family:system-ui,sans-serif;max-width:780px;margin:0 auto;padding:20px;color:#111">

  <h2 style="border-bottom:2px solid #2563eb;padding-bottom:8px;margin-bottom:4px">
    外資分點追蹤日報 — {date_str}
  </h2>
  <p style="font-size:12px;color:#6b7280;margin:0 0 4px">
    門檻：單日買賣超 ≥ {threshold_lots} 張 ｜ 資料來源：TWSE / histock.tw
  </p>

  {buy_section}
  {sell_section}
  {net_section}

  <hr style="margin:28px 0 12px;border:none;border-top:1px solid #e5e7eb">
  <h3 style="margin:0 0 8px;font-size:14px">累積持倉記錄</h3>
  {_positions_html(positions, broker_meta)}

  {_characteristics_html(broker_meta)}

  <p style="margin-top:20px;font-size:10px;color:#9ca3af">
    自動產生，請勿作為投資依據
  </p>
</body>
</html>"""
