import json
import os
import time
import requests
from datetime import datetime


# ============================================================
# GLaDOS 多域名
# Cookie 必须来自对应的实际登录域名
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
import os

USER_AGENT = os.getenv("GLADOS_USER_AGENT", "").strip()

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
    raw = os.environ.get("COOKIES", "").strip()

    if not raw:
        print("❌ 没有读取到 COOKIES")
        return []

    # 保留你原来的多账号格式：cookie1&cookie2
    return [x.strip() for x in raw.split("&") if x.strip()]


# ============================================================
# 请求
# ============================================================

def request(domain, cookie, method, path, data=None):

    url = f"https://{domain}{path}"

    headers = {
        "Cookie": cookie,
        "Origin": f"https://{domain}",
        "Referer": f"https://{domain}/console/checkin",
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
    }

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

        # 不打印 Cookie，避免泄露
        if response.text:
            print(
                f"[{domain}] 返回: "
                f"{response.text[:500]}"
            )

        return response

    except requests.RequestException as e:

        print(
            f"[{domain}] 请求失败: {repr(e)}"
        )

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

        if response is None:
            continue

        if response.status_code != 200:
            continue

        try:
            result = response.json()
        except ValueError:
            continue

        data = result.get("data")

        if isinstance(data, dict):

            email = data.get("email")

            if email:

                print(
                    f"✅ Cookie 有效域名: {domain}"
                )
                print(
                    f"   账号: {email}"
                )

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
        print(
            "⚠️ status 返回中没有 data 字段"
        )
        return result

    email = data.get("email", "未知账号")
    left_days = data.get("leftDays", "未知")

    print(
        f"📧 账号: {email}"
    )
    print(
        f"⏳ 剩余: {left_days} 天"
    )

    return result


# ============================================================
# 签到
# ============================================================

def checkin(domain, cookie):

    print(
        f"\n🎫 开始签到: {domain}"
    )

    # 关键：
    # token 必须与当前实际请求域名一致
    payload = {
        "token": domain
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

    message = str(
        result.get("message", "")
    )

    code = result.get("code")

    message_lower = message.lower()

    # 新版 GLaDOS 常见成功/重复签到结果
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
# PushPlus
# ============================================================

def pushplus(title, content):

    token = os.environ.get(
        "PUSHPLUS",
        ""
    ).strip()

    if not token:
        print("ℹ️ 未配置 PUSHPLUS，跳过推送")
        return

    url = "https://www.pushplus.plus/send"

    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": "html",
    }

    try:

        response = requests.post(
            url,
            json=data,
            timeout=30
        )

        print(
            "PushPlus HTTP:",
            response.status_code
        )

        print(
            "PushPlus:",
            response.text[:500]
        )

    except requests.RequestException as e:

        print(
            "❌ PushPlus 请求失败:",
            repr(e)
        )


# ============================================================
# 主程序
# ============================================================

def main():

    print("=" * 60)
    print("🚀 GLaDOS 自动签到")
    print(
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )
    print("=" * 60)

    cookies = get_cookies()

    if not cookies:
        return 1

    success = 0
    fail = 0

    messages = []

    for index, cookie in enumerate(
        cookies,
        start=1
    ):

        print("\n")
        print("=" * 60)
        print(f"👤 处理账号 {index}/{len(cookies)}")
        print("=" * 60)

        # ----------------------------------------------------
        # 1. 找到 Cookie 实际有效域名
        # ----------------------------------------------------

        domain, status_result = (
            find_valid_domain(cookie)
        )

        if not domain:

            fail += 1

            messages.append(
                f"账号 {index}: "
                f"❌ Cookie 无法找到有效域名"
            )

            continue

        # ----------------------------------------------------
        # 2. 签到
        # ----------------------------------------------------

        ok, message = checkin(
            domain,
            cookie
        )

        # ----------------------------------------------------
        # 3. 再获取一次状态
        # ----------------------------------------------------

        status = get_status(
            domain,
            cookie
        )

        email = "未知"

        left_days = "未知"

        if status:

            data = status.get(
                "data",
                {}
            )

            if isinstance(data, dict):

                email = data.get(
                    "email",
                    "未知"
                )

                left_days = data.get(
                    "leftDays",
                    "未知"
                )

        if ok:

            success += 1

            print(
                f"\n✅ 账号 {index} 签到成功"
            )

            messages.append(
                f"""
                <p>
                <b>账号：</b>{email}<br>
                <b>域名：</b>{domain}<br>
                <b>签到：</b>✅ {message}<br>
                <b>剩余：</b>{left_days} 天
                </p>
                """
            )

        else:

            fail += 1

            print(
                f"\n❌ 账号 {index} 签到失败"
            )

            messages.append(
                f"""
                <p>
                <b>账号：</b>{email}<br>
                <b>域名：</b>{domain}<br>
                <b>签到：</b>❌ {message}<br>
                <b>剩余：</b>{left_days} 天
                </p>
                """
            )

    # ========================================================
    # 推送
    # ========================================================

    content = "".join(messages)

    title = (
        f"GLaDOS签到："
        f"成功 {success}，失败 {fail}"
    )

    pushplus(
        title,
        content
    )

    print("\n" + "=" * 60)
    print(
        f"🏁 完成：成功 {success} / "
        f"{len(cookies)}"
    )
    print("=" * 60)

    # 全部成功才让 Actions 为成功
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
