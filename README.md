# 0921｜中信保诚稳悦债券每日监控

这个项目使用 GitHub Actions 在工作日 14:30（Asia/Shanghai）自动运行，计算主要持仓的加权涨跌估算，并通过 QQ 邮箱 SMTP 发送邮件。

## 需要配置的 GitHub Secrets

进入：

**Settings → Secrets and variables → Actions → New repository secret**

添加：

- `QQ_EMAIL`：发送邮件的 QQ 邮箱，例如 `2686718691@qq.com`
- `QQ_AUTH_CODE`：QQ 邮箱 SMTP 授权码（不是 QQ 登录密码）
- `REPORT_TO`：接收报告的邮箱

授权码不要写进代码，也不要提交到仓库。

## 运行时间

工作日 14:30，时区为 `Asia/Shanghai`。

GitHub 官方文档说明，scheduled workflow 支持 IANA 时区；但高负载时可能出现延迟，所以 14:30 不是绝对精确到秒。 

## 手动测试

进入仓库的 **Actions → 稳悦债券每日监控 → Run workflow**，可以手动运行一次。

## 重要说明

本程序计算的是主要持仓加权估算，不等同于基金公司公布的当日净值收益率。债券基金完整持仓、应计利息、现金、费用等不会全部按日公开，因此报告会明确标记估算值。
