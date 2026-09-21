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
    try:
        df = ak.bond_zh_hs_spot()
        if df is None or df.empty:
            return {}

        name_col = next(
            (c for c in df.columns if str(c) in ("名称", "债券名称")),
            None,
        )
        pct_col = next((c for c in df.columns if "涨跌幅" in str(c)), None)

        if not name_col or not pct_col:
            return {}

        quotes = {}
        for _, row in df.iterrows():
            try:
                name = str(row[name_col]).strip()
                pct = float(str(row[pct_col]).replace("%", "").strip())
                quotes[name] = pct
            except (TypeError, ValueError):
                continue

        return quotes
    except Exception as exc:
        print("债券行情查询失败:", repr(exc))
        return {}


def build_report():
    holdings, holding_source = get_holdings()
    quotes = get_bond_quotes()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    estimated = 0.0
    rows_html = []

    for name, weight in holdings:
        pct = quotes.get(name)

        if pct is None:
            change_text = "暂无可靠行情"
            contribution_text = "—"
        else:
            contribution = (weight / 100.0) * pct
            estimated += contribution
            change_text = f"{pct:+.4f}%"
            contribution_text = f"{contribution:+.4f}%"

        rows_html.append(
            "<tr>"
            f"<td>{name}</td>"
            f"<td>{weight:.2f}%</td>"
            f"<td>{change_text}</td>"
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

        <table border="1" cellpadding="6" cellspacing="0">
          <tr>
            <th>主要持仓</th>
            <th>持仓权重</th>
            <th>债券当日涨跌</th>
            <th>估算贡献</th>
          </tr>
          {''.join(rows_html)}
        </table>

        <h3>主要持仓加权估算：{estimated:+.4f}%</h3>

        <p>
          说明：这是根据已披露主要持仓及债券行情计算的方向性估算，
          不是基金公司公布的当日净值收益率。基金实际收益还会受到
          未列示资产、应计利息、现金、费用、申赎等因素影响。
        </p>
        <p>
          如果行情接口没有可靠价格，本程序不会用猜测数据补齐。
        </p>
      </body>
    </html>
    """

    return html, estimated


def send_email(html, estimated):
    subject = (
        f"【稳悦债券监控】{datetime.now():%m-%d} "
        f"主要持仓估算 {estimated:+.2f}%"
    )

    message = MIMEText(html, "html", "utf-8")
    message["From"] = FROM_EMAIL
    message["To"] = TO_EMAIL
    message["Subject"] = Header(subject, "utf-8")

    with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
        server.login(FROM_EMAIL, AUTH_CODE)
        server.sendmail(FROM_EMAIL, [TO_EMAIL], message.as_string())


if __name__ == "__main__":
    html, estimated = build_report()
    send_email(html, estimated)
    print(f"邮件已发送：{TO_EMAIL}，估算值：{estimated:+.4f}%")
