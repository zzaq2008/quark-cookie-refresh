#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸克 cookie 自动刷新 → 同步到 OpenList
================================================
每天定时跑一次：
  1. 用已登录的 Chromium profile 打开 pan.quark.cn
  2. 提取最新全量 cookie（含 HttpOnly，用 ctx.cookies() 而非 document.cookie）
  3. 调 OpenList API，把夸克存储的 cookie 换成新的

退出码：
  0  成功（或 cookie 无变化）
  2  夸克掉登录，需要人工重新扫码
  3  OpenList 侧失败（连不上 / 找不到存储 / 更新失败）

全部配置走环境变量，见 README.md。
"""

import json
import logging
import os
import sys

import requests
from playwright.sync_api import sync_playwright

OPENLIST_URL = os.environ.get("OPENLIST_URL", "http://192.168.1.100:5244").rstrip("/")
OPENLIST_USER = os.environ.get("OPENLIST_USER", "admin")
OPENLIST_PASS = os.environ.get("OPENLIST_PASS", "")
QUARK_MOUNT_PATH = os.environ.get("QUARK_MOUNT_PATH", "/夸克网盘")  # OpenList 里夸克存储的挂载路径
PROFILE_DIR = os.environ.get("QUARK_PROFILE_DIR", os.path.expanduser("~/.quark-profile"))
QUARK_HOME = "https://pan.quark.cn"
SHOT_PATH = os.environ.get("QUARK_SHOT_PATH", "/tmp/quark-login-check.png")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quark-refresh")


def fetch_cookie():
    """返回最新 cookie 字符串；掉登录返回 None。"""
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = ctx.new_page()
        try:
            page.goto(QUARK_HOME, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(10000)
            text = page.evaluate("document.body ? document.body.innerText.slice(0, 2000) : ''")

            logged_in = any(k in text for k in ("我的文件", "全部文件"))
            logged_out = "扫码登录" in text or "立即登录" in text

            if logged_out or not logged_in:
                page.screenshot(path=SHOT_PATH)
                log.error("夸克登录态失效（截图：%s），需人工重新扫码登录", SHOT_PATH)
                return None

            # document.cookie 拿不到 HttpOnly 的关键凭证（如 __pus），OpenList 会认作游客；
            # 改用 ctx.cookies() 取全量（含 HttpOnly），只保留 quark 域
            all_cookies = ctx.cookies()
            quark_cookies = [c for c in all_cookies if "quark" in c.get("domain", "")]
            prio = {"__pus": 0, "__puus": 1, "__uid": 2}  # 主凭证排前面
            quark_cookies.sort(key=lambda c: (prio.get(c["name"], 9), c["name"]))
            names = [c["name"] for c in quark_cookies]
            log.info("cookie 字段：%s", ",".join(names))
            cookie = "; ".join(f"{c['name']}={c['value']}" for c in quark_cookies)
            if len(cookie) < 100:
                page.screenshot(path=SHOT_PATH)
                log.error("取到的 cookie 过短（%d 字符），可能没真正登录", len(cookie))
                return None
            if "__pus" not in names:
                log.warning("cookie 缺少 __pus 主凭证，OpenList 可能认作游客")
            log.info("取到最新 cookie，长度 %d", len(cookie))
            return cookie
        finally:
            ctx.close()


def ol_api(method, path, token=None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = token  # Alist/OpenList 用裸 token，不用 Bearer
    url = OPENLIST_URL + path
    r = requests.request(method, url, headers=headers,
                         timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 200:
        raise RuntimeError(f"OpenList API {path} 异常：{data.get('message')}")
    return data["data"]


def update_one_storage(token, target, cookie):
    """更新单个存储的 cookie；无变化返回 False，更新成功返回 True。"""
    storage = ol_api("GET", "/api/admin/storage/get", token,
                     params={"id": target["id"]})
    addition = json.loads(storage.get("addition") or "{}")
    if addition.get("cookie") == cookie:
        log.info("存储 %s 的 cookie 已是最新，无需更新",
                 target.get("mount_path"))
        return False
    addition["cookie"] = cookie
    storage["addition"] = json.dumps(addition, ensure_ascii=False)
    ol_api("POST", "/api/admin/storage/update", token, json=storage)
    log.info("OpenList 夸克存储 cookie 已更新（id=%s mount=%s driver=%s）",
             target["id"], target.get("mount_path"), target.get("driver"))
    return True


def update_openlist(cookie):
    if not OPENLIST_PASS:
        raise RuntimeError("未设置 OPENLIST_PASS 环境变量")
    token = ol_api("POST", "/api/auth/login",
                   json={"username": OPENLIST_USER,
                         "password": OPENLIST_PASS})["token"]

    items = ol_api("GET", "/api/admin/storage/list", token,
                   params={"page": 1, "per_page": 200})["content"]
    # 优先按驱动类型识别夸克存储（挂载路径用户可能随便起名，如 /kk）；
    # 找不到再回退到按挂载路径匹配。可能有多个，都更新。
    targets = [it for it in items
               if "quark" in str(it.get("driver", "")).lower()]
    if not targets:
        targets = [it for it in items
                   if it.get("mount_path") == QUARK_MOUNT_PATH]
    if not targets:
        avail = [(it.get("mount_path"), it.get("driver")) for it in items]
        raise RuntimeError(
            f"找不到夸克存储（驱动名含 quark），也找不到挂载路径为 "
            f"{QUARK_MOUNT_PATH} 的存储；现有：{avail}")

    log.info("找到 %d 个夸克存储：%s", len(targets),
             [(t.get("mount_path"), t.get("driver")) for t in targets])
    failed = []
    for target in targets:
        try:
            update_one_storage(token, target, cookie)
        except Exception as e:  # noqa: BLE001
            log.error("存储 %s 更新失败：%s", target.get("mount_path"), e)
            failed.append(target.get("mount_path"))
    if failed:
        raise RuntimeError(f"{len(failed)} 个夸克存储更新失败：{failed}")


def main():
    cookie = fetch_cookie()
    if cookie is None:
        return 2
    try:
        update_openlist(cookie)
    except Exception as e:  # noqa: BLE001
        log.exception("更新 OpenList 失败：%s", e)
        return 3
    log.info("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
