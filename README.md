# Binance Spot Live Agent

当前版本：`v0.2.4`

一个默认连接币安现货实盘、但交易开关默认关闭的小本金网格交易 agent。当前服务器版本应保持：

```bash
EXECUTE_TRADES=false
```

它现在做几件事：

- 连接币安现货实盘拉行情、账户、K 线。
- 用保守网格/均值回归策略生成买卖信号。
- 默认示例配置是 `dry-run`；当前服务器 `.env` 已经设置 `EXECUTE_TRADES=false`，不会自动下单。
- 提供 Web 看板，默认端口 `8088`，用于查看实时价格线、信号、实盘余额、最近订单和相对启动基准的盈亏。

## 风险声明

本项目仅供学习、研究和技术交流使用，不构成任何投资建议、收益承诺或交易指导。加密资产价格波动剧烈，自动交易可能因为行情、网络、交易所接口、参数设置、代码缺陷等原因造成亏损。任何人使用、修改、部署或参考本项目进行交易，均应自行理解策略逻辑并独立承担全部风险，盈亏自负，责任自负。

强烈建议先在极小资金、交易开关关闭或模拟环境下验证完整流程。API key 只应开启读取和现货交易权限，绝不要开启提现权限。

## 准备

```bash
cp .env.example .env
```

在 Binance 创建现货 API key，只开启读取和现货交易权限，不开启提现权限，然后填入 `.env`。

## 常用命令

```bash
# 检查币安公开接口
python3 -m binance_testnet_agent.cli health

# 拉最新行情并生成一次策略信号，不下单
python3 -m binance_testnet_agent.cli once

# 查看现货账户，需要 key
python3 -m binance_testnet_agent.cli account

# 循环运行 agent
python3 -m binance_testnet_agent.cli run

# 启动本地看板
python3 -m binance_testnet_agent.dashboard --host 0.0.0.0 --port 8088
```

如果看到 `HTTP 451 restricted location`，说明当前机器的出口 IP 被 Binance 限制。换到你实际准备部署的非受限地区云服务器再运行同样命令即可。

## 开启实盘下单

确认 `.env` 里：

```bash
EXECUTE_TRADES=false
```

然后运行：

```bash
python3 -m binance_testnet_agent.cli once
```

第一版只会使用 `MARKET` 买卖，并受这些风控限制：

- `ORDER_QUOTE_SIZE`：每笔买入使用多少 USDT。
- `MAX_POSITION_QUOTE`：最多持仓价值。
- `MAX_DAILY_LOSS_QUOTE`：预留的每日亏损阈值，后续会接入成交记录后严格执行。
- `EXECUTE_TRADES`：`false` 完全不下单，`true` 会在 Binance Spot 实盘下单。

## 服务器运行

当前云服务器使用 systemd，服务名沿用旧名称但内容已经是实盘：

```bash
sudo systemctl status binance-testnet-agent
sudo systemctl status binance-testnet-dashboard
```

看交易 agent 最近日志：

```bash
journalctl -u binance-testnet-agent -n 120 --no-pager
```

看板地址：

```text
http://161.33.129.33:8088
```

Web 页面登录密码由 `.env` 中的 `DASHBOARD_PASSWORD` 控制。打开看板后会先显示页面登录框，不再使用浏览器的 HTTP Basic 弹窗。

交易开关有二次密码，点击开启/暂停交易时需要输入 `TRADING_TOGGLE_PASSWORD`。

如果访问不了，请先在云厂商安全组/防火墙里放行 TCP `8088`。

## 给服务器上的 AI

当用户问“情况怎么样”“赚了多少”“最近表现如何”时：

1. 先查看服务是否运行：

```bash
systemctl --no-pager --full status binance-testnet-agent
systemctl --no-pager --full status binance-testnet-dashboard
```

2. 再翻最近日志：

```bash
journalctl -u binance-testnet-agent -n 200 --no-pager
```

