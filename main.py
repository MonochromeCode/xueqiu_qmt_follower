"""
main.py
────────────────────────────────────────────────────────────
程序入口：配置日志，启动跟踪器

运行方法：
    python main.py

注意：
    1. 请先在 config.py 中填写 QMT 路径、账号和雪球 Cookie
    2. 必须在 miniQMT 客户端已登录的情况下运行
    3. 需要在交易时段（09:30~14:55）内运行才会下单
────────────────────────────────────────────────────────────
"""

import os
import sys
import logging
import datetime

import config
from follower import XueqiuFollower


def setup_logging():
    """配置日志：同时输出到控制台和日志文件（每天零点自动滚动，保留 30 天）"""
    from logging.handlers import TimedRotatingFileHandler

    os.makedirs(config.LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.LOG_DIR, "follower.log")

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 文件 handler：每天零点滚动，历史文件名 follower.log.YYYY-MM-DD，保留 30 天
    fh = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.info(f"日志已初始化，文件: {log_path}（每天零点自动滚动）")


def check_config():
    """启动前基本配置检查"""
    errors = []
    if "ZH123456" in config.PORTFOLIO_ID:
        errors.append("  · PORTFOLIO_ID 未修改（当前为示例值 ZH123456）")
    if "1234567890" in config.ACCOUNT_ID:
        errors.append("  · ACCOUNT_ID 未修改（当前为示例值 1234567890）")
    if "xxxxxx" in config.XUEQIU_COOKIE:
        errors.append("  · XUEQIU_COOKIE 未填写（当前为示例值）")
    if not os.path.isdir(config.QMT_PATH):
        errors.append(f"  · QMT_PATH 目录不存在: {config.QMT_PATH}")

    if errors:
        print("\n" + "=" * 60)
        print("❌  配置检查未通过，请修改 config.py：")
        for e in errors:
            print(e)
        print("=" * 60 + "\n")
        return False
    return True


def main():
    setup_logging()

    # 若配置有误，仍允许继续（可能是测试模拟模式）
    if not check_config():
        answer = input("配置存在问题，是否仍以【模拟模式】继续运行？(y/N): ").strip().lower()
        if answer != "y":
            sys.exit(0)
        logging.warning("以模拟模式继续运行（不会实际下单）")

    follower = XueqiuFollower()
    follower.start()


if __name__ == "__main__":
    main()
