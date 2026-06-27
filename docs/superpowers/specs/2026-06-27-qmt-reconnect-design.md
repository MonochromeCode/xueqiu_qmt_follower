# QMT 断线重连优化设计

日期：2026-06-27

## 背景

当前断线重连逻辑存在以下缺陷：

| 问题 | 文件 | 行号 | 现状 |
|------|------|------|------|
| 启动失败直接退出 | follower.py | 202 | `connect()` 失败 → `return`，需手动重启 |
| 固定 30s 重连间隔 | qmt_trader.py | 115 | 长时间断线无退避，持续无效轮询 |
| 重连后追单记录失效 | follower.py | 145 | `_chase_orders` 保存旧 order_id，重连后在 QMT 不存在 |
| session_id 同秒重连冲突 | qmt_trader.py | 138 | `int(time.time()*1000) % 1000000` 同秒重连产生相同 ID |
| callback 注册方式脆弱 | qmt_trader.py | 145 | `type()` 动态建类，旧 callback 对象悬空 |

## 方案选择

选择**方案二：协作重连**（qmt_trader + follower 双向协作），不引入后台线程（xtquant 线程安全性未明确）。

## 设计

### qmt_trader.py

#### 1. 指数退避重连间隔

替换固定 `_reconnect_interval = 30`：

```python
self._reconnect_base_interval = 30   # 首次等待 30s
self._reconnect_max_interval  = 300  # 最长等待 5 分钟
self._reconnect_attempts      = 0    # 连续失败次数
```

`reconnect_if_needed()` 中计算等待时间：

```python
wait = min(
    self._reconnect_base_interval * (2 ** self._reconnect_attempts),
    self._reconnect_max_interval
)
```

重连成功后重置 `_reconnect_attempts = 0`；失败后 `+= 1`。

退避序列：30s → 60s → 120s → 240s → 300s（上限）

#### 2. session_id 用单调计数器

```python
# __init__
self._session_counter = 0

# connect() 中
self._session_counter += 1
session_id = self._session_counter
```

同一进程内每次重连 session_id 单调递增，避免同秒重连冲突。

#### 3. 新增 on_reconnect_callback 钩子

```python
def set_reconnect_callback(self, cb):
    """注册重连成功回调，由 follower 用来清理失效状态"""
    self._on_reconnect_cb = cb
```

`reconnect_if_needed()` 在重连成功后调用：

```python
if ok and self._on_reconnect_cb:
    self._on_reconnect_cb()
```

#### 4. 整理 callback 注册

将 `type()` 动态类替换为正常的内部继承类 `_QMTCallback(XtQuantTraderCallback)`，在 `connect()` 开头先 stop 旧 trader 避免 callback 悬空。

### follower.py

#### 1. 启动时带重试的 connect 循环

```python
def start(self):
    while not self.trader.connect():
        logger.warning("QMT 连接失败，30s 后重试（请确认 miniQMT 已登录）...")
        time.sleep(30)

    self.trader.set_reconnect_callback(self._on_qmt_reconnected)
    self._sync_initial_rebalancing_id()
    ...
```

程序不再因 QMT 未启动而退出，等待用户登录 miniQMT 后自动继续。

#### 2. 重连成功回调 _on_qmt_reconnected

```python
def _on_qmt_reconnected(self):
    logger.warning("【重连后清理】清空失效追单/卡单记录，标记待对账")
    self._chase_orders.clear()
    self._stuck_orders.clear()
    if _is_trade_time():
        self._pending_rebalance = True
        self._save_state()
```

- 清空 `_chase_orders`：旧 order_id 在 QMT 重连后不可查，继续追单会产生无效撤单请求
- 清空 `_stuck_orders`：重连后行情状态未知，重新由主流程判断
- 交易时间内重连 → 设 `pending_rebalance=True`，开盘后（或下次循环）强制对账一次，确保持仓与雪球一致

#### 3. 断线日志改善

主循环断线等待日志改为显示重试次数和下次重试倒计时，便于运维判断。

## 变更范围

| 文件 | 改动类型 | 估计行数 |
|------|----------|---------|
| qmt_trader.py | 修改 `__init__`、`connect`、`reconnect_if_needed`，新增 `set_reconnect_callback` | ~40 行 |
| follower.py | 修改 `start`，新增 `_on_qmt_reconnected` | ~20 行 |

## 不变更的内容

- 主循环结构和轮询周期不变
- `_pending_rebalance` / `_save_state` 语义不变
- mock 模式行为不变
- 不引入后台线程