3. 从最新 JSON 日志里读取这些字段并用中文汇报：

- `price`：当前 BTCUSDT 价格。
- `signal` / `reason`：当前策略信号和原因。
- `value_quote`：当前 BTC + USDT 折算总价值。
- `baseline_value_quote`：启动时基准价值。
- `pnl_quote`：近期盈亏，单位 USDT。
- `pnl_pct`：近期盈亏百分比。
- `execute_trades`：是否正在实盘自动下单。

4. 也可以直接请求看板接口：

```bash
curl -s http://127.0.0.1:8088/api/status
```

如果要在服务器本机请求看板接口，需要带页面密码请求头：

```bash
curl -H "X-Dashboard-Password: <页面密码>" -s http://127.0.0.1:8088/api/status
```

汇报时要明确这是 Binance Spot 实盘数据，且交易开关默认关闭，只有用户手动开启才下单。

## 配置说明和建议值

配置都写在 `.env`。分享版只提供 `.env.example`，复制成 `.env` 后再填自己的 key 和密码。

### 基础与安全

| 配置 | 作用 | 50U 小本金建议 |
| --- | --- | --- |
| `BINANCE_API_KEY` | Binance 现货 API Key，只开读取和现货交易 | 必填，不要外发 |
| `BINANCE_API_SECRET` | Binance API Secret | 必填，不要外发 |
| `BINANCE_BASE_URL` | Binance 接口地址；实盘是 `https://api.binance.com` | `https://api.binance.com` |
| `BINANCE_SYMBOL` | 交易对 | `BTCUSDT` |
| `BINANCE_BASE_ASSET` | 基础资产 | `BTC` |
| `BINANCE_QUOTE_ASSET` | 计价资产 | `USDT` |
| `EXECUTE_TRADES` | 自动策略是否允许真实下单；也可在 Web 面板开关 | `false` 起步，确认后手动开 |
| `LOOP_SECONDS` | 自动策略循环间隔，秒 | `30` |
| `DASHBOARD_PASSWORD` | Web 看板页面登录密码 | 自定义强密码 |
| `TRADING_TOGGLE_PASSWORD` | 开关交易、手动交易、保存设置的二次密码 | 自定义强密码 |
| `TRADING_FEE_RATE` | 单边手续费估算，普通现货约 0.1% | `0.001` |

### 网格策略

| 配置 | 作用 | 50U 小本金建议 |
| --- | --- | --- |
| `ORDER_QUOTE_SIZE` | 固定每笔买入金额；开启自动分档后作为基础值 | `5.5` |
| `AUTO_POSITION_SIZING` | 按账户总资产自动调整每笔金额和最大持仓 | `true` |
| `GRID_STEP_PCT` | 价格低于参考价多少触发基础买入档 | `0.006` |
| `TAKE_PROFIT_PCT` | 每个批次目标利润，系统会额外覆盖买卖双边手续费 | `0.006` |
| `MAX_POSITION_QUOTE` | 最大持仓价值，超过后不再继续自动买 | `47` |
| `MAX_DAILY_LOSS_QUOTE` | 日亏损保护预留值 | `50` |

### 风控与防守

