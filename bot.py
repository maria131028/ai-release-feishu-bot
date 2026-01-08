import os
import json
import time
import hmac
import base64
import hashlib
import requests
import feedparser

# =========================
# 基础配置
# =========================

# 飞书群机器人 Webhook（用于群推送）
FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET = os.environ.get("FEISHU_SECRET")  # 未开启签名可不填

# 飞书应用（用于调用 Bitable OpenAPI）
FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

# 你现在从 URL 里提取的（但很可能不是 OpenAPI 的 app_token）
# 先保留，调试模式会打印真正应使用的 bascn... app_token
BITABLE_BASE_ID = "EEHcbK8VYaYO2Vsja9eczYLBn5j"
BITABLE_TABLE_ID = "tblblt8COU2FNveR"

# 调试开关：先保持为 1，用来打印 apps/tables 列表
DEBUG_BITABLE = True  # 拿到 bascn... 后我们再改成 False

STATE_FILE = "state.json"

FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://rsshub.app/openai/chatgpt/release-notes",
    "https://rsshub.app/openai/research",
    "https://rsshub.app/anthropic/news",
    "https://blog.google/feed/",
]

KEYWORDS = [
    "release", "update", "model", "launch", "gpt", "gemini", "claude", "llama",
    "重大", "发布", "更新", "版本"
]


# =========================
# 工具函数：state.json
# =========================

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# =========================
# 飞书群推送（Webhook）
# =========================

def sign(timestamp: str, secret: str) -> str:
    # base64(hmac_sha256(timestamp + "\n" + secret))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def post_feishu(text: str):
    payload = {"msg_type": "text", "content": {"text": text}}

    if FEISHU_SECRET:
        ts = str(int(time.time()))
        s = sign(ts, FEISHU_SECRET)
        url = f"{FEISHU_WEBHOOK}?timestamp={ts}&sign={requests.utils.quote(s)}"
    else:
        url = FEISHU_WEBHOOK

    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()


# =========================
# 飞书 OpenAPI：tenant token
# =========================

def get_tenant_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["tenant_access_token"]


# =========================
# 调试：列出你有权限的 Bitable apps 和 tables
# =========================

def debug_list_bitable_apps(token: str):
    url = "https://open.feishu.cn/open-apis/bitable/v1/apps?page_size=50"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=20)
    print("=== DEBUG: LIST_APPS ===")
    print("status:", r.status_code)
    print("resp:", r.text)
    r.raise_for_status()


def debug_list_tables(token: str, app_token: str):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables?page_size=50"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=20)
    print(f"=== DEBUG: LIST_TABLES for app_token={app_token} ===")
    print("status:", r.status_code)
    print("resp:", r.text)
    r.raise_for_status()


# =========================
# 写入 Bitable（新增记录）
# =========================

def write_bitable(token: str, model: str, change_type: str, summary: str):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_BASE_ID}/tables/{BITABLE_TABLE_ID}/records"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 说明：
    # 1) 你表里有「时间」日期字段，但我们先不写时间，避免字段格式导致 400 干扰定位
    # 2) 先确保写入最少字段成功，后面再加回时间
    data = {
        "fields": {
            "模型": model,
            "类型": change_type,
            "官方一句话": summary,
            "我的判断": ""
        }
    }

    r = requests.post(url, headers=headers, json=data, timeout=20)
    if not r.ok:
        print("=== DEBUG: WRITE_BITABLE FAILED ===")
        print("status:", r.status_code)
        print("resp:", r.text)
    r.raise_for_status()


def hit_keywords(title: str, summary: str) -> bool:
    t = (title or "").lower()
    s = (summary or "").lower()
    return any(k in t or k in s for k in KEYWORDS)


# =========================
# 主逻辑
# =========================

def main():
    token = get_tenant_token()

    # 1) 先跑调试：把你有权限的 app_token 列出来（通常是 bascn...）
    if DEBUG_BITABLE:
        debug_list_bitable_apps(token)
        # 如果你已经知道正确的 bascn...，也可以把它填在这里再跑一次列 tables：
        # debug_list_tables(token, "bascnxxxxxxxxxxxxxxxx")

    # 2) 心跳：证明“推送 OK”
    post_feishu("✅心跳：Actions 已运行。若表仍写不进，请看 Actions 日志中的 LIST_APPS 输出，找到 bascn... 的 app_token。")

    # 3) 正常 RSS 扫描（命中才推送+写表）
    state = load_state()
    seen = set(state.get("seen", []))
    new_items = []

    for feed_url in FEEDS:
        d = feedparser.parse(feed_url)
        for e in d.entries[:20]:
            link = getattr(e, "link", None)
            title = getattr(e, "title", "")
            summary = getattr(e, "summary", "")

            if not link or link in seen:
                continue

            seen.add(link)

            if hit_keywords(title, summary):
                new_items.append((title, link))

    if new_items:
        for title, link in new_items[:10]:
            post_feishu(f"【AI重大更新】{title}\n{link}")
            # 这里先写入表（如果 BITABLE_BASE_ID 还没换成 bascn...，这里大概率仍会 400）
            write_bitable(token=token, model="GPT", change_type="大版本", summary=title)

    state["seen"] = list(seen)[-2000:]
    save_state(state)


if __name__ == "__main__":
    main()
