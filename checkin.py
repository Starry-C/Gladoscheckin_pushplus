# -*- coding: utf-8 -*-
"""
GLaDOS 自动签到
核心规则：
1. 签到前先查询 /api/user/points 的 history。
2. 若今天已有 business=system:checkin，直接标记“已签到”，不提交签到。
3. 若无法可靠读取签到流水，停止本次操作，避免误重复。
4. 只有确认没有今天的签到记录，才获取 challenge 并提交一次。
5. POST 不做自动重试，避免网络超时后重复提交。
"""

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DOMAINS = [
    "glados.cloud",
    "glados.network",
    "glados.rocks",
    "glados.one",
    "glados.space",
    "glados.vip",
    "glados-facility.com",
]

LOCAL_TZ = ZoneInfo("Asia/Taipei")
CHALLENGE_SUFFIX = "glados-observe-v1"
CHALLENGE_DELAY_SECONDS = 2.7

USER_AGENT = os.getenv("GLADOS_USER_AGENT", "").strip()
if not USER_AGENT:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    print("⚠️ 未配置 GLADOS_USER_AGENT，使用默认 UA；建议配置生成 Cookie 时所用浏览器的完整 UA。")


def get_cookies():
    raw = (
        os.environ.get("GLADOS_COOKIE", "").strip()
        or os.environ.get("GLADOS_COOKIES", "").strip()
        or os.environ.get("COOKIES", "").strip()
    )
    if not raw:
        print("❌ 没有读取到 Cookie Secret")
        return []
    # 兼容原仓库的单账号 Cookie 格式
    return [raw]


