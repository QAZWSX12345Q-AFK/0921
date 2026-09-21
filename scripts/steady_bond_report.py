import os
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

FUND_CODE = "004102"
FUND_NAME = "中信保诚稳悦债券A"
REPORT_TZ = ZoneInfo("Asia/Shanghai")

TO_EMAIL = os.environ["REPORT_TO"]
FROM_EMAIL = os.environ["QQ_EMAIL"]
AUTH_CODE = os.environ["QQ_AUTH_CODE"]

FALLBACK_HOLDINGS = [
    ("25超长特别国债02", 33.78),
    ("26超长特别国债02", 20.28),
    ("25国开15", 14.84),
    ("26附息国债02", 12.10),
    ("26超长特别国债03", 4.40),
]


def get_holdings():
    """获取最新一期主要债券持仓，避免把多个报告期的数据混在一起。"""
    try:
        df = ak.fund_portfolio_bond_hold_em(symbol=FUND_CODE)
        if df is not None and not df.empty:
            name_col = next((c for c in df.columns if "债券名称" in str(c)), None)
            weight_col = next((c for c in df.columns if "占净值比例" in str(c)), None)

            if name_col and weight_col:
                date_col = next(
                    (
                        c for c in df.columns
                        if any(k in str(c) for k in ["报告期", "截止日期", "公告日期", "日期"])
                    ),
                    None,
                )
                if date_col:
                    parsed = pd.to_datetime(df[date_col], errors="coerce")
                    if parsed.notna().any():
                        latest = parsed.max()
                        df = df.loc[parsed == latest].copy()

                rows = []
                for _, row in df.iterrows():
                    try:
                        name = str(row[name_col]).strip()
                        weight = float(row[weight_col])
                        if name and pd.notna(weight):
                            rows.append((name, weight))
                    except (TypeError, ValueError):
                        continue

                if rows:
                    rows.sort(key=lambda x: x[1], reverse=True)
                    return rows[:10], "AKShare 最新报告期持仓"
    except Exception as exc:
        print("持仓查询失败:", repr(exc))

    return FALLBACK_HOLDINGS, "最近披露主要持仓（兜底）"


def get_bond_quotes():
    """优先获取银行间现券成交行情；GitHub 云端失败时交给备用代理。"""
    try:
        try:
            ak.bond_china_close_return_map()
            print("ChinaMoney 访问初始化完成")
        except Exception as exc:
            print("ChinaMoney 访问初始化失败:", repr(exc))

        df = ak.bond_spot_deal()
        if df is None or df.empty:
            print("bond_spot_deal 返回空数据")
            return {}

        name_col = next((c for c in df.columns if "债券简称" in str(c)), None)
        price_col = next((c for c in df.columns if "成交净价" in str(c)), None)
        yield_col = next((c for c in df.columns if "最新收益率" in str(c)), None)
        bp_col = next((c for c in df.columns if str(c).strip() == "涨跌"), None)

        if not name_col:
            print("银行间行情缺少债券简称字段:", list(df.columns))
            return {}

        quotes = {}
        for _, row in df.iterrows():
            try:
                name = str(row[name_col]).strip()
                if not name:
                    continue
                price = float(row[price_col]) if price_col and pd.notna(row[price_col]) else None
                yld = float(row[yield_col]) if yield_col and pd.notna(row[yield_col]) else None
                bp = float(row[bp_col]) if bp_col and pd.notna(row[bp_col]) else None
                quotes[name] = {"price": price, "yield": yld, "bp": bp}
            except (TypeError, ValueError):
                continue

        print(f"银行间行情获取成功，共 {len(quotes)} 只债券")
        return quotes
    except Exception as exc:
        print("银行间债券行情查询失败:", repr(exc))
        return {}


