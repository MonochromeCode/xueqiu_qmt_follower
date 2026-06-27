# QMT 断线重连优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 QMT 断线重连的五个缺陷：启动失败直接退出、固定30s间隔无退避、重连后追单状态失效、session_id 同秒冲突、callback 注册脆弱。

**Architecture:** `qmt_trader.py` 内部增加指数退避逻辑和 `on_reconnect_callback` 钩子；`follower.py` 在启动时改为重试循环，并注册回调在重连后清理 `_chase_orders`/`_stuck_orders` 并触发强制对账。两个文件各自独立修改，通过钩子接口解耦。

**Tech Stack:** Python 3.x，xtquant（仅 miniQMT 环境可用，本地开发为 mock 模式）

## Global Constraints

- 不引入后台线程（xtquant 线程安全性未明确）
- mock 模式（`HAS_XTQUANT=False`）行为不变，所有改动需在 mock 路径下正常运行
- `_pending_rebalance` / `_save_state` 语义不变
- 不 `pip install xtquant`，xtquant 仅在 miniQMT 环境中可用
- 运行方式：`"C:\国金证券QMT交易端\bin.x64\python.exe" main.py` 或 `python main.py`（mock 模式）

---

## 文件变更清单

| 文件 | 操作 | 变更位置 |
|------|------|---------|
| `qmt_trader.py` | 修改 | `__init__`（行 91）、`connect()`（行 124）、`reconnect_if_needed()`（行 896）|
| `qmt_trader.py` | 新增 | `set_reconnect_callback()` 方法（置于 `reconnect_if_needed` 之后）|
| `follower.py` | 修改 | `start()`（行 202）、`_main_loop()` 断线日志（行 244）|
| `follower.py` | 新增 | `_on_qmt_reconnected()` 方法 |

---

## Task 1: qmt_trader.py — 指数退避 + session 计数器 + callback 钩子

**Files:**
- Modify: `qmt_trader.py:91-116`（`__init__` 重连字段）
- Modify: `qmt_trader.py:124-173`（`connect()` session_id 修复）
- Modify: `qmt_trader.py:896-929`（`reconnect_if_needed()` 重写）
- Add method: `qmt_trader.py`（`set_reconnect_callback()`，置于 `reconnect_if_needed` 之后）

**Interfaces:**
- Produces:
  - `QMTTrader.set_reconnect_callback(cb: Callable[[], None]) -> None`
  - `QMTTrader._reconnect_base_interval: int`（Task 2 的日志需要读取）
  - `QMTTrader._reconnect_max_interval: int`
  - `QMTTrader._reconnect_attempts: int`

---

- [ ] **Step 1: 修改 `__init__` — 替换重连字段，增加 session 计数器和 callback 槽**

在 `qmt_trader.py` 第 114–116 行，将：

```python
        # 重连参数
        self._reconnect_interval = 30   # 断线后每 30 秒尝试重连一次
        self._last_reconnect_ts  = 0.0
```

替换为：

```python
        # 重连参数（指数退避）
        self._reconnect_base_interval = 30    # 首次等待 30s
        self._reconnect_max_interval  = 300   # 最长等待 5 分钟（上限）
        self._reconnect_attempts      = 0     # 连续失败次数，成功后归零
        self._last_reconnect_ts       = 0.0
        self._session_counter         = 0     # 单调递增，避免同秒重连 session_id 冲突
        self._on_reconnect_cb         = None  # 重连成功回调，由 follower 注册
```

---

- [ ] **Step 2: 修改 `connect()` — 先清理旧 trader，改用计数器 session_id**

在 `qmt_trader.py` 的 `connect()` 方法（行 124），在 `try:` 块内，将：

```python
        try:
            session_id = int(time.time() * 1000) % 1000000  # 6位 session id
            self._trader = XtQuantTrader(self.qmt_path, session_id)
```

替换为：

