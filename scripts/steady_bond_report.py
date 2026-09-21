import os
import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText

import akshare as ak
import pandas as pd

FUND_CODE = "004102"
FUND_NAME = "中信保诚稳悦债券A"

TO_EMAIL = os.environ["REPORT_TO"]
FROM_EMAIL = os.environ["QQ_EMAIL"]
AUTH_CODE = os.environ["QQ_AUTH_CODE"]

# 最近披露的主要持仓，作为行情接口异常时的兜底数据。
FALLBACK_HOLDINGS = [
    ("25超长特别国债02", 33.78),
    ("26超长特别国债02", 20.28),
    ("25国开15", 14.84),
    ("26附息国债02", 12.10),
    ("26超长特别国债03", 4.40),
]


def get_holdings():
    try:
        df = ak.fund_portfolio_bond_hold_em(symbol=FUND_CODE)
        if df is not None and not df.empty:
            name_col = next((c for c in df.columns if "债券名称" in str(c)), None)
            weight_col = next((c for c in df.columns if "占净值比例" in str(c)), None)

            if name_col and weight_col:
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
                    return rows[:10], "AKShare 实时查询"
    except Exception as exc:
        print("持仓查询失败:", repr(exc))

    return FALLBACK_HOLDINGS, "最近披露持仓（兜底）"


def get_bond_quotes():
    """
    获取银行间债券现券成交行情。

    GitHub Actions 与本地电脑的区别在于出口 IP 不同。
    ChinaMoney 对部分访问会要求先完成访问初始化，因此先调用
    bond_china_close_return_map()，再调用 bond_spot_deal()。
    """
    try:
        # 先初始化中国外汇交易中心/ChinaMoney 的访问状态
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

def build_report():
    holdings, holding_source = get_holdings()
    quotes = get_bond_quotes()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 这里不把“涨跌(BP)”错误地当成“价格涨跌幅%”。
    # 银行间现券接口给出的涨跌是收益率变动(BP)，不是价格百分比。
    # 因此本版先准确展示银行间行情和按持仓权重计算的加权收益率变动。
    weighted_bp = 0.0
    matched_count = 0
    rows_html = []

    for name, weight in holdings:
        quote = quotes.get(name)

        if quote is None:
            bp_text = "暂无行情"
            yield_text = "—"
            price_text = "—"
            contribution_text = "—"
        else:
            bp = quote.get("bp")
            yld = quote.get("yield")
            price = quote.get("price")

            bp_text = f"{bp:+.2f} BP" if bp is not None else "—"
            yield_text = f"{yld:.4f}%" if yld is not None else "—"
            price_text = f"{price:.4f}" if price is not None else "—"

            if bp is not None:
                contribution = (weight / 100.0) * bp
                weighted_bp += contribution
                matched_count += 1
                contribution_text = f"{contribution:+.4f} BP"
            else:
                contribution_text = "—"

        rows_html.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{weight:.2f}%</td>"
            f"<td>{price_text}</td>"
            f"<td>{yield_text}</td>"
            f"<td>{bp_text}</td>"
            f"<td>{contribution_text}</td>"
            "</tr>"
        )

    html = f"""
    <html>
      <body>
        <h2>中信保诚稳悦债券｜每日监控</h2>
        <p>生成时间：{now}</p>
        <p>基金：{FUND_NAME}（004102）</p>
        <p>持仓来源：{holding_source}</p>
        <p>银行间行情来源：AKShare bond_spot_deal（中国外汇交易中心/全国银行间同业拆借中心）</p>

        <table border="1" cellpadding="6" cellspacing="0">
          <tr>
            <th>主要持仓</th>
            <th>持仓权重</th>
            <th>成交净价</th>
            <th>最新收益率</th>
            <th>收益率涨跌</th>
            <th>加权贡献</th>
          </tr>
          {''.join(rows_html)}
        </table>

        <h3>主要持仓加权收益率变动：{weighted_bp:+.4f} BP</h3>
        <p>已匹配行情：{matched_count}/{len(holdings)} 只债券</p>

        <p>
          说明：银行间债券行情接口的“涨跌”单位是 BP（基点），
          表示收益率变动，并不是债券价格涨跌幅。因此本程序不会
          把 BP 直接当成百分比计算基金收益，以免产生错误结果。
        </p>
        <p>
          这版先确保持仓债券能够正确匹配到银行间实时成交行情。
          基金实际当日净值收益率仍需考虑完整持仓、应计利息、现金、
          费用以及申赎等因素。
        </p>
      </body>
    </html>
    """

    return html, weighted_bp

def send_email(html, estimated):
    subject = (
        f"【稳悦债券监控】{datetime.now():%m-%d} "
        f"主要持仓估算 {estimated:+.2f}%"
    )

    message = MIMEText(html, "html", "utf-8")
    message["From"] = FROM_EMAIL
    message["To"] = TO_EMAIL
    message["Subject"] = Header(subject, "utf-8")

    # QQ 邮箱支持 465 SSL 和 587 STARTTLS。
    # GitHub Actions 云端环境下，如果某个端口被临时断开，自动尝试另一个。
    if not FROM_EMAIL or not AUTH_CODE or not TO_EMAIL:
        raise RuntimeError("QQ_EMAIL、QQ_AUTH_CODE、REPORT_TO 不能为空")

    last_error = None

    # 方案一：465 + SSL
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(FROM_EMAIL.strip(), AUTH_CODE.strip())
            server.sendmail(
                FROM_EMAIL.strip(),
                [TO_EMAIL.strip()],
                message.as_string(),
            )
            print("QQ SMTP 465 SSL 发送成功")
            return
    except Exception as exc:
        last_error = exc
        print("QQ SMTP 465 SSL 失败，准备尝试 587 STARTTLS:", repr(exc))

    # 方案二：587 + STARTTLS
    try:
        with smtplib.SMTP("smtp.qq.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(FROM_EMAIL.strip(), AUTH_CODE.strip())
            server.sendmail(
                FROM_EMAIL.strip(),
                [TO_EMAIL.strip()],
                message.as_string(),
            )
            print("QQ SMTP 587 STARTTLS 发送成功")
            return
    except Exception as exc:
        last_error = exc

    raise RuntimeError(
        "QQ SMTP 465 和 587 均发送失败。"
        "请确认 QQ_EMAIL 与 QQ_AUTH_CODE 属于同一个 QQ 邮箱，"
        "且 QQ_AUTH_CODE 是该邮箱新生成的 SMTP 授权码。"
    ) from last_error


if __name__ == "__main__":
    html, estimated = build_report()
    send_email(html, estimated)
    print(f"邮件已发送：{TO_EMAIL}，估算值：{estimated:+.4f}%")
