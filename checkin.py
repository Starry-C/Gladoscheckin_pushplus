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
# 方糖（Server酱）推送
# ============================================================

def serverchan(title, content):
    import re
    import html

    sendkey = os.environ.get(
        "SERVERCHAN_SENDKEY",
        ""
    ).strip()

    if not sendkey:
        print("ℹ️ 未配置 SERVERCHAN_SENDKEY，跳过推送")
        return

    # Server酱正文支持 Markdown，将原来的 HTML 转成纯文本
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

    # 兼容 Server酱 Turbo（SCT）和 Server酱³（SC3）
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
        print("❌ 方糖请求失败:", repr(e))


# ============================================================
# 主程序：自动签到并推送结果
# ============================================================

def main():
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
                f"账号：{email}\n域名：{domain}\n签到：成功（{message}）\n剩余：{left_days} 天"
            )
        else:
            fail += 1
            messages.append(
                f"账号：{email}\n域名：{domain}\n签到：失败（{message}）"
            )

    title = f"GLaDOS签到：成功 {success}，失败 {fail}"
    content = "\n\n".join(messages)

    print("\n========== 签到汇总 ==========")
    print(content)

    serverchan(title, content)


if __name__ == "__main__":
    main()