| 配置 | 作用 | 50U 小本金建议 |
| --- | --- | --- |
| `MAX_FLOATING_LOSS_QUOTE` | 未平批次浮亏超过该值时暂停普通新买入 | `5` |
| `RAPID_DROP_PAUSE_PCT` | 短时间急跌阈值，触发后暂停追买 | `0.008` |
| `LARGE_DROP_PAUSE_PCT` | 大幅下跌阈值，配合反弹检测使用 | `0.02` |
| `REBOUND_BUY_PCT` | 急跌后反弹多少才允许谨慎补一手 | `0.0015` |
| `PRICE_ANOMALY_PCT` | 当前价和近期价偏离过大时视为价格源异常 | `0.02` |
| `DEFENSIVE_MODE` | 防守模式总开关 | `true` |
| `DEFENSIVE_POSITION_USAGE_TRIGGER` | 持仓占最大持仓多少后进入防守 | `0.80` |
| `DEFENSIVE_FLOATING_LOSS_QUOTE` | 浮亏达到多少后进入防守 | `2.5` |
| `DEFENSIVE_RECENT_DRAWDOWN_PCT` | 近 60 分钟回撤达到多少后进入防守 | `0.025` |
| `DEFENSIVE_NORMAL_ADD_ON_STEP_PCT` | 正常补仓间距 | `0.0025` |
| `DEFENSIVE_ADD_ON_STEP_PCT` | 防守时补仓间距，会买得更慢 | `0.005` |
| `DEFENSIVE_AGED_LOT_DAYS_1` | 老仓第一档降目标利润天数 | `7` |
| `DEFENSIVE_AGED_LOT_PROFIT_PCT_1` | 老仓第一档目标利润 | `0.0035` |
| `DEFENSIVE_AGED_LOT_DAYS_2` | 老仓第二档降目标利润天数 | `14` |
| `DEFENSIVE_AGED_LOT_PROFIT_PCT_2` | 老仓第二档目标利润 | `0.0015` |

### 波段抄底

| 配置 | 作用 | 50U 小本金建议 |
| --- | --- | --- |
| `SWING_STRATEGY` | 独立波段抄底策略开关 | `true` |
| `SWING_ALLOCATION_PCT` | 波段仓最多使用总资产比例 | `0.30` |
| `SWING_MIN_ORDER_QUOTE` | 波段最小下单额，低于币安最小额不会下 | `10` |
| `SWING_MAX_ORDER_QUOTE` | 波段单笔最大金额，避免一下买太多 | `15` |
| `SWING_ADD_STEP_PCT` | 已有波段仓后，再跌多少才补下一笔 | `0.015` |
| `SWING_MIN_BAND_PCT` | 波段买卖线最小带宽 | `0.012` |
| `SWING_MAX_BAND_PCT` | 波段买卖线最大带宽 | `0.025` |
| `SWING_MANUAL_CENTER_PRICE` | 手动固定波段中枢；`0` 表示自动计算 | `0` |
| `SWING_KLINE_INTERVAL` | 计算波段中枢用的 K 线周期 | `1h` |
| `SWING_KLINE_LIMIT` | 计算波段中枢用多少根 K 线 | `24` |

### 人工交易

| 配置 | 作用 | 建议 |
| --- | --- | --- |
| `MANUAL_BUY_AUTO_SELL` | 人工买入并记账后，默认是否交给脚本到目标价自动卖出；每次人工买入时也可以临时选择 | `false`，需要脚本接管时再选 `yes` |

人工买入分两种：

- `市价买入`：立即成交，立即写入账本。
- `限价买入`：先挂单，只有订单成交后才写入账本。

人工卖出分三种：

- `市价卖出`：对人工/波段批次开放，成交后关闭账本批次。
- `限价卖出`：给任意未平批次挂限价卖单，未成交前批次会显示“限价卖出中”和手动价格，成交后已平批次显示“限价卖出成交”。
- `外部已卖`：你已经在 Binance App 或网页卖掉时，用它同步账本；它不会向 Binance 再下单，已平批次会显示“外部已卖”。

人工买入和波段批次在未卖出前，可以在未平批次表中随时切换“开启自动卖”或“取消自动卖”。这个操作只改账本开关，不会立即下单；开启后，价格达到该批次目标卖价时才会由脚本自动卖出。

Web 看板接口统一使用 GET。为了避免密码进入 URL，页面密码、交易密码和操作参数通过请求头传递。

### 邮件报告

