#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无头扫码登录（在 NAS / 服务器上跑）：
打开夸克网盘，把登录二维码截屏保存，轮询等待用户扫码。
二维码图片默认存到 /var/log/quark-refresh/qr.png，
在飞牛「文件管理」里打开这张图，用夸克 App 扫码即可。

退出码：0 已登录；2 超时未扫码。
"""
import logging
import os
import sys
import time

from playwright.sync_api import sync_playwright

PROFILE_DIR = os.environ.get("QUARK_PROFILE_DIR",
                             os.path.expanduser("~/.quark-profile"))
QR_PATH = os.environ.get("QUARK_QR_PATH", "/var/log/quark-refresh/qr.png")
TIMEOUT = int(os.environ.get("QUARK_LOGIN_TIMEOUT", "600"))  # 最多等 10 分钟

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quark-login")


def _page_text(page) -> str:
    try:
        return page.evaluate(
            "document.body ? document.body.innerText.slice(0, 2000) : ''") or ""
    except Exception:  # noqa: BLE001
        return ""


def _cookie_len(page) -> int:
    try:
        return len(page.evaluate("document.cookie") or "")
    except Exception:  # noqa: BLE001
        return 0


def is_logged_in(page) -> bool:
    """严格版：只看页面关键词，用于初始检查。"""
    return any(k in _page_text(page) for k in ("我的文件", "全部文件"))


def poll_logged_in(page, initial_cookie: int) -> bool:
    """轮询版：关键词 + cookie 增长兜底。
    扫码确认后页面可能停在中间态（无关键词），但登录 cookie 已写入，
    这时按 cookie 显著增长判定为已登录。"""
    text = _page_text(page)
    if any(k in text for k in ("我的文件", "全部文件")):
        return True
    if ("扫码登录" not in text and "立即登录" not in text
            and _cookie_len(page) > max(150, initial_cookie + 100)):
        return True
    return False


def main() -> int:
    os.makedirs(os.path.dirname(QR_PATH), exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=["--no-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        try:
            page.goto("https://pan.quark.cn",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)

            if is_logged_in(page):
                log.info("已经是登录状态，无需扫码")
                return 0

            # 尝试点开登录弹窗（宣传页形态）
            for sel in ("text=登录", "text=立即登录", "text=立即体验"):
                try:
                    page.click(sel, timeout=3000)
                    page.wait_for_timeout(3000)
                    break
                except Exception:  # noqa: BLE001
                    continue

            page.wait_for_timeout(3000)
            page.screenshot(path=QR_PATH)
            log.info("请用夸克 App 扫描二维码登录，图片：%s", QR_PATH)
            print(f"QR:{QR_PATH}", flush=True)

            deadline = time.time() + TIMEOUT
            last_shot = 0.0
            last_state = 0.0
            initial_cookie = _cookie_len(page)
            # 注意：不要频繁 reload 页面，否则二维码会话作废；
            # 二维码 20 秒截一次图，保证用户扫到的总是新鲜的。
            while time.time() < deadline:
                page.wait_for_timeout(5000)
                if poll_logged_in(page, initial_cookie):
                    page.wait_for_timeout(2000)  # 二次确认，避免误判
                    if poll_logged_in(page, initial_cookie):
                        log.info("扫码登录成功，登录态已保存")
                        return 0
                    continue
                now = time.time()
                text = _page_text(page)
                expired = any(k in text for k in ("已过期", "已失效", "点击刷新", "重新获取"))
                if expired or now - last_shot > 20:
                    if expired:
                        log.info("二维码已过期，重新加载获取新码")
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                        except Exception:  # noqa: BLE001
                            pass
                        page.wait_for_timeout(3000)
                    try:
                        page.screenshot(path=QR_PATH)
                        last_shot = now
                        # URL 带时间戳，浏览器不会命中缓存，保证看到的是最新截图
                        log.info("二维码已更新，请尽快扫最新的一张：%s?t=%d", QR_PATH, int(now))
                        print(f"QR:{QR_PATH}?t={int(now)}", flush=True)
                    except Exception:  # noqa: BLE001
                        pass
                if now - last_state > 60:
                    last_state = now
                    try:
                        log.info("等待扫码中… url=%s title=%s", page.url, page.title())
                    except Exception:  # noqa: BLE001
                        pass
            log.error("等待扫码超时（%d 秒），请重试", TIMEOUT)
            return 2
        finally:
            ctx.close()


if __name__ == "__main__":
    sys.exit(main())
