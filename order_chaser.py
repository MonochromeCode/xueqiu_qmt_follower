"""
order_chaser.py
────────────────────────────────────────────────────────────
追单 / 卡单重试逻辑 Mixin

提供：
  - OrderChaserMixin：供 XueqiuFollower 继承，注入追单和卡单重试行为
────────────────────────────────────────────────────────────
"""

import time
import logging
from typing import Dict, List

import config

logger = logging.getLogger(__name__)


class OrderChaserMixin:
    """
    追单与卡单重试逻辑 Mixin。

    依赖宿主类提供：
        self.trader   — QMTTrader 实例
    """

    _CHASE_INTERVAL       = 10   # 追单检查间隔（秒）
    _STUCK_CHECK_INTERVAL = 30   # 卡单重试检查间隔（秒）

    def _init_chaser(self):
        """初始化追单/卡单状态，在宿主 __init__ 中调用。"""
        self._chase_orders: Dict[int, dict] = {}
        self._stuck_orders: Dict[str, dict] = {}
        self._last_chase_ts:       float = 0.0
        self._last_stuck_check_ts: float = 0.0

    # ─────────────────────────────────────────────────────────
    # 追单（每 10 秒）
    # ─────────────────────────────────────────────────────────
    def _chase_unfinished_orders(self):
        """
        每 10 秒调用一次（仅在交易时间内）：

        1. 遍历 _chase_orders 中超过 10 秒的订单
        2. 通过 get_pending_orders 判断该 order_id 是否仍在挂单
           - 不在挂单列表 → 已成交或已撤，移出追踪
           - 仍在挂单     → 撤单，按最新价重新下单，用新 order_id 替换追踪记录
        3. 新下的单同样纳入追踪，下次 10 秒再检查
        """
        if not self._chase_orders:
            return

        now_ts = time.time()
        if now_ts - self._last_chase_ts < self._CHASE_INTERVAL:
            return
        self._last_chase_ts = now_ts

        # 取当前所有未成交委托（一次性查询，减少 QMT 调用次数）
        pending     = self.trader.get_pending_orders()          # 全量，不传 stock_code
        pending_map = {o["order_id"]: o for o in pending}       # 用于快速查剩余量
        pending_ids = set(pending_map.keys())

        to_remove: List[int] = []
        to_add:    Dict[int, dict] = {}

        # 先收集所有需要处理的股票代码，批量查询行情（一次 get_full_ticks 替代 N 次单股查询）
        overdue_orders = [
            (oid, info) for oid, info in self._chase_orders.items()
            if now_ts - info["ts"] >= self._CHASE_INTERVAL
        ]
        if not overdue_orders:
            return

        # 区分：仍在挂单的 vs 已成交的
        still_pending = [(oid, info) for oid, info in overdue_orders if oid in pending_ids]
        for oid, info in overdue_orders:
            if oid not in pending_ids:
                logger.debug(f"【追单】{info['stock_code']} order_id={oid} 已成交/已撤，移出追踪")
                to_remove.append(oid)

        if still_pending:
            chase_codes = list(set(info["stock_code"] for _, info in still_pending))
            ticks = self.trader.get_full_ticks(chase_codes) if chase_codes else {}

        max_chase = getattr(config, "MAX_CHASE_COUNT", 5)
        max_dev   = getattr(config, "MAX_CHASE_PRICE_DEVIATION", 0.03)

        for oid, info in still_pending:
            code          = info["stock_code"]
            direction     = info["direction"]
            age           = int(now_ts - info["ts"])
            chase_count   = info.get("chase_count", 0)
            initial_price = info.get("initial_price", 0)

            to_remove.append(oid)

            # 追单次数上限
            if chase_count >= max_chase:
                logger.warning(
                    f"【追单】{code} {direction} order_id={oid} "
                    f"已追单 {chase_count} 次，达上限（MAX_CHASE_COUNT={max_chase}），放弃"
                )
                continue

            # 追单时间上限
            max_minutes = getattr(config, "MAX_CHASE_MINUTES", 10)
            start_ts = info.get("start_ts", info["ts"])
            elapsed_min = (now_ts - start_ts) / 60
            if elapsed_min > max_minutes:
                logger.warning(
                    f"【追单】{code} {direction} order_id={oid} "
                    f"已追单 {elapsed_min:.1f} 分钟，超过上限 {max_minutes} 分钟，放弃"
                )
                continue

            # 从批量获取的 tick 中提取对手价（买入用卖一，卖出用买一）
            tick = ticks.get(code, {})
            if direction == "BUY":
                ask_list  = tick.get("askPrice")
                new_price = float(ask_list[0]) if ask_list else 0
            else:
                bid_list  = tick.get("bidPrice")
                new_price = float(bid_list[0]) if bid_list else 0
            if new_price <= 0:
                new_price = float(tick.get("lastPrice") or 0)
            if new_price <= 0:
                logger.error(f"【追单】{code} 无法获取价格，放弃重下")
                continue

            # 价格偏离上限
            if initial_price > 0 and max_dev > 0:
                deviation = abs(new_price - initial_price) / initial_price
                if deviation > max_dev:
                    logger.warning(
                        f"【追单】{code} {direction} order_id={oid} "
                        f"当前价 {new_price:.3f} 偏离初始价 {initial_price:.3f} "
                        f"达 {deviation*100:.1f}%（上限 {max_dev*100:.0f}%），放弃"
                    )
                    continue

            logger.info(
                f"【追单】{code} {direction} order_id={oid} "
                f"挂单超 {age}s 未成交（第{chase_count+1}次追单），撤单重下..."
            )
            cancelled = self.trader.cancel_orders_for_stock(code)
            if cancelled:
                self.trader.wait_until_all_cancelled(timeout=3.0, stock_code=code)

            if direction == "BUY":
                # 扣除已部分成交量，只补买剩余未成交量
                pending_order = pending_map.get(oid, {})
                traded_vol    = int(pending_order.get("traded_volume") or 0)
                remaining_vol = int(pending_order.get("order_volume") or 0) - traded_vol
                if remaining_vol <= 0:
                    logger.debug(f"【追单】{code} BUY order_id={oid} 已全部成交，移出追踪")
                    continue
                # 按剩余量重新折算买入金额
                amount = remaining_vol * new_price
                if config.LIMIT_PROTECTION and self.trader.is_limit_up(code, tick=tick):
                    logger.warning(f"【追单-风控】{code} 已涨停，放弃重下买入")
                    continue
                lot = self.trader.get_lot_size(code)
                est_volume = self.trader.calc_buy_volume(amount, new_price, min_lot=lot)
                if est_volume <= 0:
                    logger.warning(f"【追单】{code} 计算买入股数为0，放弃重下")
                    continue
                logger.info(
                    f"【追单-重下买入】{code} 约{est_volume}股 @ {new_price:.3f} "
                    f"（剩余未成交={remaining_vol}股，折算金额≈¥{amount:,.0f}）"
                )
                new_oid = self.trader.buy(
                    stock_code=code,
                    amount=amount,
                    price=new_price,
                    remark=f"雪球追单买入-{config.PORTFOLIO_ID}",
                )
                chase_volume = est_volume
            else:  # SELL
                # 扣除已部分成交量，只补卖剩余未成交量
                pending_order = pending_map.get(oid, {})
                traded_vol    = int(pending_order.get("traded_volume") or 0)
                remaining_vol = int(pending_order.get("order_volume") or 0) - traded_vol
                if remaining_vol <= 0:
                    logger.debug(f"【追单】{code} SELL order_id={oid} 已全部成交，移出追踪")
                    continue
                positions = self.trader.get_positions()
                pos       = positions.get(code)
                can_use   = pos["can_use_volume"] if pos else 0
                if can_use <= 0:
                    logger.warning(f"【追单】{code} 可用持仓为0，无需重下卖出")
                    continue
                sell_vol = min(remaining_vol, can_use)
                if config.LIMIT_PROTECTION and self.trader.is_limit_down(code, tick=tick):
                    logger.warning(f"【追单-风控】{code} 已跌停，放弃重下卖出")
                    continue
                logger.info(
                    f"【追单-重下卖出】{code} {sell_vol}股 @ {new_price:.3f} "
                    f"（剩余未成交={remaining_vol}股）"
                )
                new_oid = self.trader.sell(
                    stock_code=code,
                    volume=sell_vol,
                    price=new_price,
                    remark=f"雪球追单卖出-{config.PORTFOLIO_ID}",
                )
                chase_volume = remaining_vol

            if new_oid is not None and new_oid > 0:
                to_add[new_oid] = {
                    "stock_code":    code,
                    "direction":     direction,
                    "amount":        info["amount"],
                    "volume":        chase_volume,
                    "ts":            time.time(),
                    "start_ts":      info.get("start_ts", info["ts"]),
                    "chase_count":   chase_count + 1,
                    "initial_price": initial_price,
                }
                logger.info(f"【追单】{code} 新委托 order_id={new_oid}")
            else:
                logger.error(f"【追单】{code} 重下失败")

        for oid in to_remove:
            self._chase_orders.pop(oid, None)
        self._chase_orders.update(to_add)

    # ─────────────────────────────────────────────────────────
    # 卡单重试（每 30 秒）
    # ─────────────────────────────────────────────────────────
    def _retry_stuck_orders(self):
        """
        每 30 秒检查一次因涨停/跌停跳过的订单。
        涨停/跌停解除后自动重试，避免需要等到下次再平衡触发。
        """
        if not self._stuck_orders:
            return
        now_ts = time.time()
        if now_ts - self._last_stuck_check_ts < self._STUCK_CHECK_INTERVAL:
            return
        self._last_stuck_check_ts = now_ts

        codes = list(self._stuck_orders.keys())
        ticks = self.trader.get_full_ticks(codes) if codes else {}

        to_remove = []
        for code, info in self._stuck_orders.items():
            tick      = ticks.get(code)
            direction = info["direction"]

            if direction == "BUY":
                if self.trader.is_limit_up(code, tick=tick):
                    continue  # 仍涨停，继续等
                # 重试时重新查询可用资金，避免使用涨停时计算的过期金额
                avail_cash, _ = self.trader.get_asset()
                retry_amount  = min(info["amount"], avail_cash) if avail_cash > 0 else info["amount"]
                if retry_amount <= 0:
                    logger.warning(f"【卡单重试】{code} 可用资金不足，放弃重试")
                    to_remove.append(code)
                    continue
                logger.info(f"【卡单重试】{code} 涨停已解除，重新尝试买入 ¥{retry_amount:,.0f}")
                order_id = self.trader.buy(
                    stock_code=code,
                    amount=retry_amount,
                    price=None,
                    remark=f"雪球卡单重试-{config.PORTFOLIO_ID}",
                )
                if order_id is not None and order_id > 0:
                    tick_price = float((tick or {}).get("lastPrice") or 0)
                    self._chase_orders[order_id] = {
                        "stock_code":    code,
                        "direction":     "BUY",
                        "amount":        retry_amount,
                        "volume":        0,
                        "ts":            time.time(),
                        "start_ts":      time.time(),
                        "chase_count":   0,
                        "initial_price": tick_price,
                    }
                to_remove.append(code)

            else:  # SELL
                if self.trader.is_limit_down(code, tick=tick):
                    continue  # 仍跌停，继续等
                logger.info(f"【卡单重试】{code} 跌停已解除，重新尝试卖出")
                volume   = info["volume"] or None  # 0 表示全部卖出
                order_id = self.trader.sell(
                    stock_code=code,
                    volume=volume,
                    price=None,
                    remark=f"雪球卡单重试-{config.PORTFOLIO_ID}",
                )
                if order_id is not None and order_id > 0:
                    positions = self.trader.get_positions()
                    pos       = positions.get(code)
                    sell_vol  = pos.get("can_use_volume", 0) if pos else (volume or 0)
                    tick_price = float((tick or {}).get("lastPrice") or 0)
                    self._chase_orders[order_id] = {
                        "stock_code":    code,
                        "direction":     "SELL",
                        "amount":        0,
                        "volume":        sell_vol,
                        "ts":            time.time(),
                        "start_ts":      time.time(),
                        "chase_count":   0,
                        "initial_price": tick_price,
                    }
                to_remove.append(code)

        for code in to_remove:
            self._stuck_orders.pop(code, None)