def get_yield_proxy():
    """
    备用行情：新浪中国国债收益率。
    当天有数据时使用今日开盘→最新；否则使用最近两个交易日收盘变化。
    """
    result = {}
    for maturity in (10, 30):
        try:
            symbol = f"中国{maturity}年期国债"
            df = ak.bond_gb_zh_sina(symbol=symbol)
            if df is None or df.empty:
                continue

            df = df.copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date", "close"]).sort_values("date")
            if df.empty:
                continue

            latest = df.iloc[-1]
            latest_date = latest["date"].date()
            today = datetime.now(REPORT_TZ).date()

            if latest_date == today and pd.notna(latest.get("open")):
                dy_bp = (float(latest["close"]) - float(latest["open"])) * 100
                basis = "今日开盘→最新"
            elif len(df) >= 2:
                prev = df.iloc[-2]
                dy_bp = (float(latest["close"]) - float(prev["close"])) * 100
                basis = "最近两个交易日收盘"
            else:
                continue

            result[maturity] = {
                "yield": float(latest["close"]),
                "dy_bp": dy_bp,
                "date": latest_date,
                "basis": basis,
            }
            print(f"新浪国债收益率代理 {maturity}Y: {latest_date}, dy={dy_bp:+.2f}BP, basis={basis}")
        except Exception as exc:
            print(f"新浪 {maturity}Y 国债收益率代理失败:", repr(exc))
    return result


def estimate_from_yield(name, weight, proxy):
    """价格变动≈-久期×收益率变动；仅作市场代理估算。"""
    is_ultra_long = "超长" in name or "30年" in name or "30Y" in name
    maturity = 30 if is_ultra_long else 10
    duration = 18.0 if maturity == 30 else 8.0
    item = proxy.get(maturity)
    if item is None:
        return None

    price_return_pct = -duration * (item["dy_bp"] / 10000.0) * 100
    contribution_pct = (weight / 100.0) * price_return_pct
    return {
        "maturity": maturity,
        "duration": duration,
        "dy_bp": item["dy_bp"],
        "yield": item["yield"],
        "basis": item["basis"],
        "price_return_pct": price_return_pct,
        "contribution_pct": contribution_pct,
    }


def build_report():
    holdings, holding_source = get_holdings()
    quotes = get_bond_quotes()
    now = datetime.now(REPORT_TZ).strftime("%Y-%m-%d %H:%M:%S")

    weighted_bp = 0.0
    weighted_estimate_pct = 0.0
    matched_count = 0
    proxy_count = 0
    rows_html = []

    proxy = {} if quotes else get_yield_proxy()

    for name, weight in holdings:
        quote = quotes.get(name)

        if quote is not None:
            bp = quote.get("bp")
            yld = quote.get("yield")
            price = quote.get("price")
            bp_text = f"{bp:+.2f} BP" if bp is not None else "—"
            yield_text = f"{yld:.4f}%" if yld is not None else "—"
            price_text = f"{price:.4f}" if price is not None else "—"

            if bp is not None:
                contribution_bp = (weight / 100.0) * bp
                weighted_bp += contribution_bp
                matched_count += 1
                contribution_text = f"{contribution_bp:+.4f} BP"
            else:
                contribution_text = "—"
            source_text = "银行间成交"
        else:
            est = estimate_from_yield(name, weight, proxy)
            if est is None:
                bp_text = "暂无"
                yield_text = "—"
                price_text = "—"
                contribution_text = "—"
                source_text = "无代理行情"
            else:
                bp_text = f"{est['dy_bp']:+.2f} BP"
                yield_text = f"{est['yield']:.4f}%"
                price_text = "代理估算"
                weighted_estimate_pct += est["contribution_pct"]
                proxy_count += 1
                contribution_text = f"{est['contribution_pct']:+.4f}%"
                source_text = f"{est['maturity']}Y国债代理（{est['basis']}，久期{est['duration']:.0f}）"

        rows_html.append(
            "<tr>"
            f"<td>{name}</td><td>{weight:.2f}%</td><td>{price_text}</td>"
            f"<td>{yield_text}</td><td>{bp_text}</td><td>{contribution_text}</td>"
            f"<td>{source_text}</td></tr>"
        )

    if quotes and matched_count:
        headline = f"银行间行情加权收益率变动 {weighted_bp:+.2f} BP"
        mode_note = "已匹配银行间现券成交行情。"
    elif proxy_count:
        headline = f"债券市场代理估算 {weighted_estimate_pct:+.3f}%"
        mode_note = (
            "银行间现券接口在 GitHub Actions 上未返回有效数据，"
            "本次切换为新浪中国国债收益率代理；该数值不是基金实际净值。"
        )
    else:
        headline = "暂无可用行情估算"
        mode_note = "银行间行情和备用国债收益率接口均未返回有效数据。"

    html = f"""
    <html><body>
      <h2>中信保诚稳悦债券｜每日监控</h2>
      <p>生成时间：{now}（北京时间）</p>
      <p>基金：{FUND_NAME}（004102）</p>
      <p>持仓来源：{holding_source}</p>
      <p>行情模式：{mode_note}</p>
      <table border="1" cellpadding="6" cellspacing="0">
        <tr><th>主要持仓</th><th>持仓权重</th><th>价格/状态</th><th>收益率</th>
        <th>收益率变动</th><th>对估算贡献</th><th>行情来源</th></tr>
        {''.join(rows_html)}
      </table>
      <h3>{headline}</h3>
      <p>银行间真实行情匹配：{matched_count}/{len(holdings)} 只</p>
      <p>备用代理估算匹配：{proxy_count}/{len(holdings)} 只</p>
      <p>
        计算说明：银行间现券接口的“涨跌”单位是 BP，表示收益率变动，不能直接当成价格涨跌幅。
        备用模式采用“价格变动≈-久期×收益率变动”的近似，仅用于下午监控时判断债券市场方向。
      </p>
      <p>
        基金实际当日净值还会受到完整持仓、应计利息、现金、费用、申赎及调仓等因素影响，
        因此“代理估算”不等同于最终基金净值收益率。
      </p>
    </body></html>
    """
    return html, weighted_estimate_pct, weighted_bp, proxy_count, matched_count