| 配置 | 作用 | 建议 |
| --- | --- | --- |
| `SMTP_HOST` | SMTP 服务器 | QQ 邮箱用 `smtp.qq.com`，企业邮箱按服务商填写 |
| `SMTP_PORT` | SMTP SSL 端口 | `465` |
| `SMTP_USERNAME` | 发件邮箱账号 | 自己的邮箱 |
| `SMTP_PASSWORD` | 邮箱 SMTP 授权码，不是登录密码 | 不要外发 |
| `SMTP_FROM_NAME` | 邮件发件人名称 | `交易报告` |
| `REPORT_RECIPIENT` | 报告收件人 | 自己的收件邮箱 |

## 当前策略

当前实盘使用“批次账本 + 多层网格”策略，会按账户资金自动调整新买入批次大小，并按 `0.1%` 现货 taker 手续费估算目标卖价。

- 参考价：最近 20 根 1 分钟 K 线收盘价均值。
- `AUTO_POSITION_SIZING=true` 时，新买入批次会按账户总估值自动分档。
- 当前约 `50 USDT` 档位：每份约 `5.5 USDT`，最大持仓约账户估值的 `92%`。
- `80~200 USDT` 档位：每份约账户估值的 `7%`，最大持仓约 `72%`。
- `200~500 USDT` 档位：每份约账户估值的 `5.5%`，最大持仓约 `65%`。
- `500 USDT+` 档位：每份约账户估值的 `4%`，最大持仓约 `60%`。
- 买入 1 档：价格低于参考价 `0.25%`，买 `1x`。
- 买入 2 档：低于 `0.50%`，买 `1.2x`。
- 买入 3 档：低于 `0.85%`，买 `1.6x`。
- 买入 4 档：低于 `1.30%`，买 `2.2x`。
- 滚动补货：当 bot 没有未平批次，且价格没有明显偏离参考价时，会买入一个 `starter` 批次，让上涨后的新价格中枢也能继续参与网格。
- 下跌续补仓：如果已有未平批次但价格继续向下，每比最低未平买入价再低约 `0.25%`，允许继续补一批；越深的档位买入倍数越大，但不超过 `MAX_POSITION_QUOTE`。
- 防守模式默认开启，但只在危险时介入：持仓价值达到最大持仓约 `80%`、未平批次浮亏超过约 `2.5 USDT`、或近 60 分钟从高点回撤超过约 `2.5%` 时，会把继续补仓间距从 `0.25%` 拉大到 `0.5%`，避免下跌中买得太密。
- 老仓目标价会动态防守：持仓超过 `7` 天时目标利润从 `0.6%` 降到约 `0.35%`，超过 `14` 天降到约 `0.15%`，但不会低于覆盖买入和卖出手续费后的保本价。账本中的原目标价不被改写，Web 看板会显示实际生效的预计卖价。

策略会记录已使用的买入/卖出档位，避免价格在同一档反复触发连续交易。当价格回到参考价另一侧时，对应档位记忆会重置。

卖出不是按参考价随便卖，而是按买入批次卖：

- 每次买入都会记录一个批次，包括买入价、数量、目标卖价。
- 只有当前价格高于该批次目标卖价时，才会卖出该批次。
- 目标卖价由 `TAKE_PROFIT_PCT=0.006` 和 `TRADING_FEE_RATE=0.001` 控制；目标价会覆盖买入费、卖出费，再争取约 `0.6%` 净空间。
- 防守模式下，老仓可能显示更低的“预计卖价”，这是动态保本附近释放资金逻辑，不是亏卖逻辑。
- 当前按实盘普通现货费率设置 `TRADING_FEE_RATE=0.001`，目标卖价会自动覆盖买入费和卖出费；实盘按这套口径覆盖买入和卖出手续费。
- Web 面板会统计未平批次、已平批次和合计手续费；未平/已平批次表也会显示每批手续费，已平批次利润按扣费后净利润展示。
- 这样可以避免把某个低价批次和高价批次混在一起亏卖。
- 风控会在价格源异常、短时间急跌、近 60 分钟大幅回撤、未平批次浮亏超过阈值时暂停新买入；如果浮亏或大幅回撤后检测到短线反弹，或者跌完后已经横住，会恢复按网格策略买入。