```python
        try:
            # 先清理旧对象，避免重连时 callback 悬空
            if self._trader is not None:
                try:
                    self._trader.stop()
                except Exception:
                    pass
                self._trader = None

            self._session_counter += 1
            session_id = self._session_counter
            self._trader = XtQuantTrader(self.qmt_path, session_id)
```

---

- [ ] **Step 3: 同时移除 `reconnect_if_needed()` 内的重复 stop 逻辑**

`reconnect_if_needed()`（行 916–921）原有：

```python
        # 先清理旧对象
        try:
            if self._trader:
                self._trader.stop()
        except Exception:
            pass
        self._trader = None
```

这段在 Step 2 之后已由 `connect()` 负责，直接删除这 6 行（保留后续的 `ok = self.connect()` 调用）。

---

- [ ] **Step 4: 重写 `reconnect_if_needed()` — 指数退避 + 调用 callback**

将 `reconnect_if_needed()` 整个方法（行 896–929）替换为：

```python
    def reconnect_if_needed(self) -> bool:
        """
        若当前连接已断开，按指数退避策略尝试重连。

        退避序列：30s → 60s → 120s → 240s → 300s（上限）
        重连成功后调用 _on_reconnect_cb（若已注册）。

        Returns:
            True  — 当前已连接（无需重连 或 重连成功）
            False — 仍未连接（在冷却期内）
        """
        if self._mock:
            return True
        if self._connected:
            return True

        now_ts = time.time()
        wait = min(
            self._reconnect_base_interval * (2 ** self._reconnect_attempts),
            self._reconnect_max_interval,
        )
        if now_ts - self._last_reconnect_ts < wait:
            return False

        self._last_reconnect_ts = now_ts
        logger.warning(
            f"【重连】检测到 QMT 断线，尝试重新连接"
            f"（第 {self._reconnect_attempts + 1} 次，本次间隔 {wait:.0f}s）..."
        )

        ok = self.connect()
        if ok:
            logger.info("【重连】QMT 重连成功")
            self._reconnect_attempts = 0
            if self._on_reconnect_cb is not None:
                try:
                    self._on_reconnect_cb()
                except Exception as e:
                    logger.error(f"【重连】回调执行异常: {e}")
        else:
            self._reconnect_attempts += 1
            next_wait = min(
                self._reconnect_base_interval * (2 ** self._reconnect_attempts),
                self._reconnect_max_interval,
            )
            logger.error(
                f"【重连】QMT 重连失败（已失败 {self._reconnect_attempts} 次），"
                f"{next_wait:.0f}s 后再试"
            )
        return ok
```

---

- [ ] **Step 5: 新增 `set_reconnect_callback()` 方法**

在 `reconnect_if_needed()` 之后、`# ─── 属性 ───` 注释块之前，插入：

```python
    def set_reconnect_callback(self, cb) -> None:
        """注册重连成功回调。重连成功后立即调用，用于清理上层失效的订单追踪状态。"""
        self._on_reconnect_cb = cb
```

---

- [ ] **Step 6: 验证 mock 模式下退避逻辑正确**

在项目根目录运行：

```bash
python -c "
import sys, time
sys.path.insert(0, '.')
import config
config.QMT_PATH = 'mock'
config.ACCOUNT_ID = 'mock'

from qmt_trader import QMTTrader

t = QMTTrader('mock', 'mock')
assert hasattr(t, '_reconnect_base_interval'), '_reconnect_base_interval missing'
assert hasattr(t, '_reconnect_max_interval'),  '_reconnect_max_interval missing'
assert hasattr(t, '_reconnect_attempts'),      '_reconnect_attempts missing'
assert hasattr(t, '_session_counter'),         '_session_counter missing'
assert hasattr(t, '_on_reconnect_cb'),         '_on_reconnect_cb missing'

# mock 模式下 reconnect_if_needed 永远返回 True
t._connected = False
assert t.reconnect_if_needed() == True, 'mock mode should always return True'

# 验证 set_reconnect_callback 注册
called = []
t.set_reconnect_callback(lambda: called.append(1))
assert t._on_reconnect_cb is not None

# 验证退避公式
base = t._reconnect_base_interval
mx   = t._reconnect_max_interval
for i, expected in enumerate([30, 60, 120, 240, 300, 300]):
    got = min(base * (2 ** i), mx)
    assert got == expected, f'backoff[{i}] expected {expected} got {got}'

print('Task 1 验证通过')
"
```

