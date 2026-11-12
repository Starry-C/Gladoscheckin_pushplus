import hashlib
import json
import math
import os
import re
import time
import requests
from datetime import datetime


# ============================================================
# GLaDOS 多域名
# ============================================================

DOMAINS = [
    "glados.cloud",
    "glados.network",
    "glados.rocks",
    "glados.one",
    "glados.space",
    "glados.vip",
    "glados-facility.com",
]

USER_AGENT = os.getenv("GLADOS_USER_AGENT", "").strip()

print(f"当前使用的 User-Agent: {USER_AGENT}")

if not USER_AGENT:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


# ============================================================
# Cookie
# ============================================================

def get_cookies():
    raw = (
        os.environ.get("GLADOS_COOKIE", "").strip()
        or os.environ.get("GLADOS_COOKIES", "").strip()
        or os.environ.get("COOKIES", "").strip()
    )

    if not raw:
        print("❌ 没有读取到 Cookie")
        return []

    return [raw]


# ============================================================
# 请求
# ============================================================
def get_browser_headers():
    ua = os.getenv("GLADOS_USER_AGENT", "").strip()
    match = re.search(r"(?:Chrome|Chromium)/(\d+)", ua)
    if not match:
        return {}

    version = match.group(1)

    if "Macintosh" in ua:
        platform = "macOS"
    elif "Windows" in ua:
        platform = "Windows"
    elif "Android" in ua:
        platform = "Android"
    elif "Linux" in ua:
        platform = "Linux"
    else:
        platform = "Unknown"

    mobile = "?1" if "Android" in ua else "?0"

    return {
        "Sec-CH-UA": (
            f'"Chromium";v="{version}", '
            f'"Google Chrome";v="{version}", '
            '"Not_A Brand";v="99"'
        ),
        "Sec-CH-UA-Mobile": mobile,
        "Sec-CH-UA-Platform": f'"{platform}"',
    }

def request(domain, cookie, method, path, data=None, log_response=True):

    url = f"https://{domain}{path}"

    headers = {
        "Cookie": cookie,
        "Origin": f"https://{domain}",
        "Referer": f"https://{domain}/console/checkin",
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
    }
    headers.update(get_browser_headers())
    headers.update({
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })

    try:
        if method == "POST":
            response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=30
            )
        else:
            response = requests.get(
                url,
                headers=headers,
                timeout=30
            )

        print(
            f"[{domain}] {method} {path} "
            f"HTTP {response.status_code}"
        )

        # 不打印 Cookie，也不打印 challenge 中的一次性 nonce
        if log_response and response.text:
            print(
                f"[{domain}] 返回: "
                f"{response.text[:500]}"
            )

        return response

    except requests.RequestException as e:
        print(f"[{domain}] 请求失败: {type(e).__name__}")
        return None


# ============================================================
# 检测 Cookie 实际对应哪个域名
# ============================================================

def find_valid_domain(cookie):

    print("\n🔍 开始检测 Cookie 对应的 GLaDOS 域名...")

    for domain in DOMAINS:

        response = request(
            domain,
            cookie,
            "GET",
            "/api/user/status"
        )

        if response is None or response.status_code != 200:
            continue

        try:
            result = response.json()
        except ValueError:
            continue

        data = result.get("data")

        if isinstance(data, dict):

            email = data.get("email")

            if email:
                print(f"✅ Cookie 有效域名: {domain}")
                print(f"   账号: {email}")
                return domain, result

        print(
            f"⚠️ {domain} 返回了 JSON，"
            f"但没有有效账号信息"
        )

    print("❌ 没有找到 Cookie 对应的有效域名")

    return None, None


# ============================================================
# 获取账户状态
# ============================================================

