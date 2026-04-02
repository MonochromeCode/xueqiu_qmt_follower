"""
check_update.py
────────────────────────────────────────────────────────────
一分钟快速检测：雪球组合是否有调仓更新

用法：
  python check_update.py

首次运行会记录当前最新调仓 ID；
之后每隔 INTERVAL 秒轮询一次，共持续 60 秒，
如果 ID 变化则打印新调仓详情并退出。
────────────────────────────────────────────────────────────
"""

import sys
import time
import json
import logging
import requests
from datetime import datetime

# ─── 配置区（修改这里）────────────────────────────────────
PORTFOLIO_ID = ""          # ← 填写雪球组合代码，如 ZH123456
COOKIE       = ""          # ← 填写你的雪球 Cookie
INTERVAL     = 5           # 轮询间隔（秒）
DURATION     = 60          # 总检测时长（秒）
# ──────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_URL  = "https://xueqiu.com"
CUBE_BASE = "https://xueqiu.com/cubes"
STOCK_BASE = "https://stock.xueqiu.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer":  "https://xueqiu.com/",
    "Accept":   "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def get_session(cookie: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({**HEADERS, "Cookie": cookie})
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


def fetch_latest_rb_id(session: requests.Session, portfolio_id: str):
    """
    获取最新调仓记录 ID 及时间戳
    接口优先级：current > v5 > history > snowmart
    """
    # 方案1：新版 current 接口 (xueqiu.com)
    url1 = f"{BASE_URL}/cubes/rebalancing/current.json"
    try:
        r = session.get(url1, params={"cube_symbol": portfolio_id}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            last_rb = (d.get("data") or {}).get("last_rb") or d.get("last_rb") or {}
            rb_id = last_rb.get("id")
            created = last_rb.get("created_at")
            if rb_id:
                return rb_id, created, "current", d
    except Exception as e:
        log.debug(f"current 接口异常: {e}")

    # 方案2：v5 current (stock.xueqiu.com，旧版)
    url2 = f"{STOCK_BASE}/v5/cube/rebalancing/current.json"
    try:
        r2 = session.get(url2, params={"cube_symbol": portfolio_id, "count": 1, "page": 1}, timeout=10)
        if r2.status_code == 200:
            d2 = r2.json()
            last_rb = (d2.get("data") or {}).get("last_rb") or d2.get("last_rb") or {}
            rb_id = last_rb.get("id")
            created = last_rb.get("created_at")
            if rb_id:
                return rb_id, created, "v5/current", d2
    except Exception as e:
        log.debug(f"v5 接口异常: {e}")

    # 方案3：history 接口
    url3 = f"{CUBE_BASE}/rebalancing/history.json"
    try:
        r3 = session.get(url3, params={"cube_symbol": portfolio_id, "count": 1, "page": 1}, timeout=10)
        if r3.status_code == 200:
            d3 = r3.json()
            records = d3.get("list") or []
            if records:
                rb_id = records[0].get("id")
                created = records[0].get("created_at")
                return rb_id, created, "history", d3
    except Exception as e:
        log.debug(f"history 接口异常: {e}")

    # 方案4：snowmart history (备用)
    url4 = f"{BASE_URL}/snowmart/cube/rebalancing/history.json"
    try:
        r4 = session.get(url4, params={"cube_symbol": portfolio_id, "count": 1, "page": 1}, timeout=10)
        if r4.status_code == 200:
            d4 = r4.json()
            records = d4.get("list") or []
            if records:
                rb_id = records[0].get("id")
                created = records[0].get("created_at")
                return rb_id, created, "snowmart/history", d4
        else:
            log.error(f"所有接口均返回错误，最后一个状态码: {r4.status_code}")
    except Exception as e:
        log.error(f"snowmart 接口异常: {e}")

    return None, None, "failed", {}


def fmt_ts(ts_ms):
    if not ts_ms:
        return "未知"
    try:
        return datetime.fromtimestamp(int(ts_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts_ms)


def print_rebalancing_detail(data: dict, source: str):
    """打印调仓详情"""
    print("\n" + "="*55)
    print("  🔔  检测到组合调仓更新！")
    print("="*55)

    if source == "v5/current":
        last_rb = (data.get("data") or {}).get("last_rb") or data.get("last_rb") or {}
        rb_id   = last_rb.get("id")
        created = fmt_ts(last_rb.get("created_at"))
        holdings = last_rb.get("holdings") or []
        print(f"  调仓ID : {rb_id}")
        print(f"  时间   : {created}")
        print(f"  持仓股数: {len(holdings)}")
        print()
        print(f"  {'股票代码':<12}{'股票名称':<10}{'目标权重':>8}  {'前次权重':>8}  {'变动':>6}")
        print("  " + "-"*50)
        for h in holdings:
            w  = float(h.get("weight") or 0)
            pw = float(h.get("prev_weight") or 0)
            delta = w - pw
            sign = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
            print(f"  {h.get('stock_symbol',''):<12}{h.get('stock_name',''):<10}"
                  f"{w:>7.2f}%  {pw:>7.2f}%  {sign}{abs(delta):.2f}%")
    else:
        records = data.get("list") or []
        if not records:
            return
        r = records[0]
        rb_id   = r.get("id")
        created = fmt_ts(r.get("created_at"))
        histories = r.get("rebalancing_histories") or []
        print(f"  调仓ID : {rb_id}")
        print(f"  时间   : {created}")
        print(f"  涉及股数: {len(histories)}")
        print()
        print(f"  {'股票代码':<12}{'股票名称':<10}{'目标权重':>8}  {'前次权重':>8}  {'变动':>6}")
        print("  " + "-"*50)
        for h in histories:
            tw = float(h.get("target_weight") or h.get("weight") or 0)
            pw = float(h.get("prev_weight") or 0)
            delta = tw - pw
            sign = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
            print(f"  {h.get('stock_symbol',''):<12}{h.get('stock_name',''):<10}"
                  f"{tw:>7.2f}%  {pw:>7.2f}%  {sign}{abs(delta):.2f}%")
    print("="*55 + "\n")


def main():
    portfolio_id = PORTFOLIO_ID.strip()
    cookie       = COOKIE.strip()

    # 支持命令行传参：python check_update.py ZH123456 "cookie字符串"
    if len(sys.argv) >= 3:
        portfolio_id = sys.argv[1].strip()
        cookie       = sys.argv[2].strip()
    elif len(sys.argv) == 2:
        portfolio_id = sys.argv[1].strip()

    if not portfolio_id:
        print("❌ 请在脚本顶部填写 PORTFOLIO_ID，或通过命令行传参：")
        print("   python check_update.py ZH123456 \"your_cookie\"")
        sys.exit(1)
    if not cookie:
        print("❌ 请在脚本顶部填写 COOKIE，或通过命令行传参")
        sys.exit(1)

    print(f"\n🔍 开始检测组合 [{portfolio_id}] 调仓更新，持续 {DURATION} 秒 ...")
    session = get_session(cookie)

    # 获取基准 ID
    base_id, base_created, source, data = fetch_latest_rb_id(session, portfolio_id)
    if base_id is None:
        print("❌ 接口请求失败，请检查 Cookie 是否有效或网络是否正常")
        sys.exit(1)

    print(f"✅ 接口正常（{source}）")
    print(f"   当前最新调仓 ID: {base_id}  时间: {fmt_ts(base_created)}")
    print(f"   每 {INTERVAL} 秒检测一次，共 {DURATION} 秒\n")

    start = time.time()
    check_count = 0

    while time.time() - start < DURATION:
        time.sleep(INTERVAL)
        check_count += 1
        elapsed = int(time.time() - start)

        new_id, new_created, new_source, new_data = fetch_latest_rb_id(session, portfolio_id)
        if new_id is None:
            log.warning(f"[{check_count}] 第{elapsed}s 请求失败，继续等待...")
            continue

        if new_id != base_id:
            log.info(f"[{check_count}] 第{elapsed}s ✅ 发现新调仓！ID: {new_id}")
            print_rebalancing_detail(new_data, new_source)
            sys.exit(0)
        else:
            log.info(f"[{check_count}] 第{elapsed}s 无变化，ID仍为 {base_id}")

    print(f"\n⏱ {DURATION} 秒内未检测到调仓更新，组合当前无新调仓。")
    print(f"   最新调仓 ID: {base_id}  时间: {fmt_ts(base_created)}")


if __name__ == "__main__":
    main()