预期输出：`Task 1 验证通过`

---

- [ ] **Step 7: Commit**

```bash
cd D:/githubDemo/xueqiu_qmt_follower
git add qmt_trader.py
git commit -m "feat: exponential backoff reconnect, session counter, reconnect callback hook"
```

---

## Task 2: follower.py — 启动重试循环 + 重连后状态清理

**Files:**
- Modify: `follower.py:202-215`（`start()` 方法）
- Modify: `follower.py:244-248`（`_main_loop()` 断线日志）
- Add method: `follower.py`（`_on_qmt_reconnected()`，置于 `_check_offhour_cancel` 之前）

**Interfaces:**
- Consumes:
  - `QMTTrader.set_reconnect_callback(cb)` — Task 1 新增
  - `QMTTrader._reconnect_base_interval: int` — Task 1 新增
  - `QMTTrader._reconnect_max_interval: int` — Task 1 新增
  - `QMTTrader._reconnect_attempts: int` — Task 1 新增

---

- [ ] **Step 1: 修改 `start()` — 改为重试循环**

将 `follower.py` 的 `start()` 方法（行 202–215）整体替换为：

```python
    def start(self):
        # 启动时带重试：等待 miniQMT 登录，不再因首次连接失败直接退出
        while not self.trader.connect():
            logger.warning(
                "QMT 连接失败，30s 后重试"
                "（请确认 miniQMT 客户端已启动并登录）..."
            )
            time.sleep(30)

        self.trader.set_reconnect_callback(self._on_qmt_reconnected)
        self._sync_initial_rebalancing_id()

        logger.info("开始监控雪球组合调仓通知...")
        try:
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("用户中断，程序退出")
        finally:
            self.trader.disconnect()
```

---

- [ ] **Step 2: 新增 `_on_qmt_reconnected()` 方法**

在 `follower.py` 的 `_check_offhour_cancel` 方法之前（约行 313），插入：

```python
    # ─────────────────────────────────────────────────────────
    # QMT 重连成功回调
    # ─────────────────────────────────────────────────────────
    def _on_qmt_reconnected(self):
        """
        QMT 重连成功后由 qmt_trader 调用。

        重连后旧 order_id 在 QMT 侧已不存在，继续追单会产生无效撤单请求。
        交易时间内重连还需触发一次强制对账，确保持仓与雪球一致。
        """
        logger.warning(
            "【重连后清理】清空失效追单/卡单记录"
            f"（_chase_orders={len(self._chase_orders)} 笔，"
            f"_stuck_orders={len(self._stuck_orders)} 笔）"
        )
        self._chase_orders.clear()
        self._stuck_orders.clear()
        if _is_trade_time():
            logger.warning(
                "【重连后清理】交易时间内重连，标记待对账，"
                "下次主循环将强制执行一次再平衡"
            )
            self._pending_rebalance = True
            self._save_state()
```

---

- [ ] **Step 3: 更新 `_main_loop()` 断线日志**

在 `_main_loop()` 中（行 244–248），将：

```python
            # ── 断线重连检测（每次循环都检查）──
            if not self.trader.reconnect_if_needed():
                logger.warning("QMT 未连接，等待重连...")
                time.sleep(30)
                continue
```

替换为：