def send_email(html, estimated_pct, weighted_bp, proxy_count, matched_count):
    report_time = datetime.now(REPORT_TZ)
    if matched_count:
        subject_value = f"银行间行情 {weighted_bp:+.2f}BP"
    elif proxy_count:
        subject_value = f"代理估算 {estimated_pct:+.3f}%"
    else:
        subject_value = "暂无估算"

    subject = f"【稳悦债券监控】{report_time:%m-%d} {subject_value}"
    message = MIMEText(html, "html", "utf-8")
    message["From"] = FROM_EMAIL
    message["To"] = TO_EMAIL
    message["Subject"] = Header(subject, "utf-8")

    if not FROM_EMAIL or not AUTH_CODE or not TO_EMAIL:
        raise RuntimeError("QQ_EMAIL、QQ_AUTH_CODE、REPORT_TO 不能为空")

    last_error = None
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(FROM_EMAIL.strip(), AUTH_CODE.strip())
            server.sendmail(FROM_EMAIL.strip(), [TO_EMAIL.strip()], message.as_string())
            print("QQ SMTP 465 SSL 发送成功")
            return
    except Exception as exc:
        last_error = exc
        print("QQ SMTP 465 SSL 失败，准备尝试 587 STARTTLS:", repr(exc))

    try:
        with smtplib.SMTP("smtp.qq.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(FROM_EMAIL.strip(), AUTH_CODE.strip())
            server.sendmail(FROM_EMAIL.strip(), [TO_EMAIL.strip()], message.as_string())
            print("QQ SMTP 587 STARTTLS 发送成功")
            return
    except Exception as exc:
        last_error = exc

    raise RuntimeError(
        "QQ SMTP 465 和 587 均发送失败。请确认 QQ_EMAIL 与 QQ_AUTH_CODE 属于同一个 QQ 邮箱，"
        "且 QQ_AUTH_CODE 是该邮箱新生成的 SMTP 授权码。"
    ) from last_error


if __name__ == "__main__":
    html, estimated_pct, weighted_bp, proxy_count, matched_count = build_report()
    send_email(html, estimated_pct, weighted_bp, proxy_count, matched_count)
    print(
        f"邮件已发送：{TO_EMAIL}；代理估算：{estimated_pct:+.4f}%；"
        f"银行间加权BP：{weighted_bp:+.4f}；银行间匹配：{matched_count}；代理匹配：{proxy_count}"
    )