## 双策略：网格 + 大波段仓

除日常网格仓外，实盘还可以开启一个独立的大波段仓：

- `SWING_STRATEGY=true` 开启。
- 默认使用账户总估值的 `30%` 作为波段仓资金池，网格仓继续独立运行。
- 波段资金池不是下一笔要买入的金额，只是允许波段仓占用的上限；真实单笔金额由 `SWING_MIN_ORDER_QUOTE` 和 `SWING_MAX_ORDER_QUOTE` 控制。例如资金池 `120U`、单笔上限 `15U` 时，触发后也只会分批买入约 `15U`。
- 波段中枢默认按最近 `24` 根 `1h` K 线收盘价均值计算，更贴近当前价格阶段。
- 波段带宽按近期波动自动计算，并限制在 `1.2%~2.5%` 之间。
- 买入线 = 中枢价 * `(1 - 带宽)`。
- 卖出线 = 中枢价 * `(1 + 带宽)`。
- 如果想固定中枢，例如围绕 `73000` 做 `72000/74000` 附近的波段，可以设置 `SWING_MANUAL_CENTER_PRICE=73000`。

例如近期中枢约 `73905`、最小带宽 `1.2%` 时，波段买入线约 `73018`，卖出线约 `74792`。这比固定 `72000/74000` 更适合价格中枢缓慢变化的情况。

波段批次在未平批次表中可以手工接管：

- `取消自动卖`：脚本不再按波段目标价自动卖出该批次。
- `开启自动卖`：恢复按该批次波段目标价自动卖出。
- `市价卖出`：按当前市价卖出并关闭该批次账本记录。

订单记录保存在服务器：

```bash
/opt/binance-testnet-agent/data/trades_BTCUSDT.jsonl
```

批次账本保存在服务器：

```bash
/opt/binance-testnet-agent/data/lots_BTCUSDT.json
```

备份保存在服务器：

```bash
/opt/binance-testnet-agent/backups/
```

系统每 30 分钟备份一次 README、脱敏环境配置、data 账本和最近 systemd 日志，保留最近 14 天。

## 本地快速回测

不连接 Binance API 时，可以用本地合成的一周分钟级行情快速验证策略：

```bash
python3 -m binance_testnet_agent.local_backtest
```

回测默认使用：

- 初始资产：`50 USDT`
- 初始 BTC 价格：`73000`
- 周期：`7` 天，每分钟一个价格点
- 手续费：买入 `0.1%`，卖出 `0.1%`
- 风控：包含短时间急跌、近 60 分钟大幅回撤、浮亏阈值和反弹补仓判断
- 策略参数：当前 50U 小本金网格参数

它会跑窄幅震荡、宽幅震荡、缓慢上涨、单边下跌、先跌后反弹几种场景，并输出总资产、收益率、手续费、买卖次数、未平批次和最大回撤。

如果要用 Binance 历史 K 线回测指定月份：

```bash
python3 -m binance_testnet_agent.historical_backtest \
  --start 2026-04-01 \
  --end 2026-05-01 \
  --initial-quote 273.95 \
  --take-profits 0.0045,0.006,0.008,0.010,0.012
```

这个命令默认使用 `1m` K 线，并用当前 `.env` 中的网格、风控、手续费配置，只改变止盈比例做对比。

Web 看板也内置了“策略回测”：

- `模拟盘`：用当前账户资金和当前策略参数跑多种合成场景。
- `真实 K 线`：输入开始和结束日期，用 Binance 历史 `1m` K 线回测。
- `盈利比例`：输入多个比例，例如 `0.6,0.8,1.0,1.2`，面板会输出收益率、手续费、买卖次数、未平批次、最大回撤，并给出建议比例。

## 邮件交易报告

服务器会发送实盘交易报告到 `REPORT_RECIPIENT`：

- 日报：每天北京时间 `18:00`
- 周报：每周日北京时间 `18:00`
- 月报：每月最后一天北京时间 `18:00`

