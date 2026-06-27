"""
notifier.py
────────────────────────────────────────────────────────────
再平衡完成通知

提供：
  - notify_rebalance_done()：向钉钉机器人发送持仓快照
────────────────────────────────────────────────────────────
"""

import datetime
import logging
from typing import Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)


def notify_rebalance_done(
    webhook: str,
    portfolio_id: str,
    results: List[Tuple[str, str, str]],
    total_amount: float,
    actual_cash: float,
    target_cash: float,
    positions: Dict[str, dict],
    ticks: Dict[str, dict],
) -> None:
    """
    再平衡完成后向钉钉机器人发送持仓快照。
    仅在有成功下单（status=="ok"）时触发，避免无操作时刷屏。

    Args:
        webhook:      钉钉机器人 Webhook URL
        portfolio_id: 雪球组合 ID（仅用于显示）
        results:      [(stock_code, direction, status), ...]
        total_amount: 账户总资产（元）
        actual_cash:  当前可用现金（元）
        target_cash:  目标现金（元）
        positions:    {stock_code: {"volume": int, "open_price": float, "market_value": float}}
        ticks:        {stock_code: {"lastPrice": float, ...}}
    """
    if not webhook:
        return

    ok_results = [(c, d) for c, d, s in results if s == "ok"]
    if not ok_results:
        return

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"【雪球跟单】再平衡完成 {now_str}"]
    lines.append(f"组合: {portfolio_id}  账户总资产: ¥{total_amount:,.0f}")
    lines.append("")

    lines.append("本次成交:")
    for code, direction in ok_results:
        lines.append(f"  {direction}  {code}")

    lines.append("")
    lines.append("当前持仓:")
    if positions:
        for code, pos in sorted(positions.items()):
            volume = int(pos.get("volume") or 0)
            tick   = ticks.get(code, {})
            price  = float(tick.get("lastPrice") or 0) or float(pos.get("open_price") or 0)
            mv     = price * volume if price > 0 else float(pos.get("market_value") or 0)
            pct    = mv / total_amount * 100 if total_amount > 0 else 0
            lines.append(f"  {code}  {volume}股  市值≈¥{mv:,.0f}  占比{pct:.1f}%")
    else:
        lines.append("  （空仓）")

    lines.append("")
    lines.append(f"现金: ¥{actual_cash:,.0f}  目标现金: ¥{target_cash:,.0f}")

    text = "\n".join(lines)
    try:
        resp = requests.post(
            webhook,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=5,
        )
        resp.raise_for_status()
        logger.info("钉钉通知已发送")
    except Exception as e:
        logger.warning(f"钉钉通知发送失败: {e}")