def build_session():
    session = requests.Session()
    # 只对 GET 做有限重试；POST 签到绝不自动重试
    retry = Retry(
        total=2,
        connect=2,
        read=1,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


def client_hints(ua):
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
    elif "iPhone" in ua or "iPad" in ua:
        platform = "iOS"
    elif "Linux" in ua:
        platform = "Linux"
    else:
        platform = "Unknown"
    mobile = "?1" if ("Android" in ua or "iPhone" in ua) else "?0"
    return {
        "Sec-CH-UA": (
            f'"Chromium";v="{version}", '
            f'"Google Chrome";v="{version}", '
            '"Not_A Brand";v="99"'
        ),
        "Sec-CH-UA-Mobile": mobile,
        "Sec-CH-UA-Platform": f'"{platform}"',
    }


def headers(domain, ua=None):
    current_ua = (ua or USER_AGENT).strip()
    result = {
        "Cookie": COOKIE_FOR_REQUEST,
        "Origin": f"https://{domain}",
        "User-Agent": current_ua,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    result.update(client_hints(current_ua))
    # 不人为添加 Referer；不伪造 IP、TLS 指纹或 VPN 出口
    return result


COOKIE_FOR_REQUEST = ""


def request(domain, method, path, data=None, ua=None, timeout=25):
    url = f"https://{domain}{path}"
    try:
        if method == "GET":
            response = SESSION.get(
                url, headers=headers(domain, ua), timeout=timeout
            )
        elif method == "POST":
            # 重要：签到 POST 不自动重试
            response = requests.post(
                url, headers=headers(domain, ua), json=data, timeout=timeout
            )
        else:
            raise ValueError("不支持的 HTTP 方法")
        print(f"[{domain}] {method} {path} -> HTTP {response.status_code}")
        return response
    except requests.RequestException as exc:
        print(f"[{domain}] 请求异常：{type(exc).__name__}")
        return None


def parse_local_date(value):
    """兼容 Unix 秒/毫秒时间戳、ISO 时间和常见日期字符串。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 10**12:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(LOCAL_TZ).date()
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{10,13}", text):
            number = float(text)
            if len(text) >= 13:
                number /= 1000.0
            return datetime.fromtimestamp(number, tz=timezone.utc).astimezone(LOCAL_TZ).date()
        normalized = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=LOCAL_TZ)
            else:
                parsed = parsed.astimezone(LOCAL_TZ)
            return parsed.date()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return None


def get_json(response, label):
    if response is None:
        print(f"❌ {label}：网络请求无响应")
        return None
    try:
        data = response.json()
    except ValueError:
        print(f"❌ {label}：服务器返回非 JSON")
        return None
    if not isinstance(data, dict):
        print(f"❌ {label}：响应格式异常")
        return None
    return data


def find_valid_domain(cookie):
    print("\n🔍 检查账号登录状态和所属域名")
    for domain in DOMAINS:
        response = request(domain, "GET", "/api/user/status")
        result = get_json(response, "账户状态查询")
        if not result:
            continue
        data = result.get("data")
        if result.get("code") == 0 and isinstance(data, dict) and data.get("email"):
            print(f"✅ 登录状态有效，识别域名：{domain}")
            print(f"会员剩余：{data.get('leftDays', '未知')} 天")
            return domain, result
    return None, None


def inspect_today_checkin(domain):
    """
    返回：
      ("already", detail) = 流水明确显示今天已签到
      ("not_checked", detail) = 流水可读取，未发现今天签到
      ("unknown", detail) = 无法判断，必须停止，不能提交签到
    """
    response = request(domain, "GET", "/api/user/points")
    result = get_json(response, "签到前积分流水查询")
    if result is None or response.status_code != 200:
        return "unknown", "积分流水接口不可用"

    if result.get("code") not in (0, None):
        return "unknown", f"积分流水接口返回 code={result.get('code')}"

    history = result.get("history")
    if not isinstance(history, list) or not history:
        return "unknown", "积分流水为空或格式异常，无法排除今天已经签到"

    today = datetime.now(LOCAL_TZ).date()
    checkin_entries = []
    unparsed_checkin_entries = []

    for item in history:
        if not isinstance(item, dict):
            continue
        business = str(item.get("business", "")).strip().lower()
        if business not in ("system:checkin", "system_checkin", "checkin"):
            continue
        entry_date = parse_local_date(
            item.get("time", item.get("createdAt", item.get("created_at", item.get("timestamp"))))
        )
        if entry_date is None:
            unparsed_checkin_entries.append(item)
        elif entry_date == today:
            checkin_entries.append(item)

    if checkin_entries:
        latest = checkin_entries[0]
        return "already", (
            f"积分流水已存在今天的签到记录（{latest.get('time', '时间未知')}），"
            "不再提交签到"
        )

    if unparsed_checkin_entries:
        return "unknown", "发现签到流水但无法识别日期，停止以避免重复签到"

    # 对外返回的 history 列表已逐条检查；未发现今天的签到记录才允许进入签到流程。
    return "not_checked", f"已检查 {len(history)} 条积分流水，未发现今天的签到记录"


def get_status(domain):
    response = request(domain, "GET", "/api/user/status")
    result = get_json(response, "账户状态查询")
    if result and isinstance(result.get("data"), dict):
        return result["data"]
    return None


PLATFORM_UA = {
    "Windows": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "macOS": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
    "Linux": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "iOS": (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "iPad": (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "iPhone": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Android": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
}


def submit_once(domain, ua):
    challenge_response = request(
        domain, "GET", "/api/user/checkin/challenge", ua=ua
    )
    challenge = get_json(challenge_response, "获取 challenge")
    if challenge is None:
        return None, "获取 challenge 失败，本次未提交签到"

    nonce = challenge.get("nonce")
    ttl = challenge.get("ttl", 300000)
    if (
        challenge.get("code") != 0
        or not isinstance(nonce, str)
        or not nonce
        or len(nonce) > 4096
        or type(ttl) not in (int, float)
        or ttl <= CHALLENGE_DELAY_SECONDS * 1000
    ):
        return None, "服务器未返回有效 challenge，本次未提交签到"

    received_at = time.monotonic()
    proof = hashlib.sha256(
        f"{nonce}.{CHALLENGE_SUFFIX}".encode("utf-8")
    ).hexdigest()

    time.sleep(CHALLENGE_DELAY_SECONDS)
    if (time.monotonic() - received_at) * 1000 >= ttl:
        return None, "challenge 已过期，本次未提交签到"

    payload = {"token": domain, "nonce": nonce, "proof": proof}
    response = request(
        domain, "POST", "/api/user/checkin", data=payload, ua=ua, timeout=30
    )
    result = get_json(response, "签到提交")
    if result is None:
        return None, "签到提交后未收到可确认的响应；请核对官网，不自动重试"
    return result, None


def do_checkin(domain):
    print("\n🎫 开始执行 challenge/proof 签到")
    result, error = submit_once(domain, USER_AGENT)
    if error:
        return "failed", error

    if result.get("code") == 4 and result.get("reason") == "device-mismatch":
        device = result.get("loginDevice")
        retry_ua = PLATFORM_UA.get(device)
        if not retry_ua:
            return "failed", f"设备不匹配，服务器返回的平台无法识别：{device}"
        print(f"⚠️ 服务端明确要求设备平台为 {device}，重新获取 challenge 后重试一次")
        result, error = submit_once(domain, retry_ua)
        if error:
            return "failed", error

    code = result.get("code")
    repeated = result.get("repeated") is True
    message = str(result.get("message", "")).strip()
    points = result.get("points")

    if code == 1 or repeated:
        return "already", "服务器确认今日已签到，不计为失败"

    if code == 0:
        if points is None:
            return "success", message or "服务器确认签到成功；积分数额未在响应中提供"
        try:
            n = float(points)
            if n <= 1:
                return "success", f"签到成功，服务器实际返回 {points} 积分（奖励偏低，不能靠重复提交补签）"
        except (TypeError, ValueError):
            pass
        return "success", f"签到成功，获得 {points} 积分"

    return "failed", message or f"服务器返回 code={code}"


def serverchan(title, content):
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if not sendkey:
        print("ℹ️ 未配置 SERVERCHAN_SENDKEY，跳过推送")
        return

    if sendkey.startswith("sctp"):
        match = re.match(r"^sctp(\d+)t", sendkey)
        if not match:
            print("❌ Server酱 SendKey 格式不正确")
            return
        url = f"https://{match.group(1)}.push.ft07.com/send/{sendkey}.send"
    else:
        url = f"https://sctapi.ftqq.com/{sendkey}.send"

    try:
        response = requests.post(
            url, data={"title": title, "desp": content}, timeout=20
        )
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.ok and str(data.get("code")) == "0":
            print("✅ Server酱推送成功")
        else:
            print("⚠️ Server酱未确认推送成功")
    except requests.RequestException as exc:
        print(f"⚠️ Server酱推送请求异常：{type(exc).__name__}")


def read_only_status(cookie):
    domain, status = find_valid_domain(cookie)
    if not domain:
        print("❌ 只读检查：未能验证登录状态")
        return
    response = request(domain, "GET", "/api/user/points")
    result = get_json(response, "积分流水只读查询")
    if not result:
        return
    print(f"当前总积分：{result.get('points', '未知')}")
    history = result.get("history")
    if isinstance(history, list):
        print(f"本次接口返回流水条数：{len(history)}")
        for item in history[:20]:
            if isinstance(item, dict):
                print(
                    "流水：",
                    "business=", item.get("business", "未知"),
                    "| change=", item.get("change", "未知"),
                    "| time=", item.get("time", "未知"),
                )
    print("🔒 只读检查结束：没有获取 challenge，也没有提交签到")


def main():
    global COOKIE_FOR_REQUEST

    cookies = get_cookies()
    if not cookies:
        return 1
    COOKIE_FOR_REQUEST = cookies[0]

    if os.environ.get("CHECK_COOKIE_ONLY", "").strip() == "1":
        print("✅ Cookie Secret 已注入")
        print(f"Cookie 字符数：{len(COOKIE_FOR_REQUEST)}")
        print("🔒 本次未访问 GLaDOS，未执行签到")
        return 0

    if os.environ.get("CHECK_STATUS_ONLY", "").strip() == "1":
        read_only_status(COOKIE_FOR_REQUEST)
        return 0

    domain, status = find_valid_domain(COOKIE_FOR_REQUEST)
    if not domain:
        title = "GLaDOS签到：登录状态异常"
        content = "未能验证 Cookie 登录状态，本次没有提交签到。请检查登录状态。"
        print(content)
        serverchan(title, content)
        return 1

    # 第一步：签到前查重
    state, detail = inspect_today_checkin(domain)
    print(f"\n🔎 今日签到预检查：{detail}")

    if state == "already":
        title = "GLaDOS签到：已签到"
        content = f"今日已签到。\n{detail}\n本次未获取 challenge，未提交签到请求。"
        print(f"\n✅ {content}")
        serverchan(title, content)
        return 0

    if state == "unknown":
        title = "GLaDOS签到：状态无法确认"
        content = (
            f"无法确认今天是否已经签到：{detail}\n"
            "为避免重复签到，本次没有提交签到请求。"
        )
        print(f"\n⚠️ {content}")
        serverchan(title, content)
        return 1

    # 第二步：只有查重明确未发现今日签到后才提交
    print("✅ 预检查未发现今日签到记录，进入签到流程")
    state, detail = do_checkin(domain)

    if state == "already":
        title = "GLaDOS签到：已签到"
        content = f"今日已签到。\n{detail}"
        print(f"\n✅ {content}")
        serverchan(title, content)
        return 0

    if state == "success":
        title = "GLaDOS签到：成功"
        content = f"签到成功。\n{detail}"
        print(f"\n✅ {content}")
        serverchan(title, content)
        return 0

    title = "GLaDOS签到：需要检查"
    content = f"本次签到未能确认成功。\n{detail}"
    print(f"\n⚠️ {content}")
    serverchan(title, content)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
