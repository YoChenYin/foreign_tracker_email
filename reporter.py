"""產生每日 HTML 報告（囤貨 / 出貨 / 贏家 三區塊 + 分點特性頁腳）"""
from datetime import date


# ── 小工具 ────────────────────────────────────────────────────

def _fmt_value(ntd: int) -> str:
    """NT$ 金額格式化：≥1億 → X.X億，否則 X,XXX萬"""
    wan = ntd // 10_000
    if wan >= 10_000:
        return f"{wan / 10_000:.1f}億"
    return f"{wan:,}萬"

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

def _trade_section(title: str, trades: list[dict], metric_col: str, value_col: str,
                   broker_meta: dict, dynamic_color: bool = False) -> str:
    if not trades:
        empty = ('<p style="color:#9ca3af;font-size:12px;margin:4px 0 16px">'
                 '今日無達門檻記錄</p>')
        return f'<h3 style="margin:22px 0 4px;font-size:14px">{title}</h3>{empty}'

    # 按股票分組，以「該股 value 合計絕對值」排序
    by_stock: dict[str, list[dict]] = {}
    for t in trades:
        by_stock.setdefault(t["stock_code"], []).append(t)

    stock_order = sorted(
        by_stock.keys(),
        key=lambda c: abs(sum(r[value_col] for r in by_stock[c])),
        reverse=True,
    )

    if metric_col == "buy_lots":
        hdr_bg, action_word = "#f0fdf4", "買進共"
    elif metric_col == "sell_lots":
        hdr_bg, action_word = "#fef2f2", "賣出共"
    else:
        hdr_bg, action_word = "#fefce8", "淨買賣共"

    cards = ""
    for code in stock_order:
        rows  = by_stock[code]
        name  = rows[0]["stock_name"]
        total_val = sum(r[value_col] for r in rows)
        tv_str    = (_fmt_value(abs(total_val))
                     if not dynamic_color
                     else (f"+{_fmt_value(total_val)}" if total_val >= 0
                           else f"-{_fmt_value(abs(total_val))}"))

        cards += (
            f'<table style="width:100%;margin-bottom:10px;border:1px solid #e5e7eb;'
            f'border-collapse:collapse;font-size:13px">'
            f'<tr style="background:{hdr_bg}">'
            f'<td style="padding:7px 10px;font-weight:700">{code} {name}</td>'
            f'<td style="padding:7px 10px;text-align:right;color:#6b7280;font-size:11px">'
            f'{len(rows)} 家分點 ｜ {action_word} {tv_str}</td>'
            f'</tr>'
        )

        for r in sorted(rows, key=lambda x: abs(x[value_col]), reverse=True):
            bid   = r["broker_code"]
            meta  = broker_meta.get(bid, {})
            emoji = meta.get("emoji", "")
            bname = meta.get("name", bid)
            buy, sell, net = r["buy_lots"], r["sell_lots"], r["net_lots"]
            bv = r.get("buy_value", 0)
            sv = r.get("sell_value", 0)
            nv = r.get("net_value", 0)

            if metric_col == "buy_lots":
                main = f"買 {buy:,} 張 / {_fmt_value(bv)}"
                sub  = f"賣 {sell:,}"
                color = "#16a34a"
            elif metric_col == "sell_lots":
                main = f"賣 {sell:,} 張 / {_fmt_value(sv)}"
                sub  = f"買 {buy:,}"
                color = "#dc2626"
            else:
                sign  = "+" if net >= 0 else ""
                nv_s  = f"+{_fmt_value(nv)}" if nv >= 0 else f"-{_fmt_value(abs(nv))}"
                main  = f"淨 {sign}{net:,} 張 / {nv_s}"
                sub   = f"買 {buy:,}  賣 {sell:,}"
                color = "#16a34a" if net >= 0 else "#dc2626"

            cards += (
                f'<tr style="border-top:1px solid #f3f4f6">'
                f'<td style="padding:5px 10px 5px 18px;color:#374151;white-space:nowrap">'
                f'{emoji} {bname}（{bid}）</td>'
                f'<td style="padding:5px 10px;text-align:right">'
                f'<span style="color:{color};font-weight:bold">{main}</span>'
                f'<span style="color:#9ca3af;font-size:11px;margin-left:8px">{sub}</span>'
                f'</td></tr>'
            )
        cards += "</table>"

    return f'<h3 style="margin:22px 0 5px;font-size:14px">{title}</h3>{cards}'


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
    threshold_wan: int = 0,
) -> str:
    date_str    = report_date.strftime("%Y-%m-%d")
    threshold   = threshold_wan * 10_000  # 萬元 → NT$

    buy_trades = [t for t in trades
                  if broker_meta.get(t["broker_code"], {}).get("watch_side") == "buy"
                  and t.get("buy_value", 0) >= threshold]
    sell_trades = [t for t in trades
                   if broker_meta.get(t["broker_code"], {}).get("watch_side") == "sell"
                   and t.get("sell_value", 0) >= threshold]
    net_trades = [t for t in trades
                  if broker_meta.get(t["broker_code"], {}).get("watch_side") == "net"
                  and abs(t.get("net_value", 0)) >= threshold]

    thr_str = _fmt_value(threshold)

    buy_section  = _trade_section(
        f"📈 囤貨分點 今日買進（≥ {thr_str}）",
        buy_trades, "buy_lots", "buy_value", broker_meta)
    sell_section = _trade_section(
        f"📉 出貨分點 今日賣出（≥ {thr_str}）",
        sell_trades, "sell_lots", "sell_value", broker_meta)
    net_section  = _trade_section(
        f"👑 綜合贏家 今日淨買賣（≥ {thr_str}）",
        net_trades, "net_lots", "net_value", broker_meta, dynamic_color=True)

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
    門檻：單日買賣超 ≥ {thr_str} ｜ 資料來源：TWSE / histock.tw
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