```python
            # ── 断线重连检测（每次循环都检查）──
            if not self.trader.reconnect_if_needed():
                attempts  = self.trader._reconnect_attempts
                next_wait = min(
                    self.trader._reconnect_base_interval * (2 ** attempts),
                    self.trader._reconnect_max_interval,
                )
                logger.warning(
                    f"QMT 断线中（已失败 {attempts} 次），"
                    f"{next_wait:.0f}s 后将重试..."
                )
                time.sleep(10)   # 短 sleep 保持主循环响应，reconnect_if_needed 内部已限速
                continue
```

---

- [ ] **Step 4: 验证 mock 模式下启动和回调逻辑**

```bash
python -c "
import sys, time
sys.path.insert(0, '.')

# 验证 _on_qmt_reconnected 存在且可调用
import config
# 使用 mock 配置（不实际连接 QMT）
from unittest.mock import patch, MagicMock

# 直接测试 _on_qmt_reconnected 逻辑（不启动完整 follower）
from follower import XueqiuFollower

# 构造最小 follower 实例（mock trader 已在 QMTTrader 内置）
f = XueqiuFollower.__new__(XueqiuFollower)
f._chase_orders  = {1001: {'stock_code': '600519.SH'}, 1002: {'stock_code': '000001.SZ'}}
f._stuck_orders  = {'000002.SZ': {'direction': 'BUY'}}
f._pending_rebalance = False

# 模拟非交易时间（避免触发 _save_state）
import follower as fmod
orig = fmod._is_trade_time
fmod._is_trade_time = lambda: False

f._on_qmt_reconnected()
assert len(f._chase_orders) == 0, '_chase_orders 应被清空'
assert len(f._stuck_orders) == 0, '_stuck_orders 应被清空'
assert f._pending_rebalance == False, '非交易时间不应设 pending'

# 模拟交易时间
fmod._is_trade_time = lambda: True
import pathlib, json, tempfile, os
tmp = tempfile.mktemp(suffix='.json')
f._state_file = pathlib.Path(tmp)
f._last_rebalancing_id = None
f._on_qmt_reconnected()
assert f._pending_rebalance == True, '交易时间应设 pending_rebalance'
os.unlink(tmp)

fmod._is_trade_time = orig
print('Task 2 验证通过')
"
```

预期输出：`Task 2 验证通过`

---

- [ ] **Step 5: 集成冒烟测试 — mock 模式启动不报错**

```bash
python -c "
import threading, time, sys
sys.path.insert(0, '.')

# mock 模式下，start() 应能正常连接（connect() 在 mock 下直接返回 True）
# 验证不再直接退出
from follower import XueqiuFollower
import follower as fmod

f = XueqiuFollower()

# 替换 _main_loop 避免无限循环
def mock_loop(self):
    raise KeyboardInterrupt
fmod.XueqiuFollower._main_loop = mock_loop

# start() 应在 connect 成功后注册 callback，然后因 KeyboardInterrupt 正常退出
f.start()
assert f.trader._on_reconnect_cb is not None, 'callback 未注册'
print('集成冒烟测试通过')
"
```

预期输出：`集成冒烟测试通过`（可能有日志输出，属正常）

---

- [ ] **Step 6: Commit**

```bash
cd D:/githubDemo/xueqiu_qmt_follower
git add follower.py
git commit -m "feat: startup retry loop, post-reconnect state cleanup and force-rebalance"
```

---

## 验收标准

| 场景 | 预期行为 |
|------|---------|
| miniQMT 未启动时运行 `main.py` | 每 30s 打印重试日志，不退出 |
| 运行中 QMT 断线 | 首次 30s 后重试；连续失败退避到 60s → 120s → 240s → 300s |
| 重连成功（交易时间） | 日志打印"重连后清理"，`_chase_orders` 和 `_stuck_orders` 清空，`pending_rebalance=True` |
| 重连成功（非交易时间） | 仅清空追单记录，不设 `pending_rebalance` |
| 同秒内两次重连 | session_id 不同（计数器递增），无冲突 |
