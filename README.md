# 雪球组合 QMT 跟踪交易脚本

> 基于 miniQMT（xtquant）自动跟踪雪球投资组合调仓，按账户总资产比例精确对齐持仓

---

## 文件结构

```
xueqiu_qmt_follower/
├── config.py          # 配置文件（必须修改）
├── xueqiu_client.py   # 雪球数据获取模块
├── qmt_trader.py      # miniQMT 交易执行模块
├── follower.py        # 跟踪主逻辑
├── main.py            # 程序入口
├── state.json         # 运行状态持久化（自动生成）
├── check_update.py    # 诊断：验证雪球调仓检测
├── test_api.py        # 诊断：探测雪球接口存活
└── logs/              # 运行日志（自动创建）
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install requests
# xtquant 已内置在 miniQMT 安装目录中，无需 pip 安装
```

### 2. 配置 `config.py`

必须修改以下 4 项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `QMT_PATH` | miniQMT `userdata_mini` 目录绝对路径 | `r"C:\国金证券QMT交易端\userdata_mini"` |
| `ACCOUNT_ID` | 资金账号（数字字符串） | `"1234567890"` |
| `PORTFOLIO_ID` | 雪球组合代码 | `"ZH123456"` |
| `XUEQIU_COOKIE` | 雪球登录 Cookie | 见下方获取方法 |

### 3. 获取雪球 Cookie