systemd 使用 UTC 时间，所以北京时间 `18:00` 对应 `10:00 UTC`。

```bash
systemctl status binance-testnet-report.timer
systemctl status binance-testnet-weekly-report.timer
systemctl status binance-testnet-monthly-report.timer
systemctl start binance-testnet-report.service
```

50U 小本金实盘建议：

```bash
ORDER_QUOTE_SIZE=5.5
AUTO_POSITION_SIZING=true
MAX_POSITION_QUOTE=47
TAKE_PROFIT_PCT=0.006
TRADING_FEE_RATE=0.001
MAX_FLOATING_LOSS_QUOTE=5
RAPID_DROP_PAUSE_PCT=0.008
DEFENSIVE_MODE=true
DEFENSIVE_POSITION_USAGE_TRIGGER=0.80
DEFENSIVE_FLOATING_LOSS_QUOTE=2.5
DEFENSIVE_RECENT_DRAWDOWN_PCT=0.025
DEFENSIVE_ADD_ON_STEP_PCT=0.005
DEFENSIVE_AGED_LOT_DAYS_1=7
DEFENSIVE_AGED_LOT_PROFIT_PCT_1=0.0035
DEFENSIVE_AGED_LOT_DAYS_2=14
DEFENSIVE_AGED_LOT_PROFIT_PCT_2=0.0015
```

## 云服务器建议

实盘 API key 只开启现货交易和读取权限，不开启提现权限；交易开关默认关闭，确认状态后再手动开启。

## 贡献与感谢

