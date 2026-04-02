"""
test_api.py
────────────────────────────────────────────────────────────
测试雪球组合调仓接口，找出可用的最新接口

用法：
  1. 在下方填写你的 COOKIE 和 PORTFOLIO_ID
  2. python test_api.py
────────────────────────────────────────────────────────────
"""

import requests
import json
import sys

# ─── 配置区（修改这里）────────────────────────────────────
PORTFOLIO_ID = ""          # ← 填写雪球组合代码，如 ZH123456
COOKIE       = ""          # ← 填写你的雪球 Cookie
# ──────────────────────────────────────────────────────────

if not PORTFOLIO_ID or not COOKIE:
    print("❌ 请在脚本顶部填写 PORTFOLIO_ID 和 COOKIE")
    sys.exit(1)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://xueqiu.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cookie": COOKIE,
}

def test_api(name, url, params=None):
    """测试单个接口"""
    print(f"\n{'='*60}")
    print(f"测试接口: {name}")
    print(f"URL: {url}")
    if params:
        print(f"参数: {params}")
    print("-"*60)
    
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        # 先访问主页初始化 session
        s.get("https://xueqiu.com", timeout=10)
        
        r = s.get(url, params=params, timeout=15)
        print(f"状态码: {r.status_code}")
        
        if r.status_code == 200:
            try:
                data = r.json()
                print(f"✅ 接口正常返回 JSON")
                
                # 检查是否有数据
                if "data" in data or "list" in data:
                    print(f"数据结构预览:")
                    preview = json.dumps(data, ensure_ascii=False, indent=2)[:500]
                    print(preview + "..." if len(str(data)) > 500 else preview)
                    return True, data
                else:
                    print(f"⚠️ 返回 JSON 但可能无有效数据")
                    print(json.dumps(data, ensure_ascii=False, indent=2)[:500])
                    return False, data
            except Exception as e:
                print(f"⚠️ 返回 200 但解析 JSON 失败: {e}")
                print(f"原始内容: {r.text[:300]}")
                return False, None
        else:
            print(f"❌ 接口返回错误: {r.status_code}")
            print(f"响应内容: {r.text[:300]}")
            return False, None
            
    except Exception as e:
        print(f"❌ 请求异常: {e}")
        return False, None


def main():
    portfolio_id = PORTFOLIO_ID.strip().upper()
    
    print(f"\n🔍 开始测试组合 [{portfolio_id}] 的调仓接口...")
    print(f"Cookie 长度: {len(COOKIE)} 字符")
    
    results = []
    
    # 接口1: v5 current (旧版，可能已废弃)
    url1 = "https://stock.xueqiu.com/v5/cube/rebalancing/current.json"
    ok1, data1 = test_api("v5/current (stock.xueqiu.com)", url1, {"cube_symbol": portfolio_id, "count": 1, "page": 1})
    results.append(("v5/current", ok1, data1))
    
    # 接口2: xueqiu.com cubes current (新版)
    url2 = "https://xueqiu.com/cubes/rebalancing/current.json"
    ok2, data2 = test_api("cubes/current (xueqiu.com)", url2, {"cube_symbol": portfolio_id})
    results.append(("cubes/current", ok2, data2))
    
    # 接口3: snowmart current (pysnowball使用的)
    url3 = "https://xueqiu.com/snowmart/cube/rebalancing/show/current.json"
    ok3, data3 = test_api("snowmart/current", url3, {"cube_symbol": portfolio_id})
    results.append(("snowmart/current", ok3, data3))
    
    # 接口4: history (调仓历史)
    url4 = "https://xueqiu.com/cubes/rebalancing/history.json"
    ok4, data4 = test_api("cubes/history", url4, {"cube_symbol": portfolio_id, "count": 1, "page": 1})
    results.append(("cubes/history", ok4, data4))
    
    # 接口5: snowmart history
    url5 = "https://xueqiu.com/snowmart/cube/rebalancing/history.json"
    ok5, data5 = test_api("snowmart/history", url5, {"cube_symbol": portfolio_id, "count": 1, "page": 1})
    results.append(("snowmart/history", ok5, data5))
    
    # 汇总
    print(f"\n{'='*60}")
    print("📊 测试结果汇总")
    print("="*60)
    for name, ok, data in results:
        status = "✅ 可用" if ok else "❌ 失败"
        print(f"  {name:<25} {status}")
    
    # 找出最佳接口
    working = [(n, d) for n, ok, d in results if ok]
    if working:
        print(f"\n✅ 推荐使用的接口:")
        for name, data in working:
            print(f"   - {name}")
    else:
        print(f"\n❌ 所有接口均不可用，请检查 Cookie 是否有效")


if __name__ == "__main__":
    main()