1. Chrome 浏览器登录 [xueqiu.com](https://xueqiu.com)
2. 按 `F12` 打开开发者工具 → Network 标签
3. 刷新页面，点击任意请求
4. 在 Request Headers 中找到 `Cookie:` 行
5. 复制完整 Cookie 字符串粘贴到 `config.py`

Cookie 有效期通常为 30 天，过期后需重新获取。过期时程序会输出 CRITICAL 日志，并可通过钉钉/企微 Webhook 推送告警（见 `DINGTALK_WEBHOOK` 配置）。

### 4. 确保 miniQMT 已登录

运行脚本前，必须先打开 miniQMT 客户端并成功登录账号。

### 5. 运行

```bash
# 方法一：用 miniQMT 内置 Python（包含 xtquant，推荐）
"C:\国金证券QMT交易端\bin.x64\python.exe" main.py

# 方法二：将 xtquant 加入 sys.path 后用系统 Python
python main.py
```

xtquant 无法导入时自动进入**模拟模式**——所有交易指令只打印日志，不实际下单。

---

## 跟单模式

通过 `config.TRADE_MODE` 切换，默认 `ratio_follow`。

### ratio_follow（推荐）

自动读取账户实际总资产，按雪球各股权重计算目标市值，与当前持仓做差值后下单。

```
目标市值[i] = 账户总资产 × (雪球权重[i] / 100)
差值 > 0  → 买入
差值 < 0  → 卖出
|差值| < REBALANCE_THRESHOLD 且 < REBALANCE_ABS_THRESHOLD → 忽略
```

执行顺序：**先卖后买**

1. 执行所有卖单（限价单，对手价）
2. 等待卖单成交回款（`SELL_SETTLE_TIMEOUT` 秒）
3. 刷新持仓和现金快照，重算买入差值
4. 现金不足时按比例缩减各买单

关键配置：

```python
REBALANCE_THRESHOLD     = 0.02    # 相对偏差门槛（2%）
REBALANCE_ABS_THRESHOLD = 200.0   # 绝对金额门槛（元）
SELL_SETTLE_TIMEOUT     = 8.0     # 等待卖单回款最长秒数
TOTAL_AMOUNT            = ...     # 仅作 fallback，正常情况不生效
```

### fixed_amount（旧模式）

对雪球的新建仓/加仓/减仓/清仓事件，每次以固定金额 `FIXED_AMOUNT` 执行。仅保留用于兼容。

---

## 工作流程

```
主循环（每 POLL_INTERVAL_SECONDS 秒，每次只拉一次雪球接口）
  ├─ 断线重连检测
  ├─ 非交易时间
  │    └─ 每 OFFHOUR_CANCEL_INTERVAL 秒检查雪球调仓 ID
  │         ID 变化 → 撤销 QMT 全部挂单 → 标记 pending_rebalance
  └─ 交易时间
       ├─ pending_rebalance=True
       │    └─ 等待 OPEN_COOLDOWN_SECONDS 冷静期 → 执行再平衡 → 清除标记
       ├─ 检测到新调仓通知 或 5 分钟兜底定时检查
       │    └─ 撤销现有挂单 → 计算差值 → 先卖（等回款）→ 刷新快照 → 买入
       │    └─ 有成功下单时 → 发送钉钉持仓快照通知
       ├─ 每 10 秒：追单检查
       │    未成交挂单（扣除已部分成交量）→ 撤单 → 按最新对手价重下
       └─ 每 30 秒：涨跌停卡单检查
            价格解除 → 自动重下 → 加入追单队列
```

---

## 追单机制

下单后每 10 秒检查一次是否成交。若有部分成交，只补下剩余未成交量（`委托量 - 已成交量`），避免超买/超卖。未成交时撤单并按最新对手价重新下单，直到以下任一条件触发放弃：

| 终止条件 | 配置项 | 默认值 |
|----------|--------|--------|
| 追单次数达上限 | `MAX_CHASE_COUNT` | 5 次 |
| 当前价偏离初始信号价超过阈值 | `MAX_CHASE_PRICE_DEVIATION` | 3% |
| 涨停（买入）/ 跌停（卖出） | `LIMIT_PROTECTION` | True |

---

## 涨跌停卡单

涨停/跌停时跳过的订单会记入卡单队列，每 30 秒自动检查一次。价格解除后立即重下并加入追单追踪，不需要等到下次再平衡触发。新调仓信号到来时清空旧卡单，避免用过期量重试。

---

## 钉钉通知

填写 `config.DINGTALK_WEBHOOK` 后，每次再平衡有成功下单时自动推送：

- 本次成交明细（买入/卖出股票列表）
- 当前完整持仓快照（实时市值、占比）
- 账户总资产和现金状态

无操作时不推送，避免刷屏。Cookie 失效时也会单独推送告警。

---

## 风控机制

| 风控项 | 配置项 | 默认值 |
|--------|--------|--------|
| 单笔最大金额 | `MAX_SINGLE_ORDER_AMOUNT` | 15,000 元 |
| 单日最大交易笔数 | `MAX_DAILY_TRADES` | 100 笔 |
| 最低现金比例 | `MIN_CASH_RATIO` | 5% |
| 涨停不追买 / 跌停不追卖 | `LIMIT_PROTECTION` | True |
| 调仓 ID 去重 | state.json 持久化 | 重启后不重复下单 |
| T+1 保护 | 自动识别可用量=0 | 当日买入不卖出 |
| ST 股票涨跌幅 | 自动查询名称缓存（当日有效） | ±5% |

---

## 可转债支持

代码前缀 `11`（沪市）/ `12`（深市）自动识别为可转债：

- 最小交易单位 **10 张**（A 股为 100 股）
- 涨跌幅限制 **±20%**
- 买入价在卖一价基础上加 `CB_BUY_OFFSET`，卖出价在买一价基础上减 `CB_SELL_OFFSET`，确保流动性差时也能快速成交

---

## 跨重启状态

`state.json` 保存 `last_rebalancing_id` 和 `pending_rebalance` 标记。重启后：
- 不重复执行已处理的调仓
- 若重启前已标记待开盘重算，开盘后自动继续执行

---

## 注意事项

1. **Cookie 是真实 auth token，勿提交到版本库，勿分享给他人**
2. **xtquant 只能用 miniQMT 内置版本，不要 `pip install xtquant`**
3. **雪球 API 为非官方接口，可能随时变更**；`test_api.py` 可用于快速探测接口存活状态
4. **实盘使用前请先在模拟账号充分验证**
5. A 股（SH / SZ / BJ）及可转债均支持；港股代码可解析但未经实盘验证

---

## 常见问题

**Q: 提示 `xtquant 未安装，进入模拟模式`**

将 xtquant 目录加入 Python 路径，或直接使用 miniQMT 内置 Python：
```python
import sys
sys.path.append(r"C:\国金证券QMT交易端\bin.x64\Lib\site-packages")
```

**Q: 雪球接口返回 401 / Cookie 失效**

重新从浏览器获取 Cookie 并更新 `config.py`。

**Q: 开盘时触发了大量买卖单**

正常现象。若前一天非交易时间雪球发生了调仓变化（或程序重启），开盘后 `pending_rebalance` 机制会强制执行一次全量再平衡。`OPEN_COOLDOWN_SECONDS` 可控制开盘后等待多久再执行。

**Q: 追单一直在撤单重下，停不下来**

检查 `MAX_CHASE_COUNT`（次数上限）和 `MAX_CHASE_PRICE_DEVIATION`（价格偏离上限）是否配置合理。也可能是该股流动性极差，可考虑加大 `CB_BUY_OFFSET` / `CB_SELL_OFFSET`（针对可转债）。

**Q: 涨停股买不到，怎么处理**

程序会自动记录到卡单队列，每 30 秒检查一次，涨停解除后立即重下，无需手动干预。

**Q: 买入后资金不足废单**

可能是卖单尚未成交回款就开始买入。调大 `SELL_SETTLE_TIMEOUT`（默认 8 秒）给卖单更多时间成交；或检查账户是否有其他持仓占用资金。

**Q: 下单后立即被撤单**

查看 `logs/` 目录日志，通常是账号权限问题或价格超出涨跌停限制。