def get_status(domain, cookie):

    response = request(
        domain,
        cookie,
        "GET",
        "/api/user/status"
    )

    if response is None:
        return None

    try:
        result = response.json()
    except ValueError:
        print("❌ status 返回的不是 JSON")
        return None

    data = result.get("data")

    if not isinstance(data, dict):
        print("⚠️ status 返回中没有 data 字段")
        return result

    email = data.get("email", "未知账号")
    left_days = data.get("leftDays", "未知")

    print(f"📧 账号: {email}")
    print(f"⏳ 剩余: {left_days} 天")

    return result


# ============================================================
# 签到：先获取 challenge，再提交验证
# ============================================================

def checkin(domain, cookie):

    print(f"\n🎫 开始签到: {domain}")

    # 第一步：获取一次性 challenge
    challenge_response = request(
        domain,
        cookie,
        "GET",
        "/api/user/checkin/challenge",
        log_response=False
    )

    if challenge_response is None:
        return False, "获取签到验证失败：网络请求失败"

    try:
        challenge = challenge_response.json()
    except ValueError:
        return False, "获取签到验证失败：服务器返回非 JSON"

    if not isinstance(challenge, dict):
        return False, "获取签到验证失败：响应格式异常"

    nonce = challenge.get("nonce")
    ttl = challenge.get("ttl", 300000)

    if (
        challenge.get("code") != 0
        or not isinstance(nonce, str)
        or not 0 < len(nonce) <= 4096
        or type(ttl) not in (int, float)
        or not math.isfinite(ttl)
        or ttl <= 2700
    ):
        return False, "获取签到验证失败：未取得有效 challenge，本次未提交签到"

    # 第二步：计算验证 proof
    received_at = time.monotonic()

    proof = hashlib.sha256(
        (nonce + ".glados-observe-v1").encode("utf-8")
    ).hexdigest()

    # 第三步：等待验证要求的时间
    time.sleep(2.7)

    if (time.monotonic() - received_at) * 1000 >= ttl:
        return False, "签到验证已过期，本次未提交签到"

    # 第四步：提交签到
    payload = {
        "token": domain,
        "nonce": nonce,
        "proof": proof,
    }

    response = request(
        domain,
        cookie,
        "POST",
        "/api/user/checkin",
        payload
    )

    if response is None:
        return False, "网络请求失败"

    try:
        result = response.json()
    except ValueError:
        return False, (
            f"服务器返回非 JSON: "
            f"{response.text[:200]}"
        )

    message = str(result.get("message", ""))
    code = result.get("code")
    message_lower = message.lower()

    if (
        code == 0
        or "checkin" in message_lower
        or "repeat" in message_lower
        or "today's observation" in message_lower
    ):

        if (
            "repeat" in message_lower
            or "tomorrow" in message_lower
        ):
            return True, "今日已签到"

        return True, message or "签到成功"

    return False, message or f"签到失败，code={code}"


# ============================================================
# 方糖（Server酱）推送
# ============================================================

def serverchan(title, content):
    import re
    import html

    sendkey = os.environ.get(
        "SERVERCHAN_SENDKEY",
        ""
    ).strip()

    print(
        "方糖密钥读取状态：",
        "已读取" if sendkey else "未读取"
    )

    if not sendkey:
        print("ℹ️ 未配置 SERVERCHAN_SENDKEY，跳过推送")
        return

    content = re.sub(
        r"<br\s*/?>", "\n", content, flags=re.IGNORECASE
    )
    content = re.sub(
        r"</p\s*>", "\n\n", content, flags=re.IGNORECASE
    )
    content = re.sub(
        r"<[^>]+>", "", content
    )
    content = html.unescape(content).strip()

    if sendkey.startswith("sctp"):
        match = re.match(r"^sctp(\d+)t", sendkey)

        if not match:
            print("❌ Server酱 SendKey 格式不正确")
            return

        uid = match.group(1)
        url = f"https://{uid}.push.ft07.com/send/{sendkey}.send"
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"

    data = {
        "title": title,
        "desp": content,
    }

    try:
        response = requests.post(
            url,
            data=data,
            timeout=30
        )

        print("方糖 HTTP:", response.status_code)

        try:
            result = response.json()

            print(
                "方糖推送结果:",
                result.get("message", result.get("code", "未知"))
            )

            if response.ok and str(result.get("code")) == "0":
                print("✅ 方糖推送成功")
            else:
                print("❌ 方糖推送未确认成功，请检查返回结果")

        except ValueError:
            print("❌ 方糖返回的不是有效 JSON")

    except requests.RequestException as e:
        print("❌ 方糖请求失败:", type(e).__name__)