感谢 [R0A1NG](https://github.com/R0A1NG) 提供了大量策略、交互和使用体验建议，帮助这个项目在短时间内快速成长。

## Web 修改盈利比例

在 Web 看板的“设置”里可以修改默认盈利比例：

- 默认只影响后续新买入批次。
- 如果选择“重算未平批次”，系统会按新比例重新计算未平普通/人工批次的预计卖价。
- 波段仓会跳过，因为波段仓有独立目标价。
- 已经挂了限价卖出的批次会跳过，因为 Binance 上已有挂单价格。
- 修改会写入 `.env`，自动交易进程每轮都会重新读取配置，不需要手动重启服务。

## 更新日志

### v0.2.4 - 2026-06-04

- 修复策略回测遇到 Nginx/上游 HTML 错误页时，前端显示 `Unexpected token '<'` 的问题；现在会先判断返回类型并给出可读错误。
- 策略回测不再要求输入交易开关密码，登录看板后即可运行；回测不会下单。
- 波段策略展示从“预算”改为“资金池 / 已占用 / 单笔范围”，避免误解成下一笔会一次性买完整个资金池。
- 波段策略接口增加单笔最小/最大金额字段，Web 面板可直接展示当前波段分批规则。

### v0.2.3 - 2026-06-03

- 手机端表格体验优化：最近订单、未平批次、限价挂单、已平批次、回测结果在小屏下自动变成卡片式布局，不再依赖横向拖动才能看完整。
- 手机端操作按钮改为卡片内换行排列，限价卖出、外部已卖、市价卖出、取消自动卖等操作更容易点。
- 移动端字段增加标签，例如成本价、预计卖价、手续费、浮盈亏、净利润，避免只看到一串数字不知道含义。

### v0.2.2 - 2026-06-03

- 修复限价买入/卖出刚挂单时最近订单金额显示为 `0` 的问题；现在会用预估挂单金额展示，成交后展示实际成交额。
- 修复部分限价买入成交后，如果交易所订单查询返回的累计成交额为 `0` 或空，未能写入未平批次的问题；现在会用成交数量乘限价补算成交额并补记账本。
- 最近订单的档位不再统一显示 `manual-entry`，会保留 `manual-limit-buy`、`manual-limit-sell` 等真实操作类型，方便排查用户反馈。

### v0.2.1 - 2026-06-03

- 新增内置 SVG 网站图标，浏览器标签页不再显示默认空白图标。
- Web 看板优化移动端布局：小屏下核心卡片单列展示、图表高度压缩、表格横向滚动、按钮更容易点击。
- 前端登录校验改为会话内缓存，首次验证后不再每次刷新都请求 `/api/login`；密码失效时才重新弹出登录框。
- 资产估值改为统计交易所 `free + locked` 余额，限价卖出挂单锁定的 BTC 也会计入总资产估值；余额行会标明锁定数量。
- 自动 agent 日志估值同步使用 `free + locked` 口径，但真实卖出前仍只检查可用 BTC，避免锁定资产被重复卖出。
- README 增加醒目的学习研究用途和风险自负声明，并新增贡献感谢区。

### v0.2.0 - 2026-06-03

- Web 看板顶部重排为行情主卡、资产盈亏、操作区三组，账户与策略、最近订单改为更清晰的双栏面板。
- 未平批次新增“市价卖出”和“外部已卖”操作。“市价卖出”对人工/波段批次开放；“外部已卖”用于同步你在 Binance 手动卖出的批次，只改账本，不会重复下单。
- 新增账本同步状态，显示账本记录的未平 BTC 与账户真实 BTC 是否一致。
- 自动卖出增加保护：如果账本批次数量大于账户实际 BTC，脚本会跳过卖出并提示先同步账本，避免卖出不存在的仓位。
- 手动买入/卖出当前均为市价成交；自定义价格属于限价挂单，需要订单成交跟踪，暂未在本版启用。
- 防守/抄底逻辑确认：低位补仓会受最大持仓、现金余额、浮亏风控共同限制；价格低不等于一定继续买。
- 波段抄底升级为分批模式：波段低位第一笔最多买 `SWING_MAX_ORDER_QUOTE`，默认 `15 USDT`；已有波段仓后，需要比上一笔再低 `SWING_ADD_STEP_PCT`，默认 `1.5%`，才会继续补下一笔。
- 波段抄底可以绕过“网格仓已有浮亏”这条软限制，但仍不会绕过急跌暂停、价格异常等硬风控。
- Web 看板“最近实盘订单”改为每页 `10` 条，使账户策略面板和订单面板高度更接近。
- 人工交易升级：支持市价买入、市价卖出、限价买入、限价卖出和挂单取消；限价单只有成交后才会写入账本，避免未成交订单误记为持仓。
- 波段仓预计卖价显示修正：波段抄底仓现在展示真实波段目标价，并在未平批次中标记“波段”；网格仓仍展示防守模式下的实际生效卖价。
- 人工买入新增 `MANUAL_BUY_AUTO_SELL` 配置，并支持每次买入时选择是否自动卖出；限价卖出中、限价卖出成交、外部已卖都会在批次表里明确标识，手动价格单独成列。
- 未卖出的人工买入批次新增批次级自动卖出开关，可在 Web 看板随时开启或取消自动卖出。
- README 新增完整配置说明和 50U 小本金建议值，覆盖基础安全、网格、防守、波段、人工交易和邮件报告。
- Web 看板认证从 HTTP Basic 改为页面登录密码 `DASHBOARD_PASSWORD`；前端接口统一改为 GET，请求参数和密码通过请求头传递，避免出现在 URL 中。
- 新增 Binance 历史 K 线回测命令，可按月份比较不同止盈比例。
- Web 看板新增策略回测区，支持当前资金模拟盘、指定日期真实 K 线、多个盈利比例对比和自动建议。
- Web 设置新增默认盈利比例修改，可选择只影响后续新单，或重算未平普通/人工批次预计卖价。
- 波段批次新增手工市价卖出和批次级自动卖出开关；取消自动卖后，波段策略不会再自动卖出该批次。
- 测试覆盖增加到 33 项，新增外部卖出同步账本、批次级自动卖出开关、未平批次重算目标价和波段分批补仓的单元测试。