# ============================================================
# 主程序：自动签到并推送结果
# ============================================================

def main():

    if os.environ.get("CHECK_COOKIE_ONLY", "").strip() == "1":
        raw = (
            os.environ.get("GLADOS_COOKIE", "").strip()
            or os.environ.get("GLADOS_COOKIES", "").strip()
            or os.environ.get("COOKIES", "").strip()
        )

        if not raw:
            print("❌ Cookie Secret 未注入（变量为空）")
            return

        names = []
        for item in raw.split(";"):
            item = item.strip()
            if "=" in item:
                name = item.split("=", 1)[0].strip()
                if name and name not in names:
                    names.append(name)

        print("✅ Cookie Secret 已注入")
        print(f"Cookie 字符数：{len(raw)}")
        print("检测到的 Cookie 名称：" + ", ".join(names))
        print("🔒 本次没有访问 GLaDOS，也没有执行签到")
        return

    if os.environ.get("CHECK_STATUS_ONLY", "").strip() == "1":
        cookies = get_cookies()
        if not cookies:
            return

        print("\n🔎 只读验证登录状态：仅请求 /api/user/status")
        for index, cookie in enumerate(cookies, start=1):
            print(f"\n========== 账号 {index} ==========")
            domain, status = find_valid_domain(cookie)
            if domain and status:
                data = status.get("data", {})
                print("✅ 登录状态验证通过")
                print(f"识别域名：{domain}")
                print(f"剩余天数：{data.get('leftDays', '未知')}")
                response = request(
                    domain,
                    cookie,
                    "GET",
                    "/api/user/points",
                    log_response=False
                )

                if response is not None and response.status_code == 200:
                    try:
                        result = response.json()
                        if isinstance(result, dict) and "points" in result:
                            print(f"当前总积分：{result.get('points')}")
                            history = result.get("history", [])
                            if isinstance(history, list) and history:
                                print(f"最近一笔积分变动：{history[0].get('change', '未知')}")
                            else:
                                print("没有可读取的积分流水")
                        else:
                            print("积分接口未返回 points 字段")
                    except ValueError:
                        print("积分接口返回格式异常")
                else:
                    print("积分接口请求失败")
            else:
                print("❌ 未能通过账户状态验证")
        print("\n🔒 只读验证结束：没有获取 challenge，没有提交签到")
        return

    cookies = get_cookies()

    if not cookies:
        return

    success = 0
    fail = 0
    messages = []

    for index, cookie in enumerate(cookies, start=1):

        print(f"\n========== 账号 {index} ==========")

        domain, status = find_valid_domain(cookie)

        if not domain:
            fail += 1
            messages.append(
                f"账号 {index}：未找到有效登录域名，签到失败。"
            )
            continue

        data = status.get("data", {})
        email = data.get("email", f"账号 {index}")
        left_days = data.get("leftDays", "未知")

        ok, message = checkin(domain, cookie)

        if ok:
            success += 1
            messages.append(
                f"账号：{email}\n"
                f"域名：{domain}\n"
                f"签到：成功（{message}）\n"
                f"剩余：{left_days} 天"
            )
        else:
            fail += 1
            messages.append(
                f"账号：{email}\n"
                f"域名：{domain}\n"
                f"签到：失败（{message}）"
            )

    title = f"GLaDOS签到：成功 {success}，失败 {fail}"
    content = "\n\n".join(messages)

    print("\n========== 签到汇总 ==========")
    print(content)

    serverchan(title, content)


if __name__ == "__main__":
    main()
