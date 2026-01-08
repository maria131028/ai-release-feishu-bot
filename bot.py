import os
import json
import time
import hmac
import base64
import hashlib
import requests
import feedparser
from typing import Dict, Any, List, Tuple

# =========================
# 基础配置（环境变量）
# =========================

FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "").strip()

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

# Bitable（你已验证可用）
BITABLE_BASE_ID = os.environ.get("BITABLE_BASE_ID", "EEHcbK8VYaYO2Vsja9eczYLBn5j").strip()
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "tblblt8COU2FNveR").strip()

STATE_FILE = "state.json"

# =========================
# RSS 源 & 关键词
# =========================

FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://rsshub.app/openai/chatgpt/release-notes",
    "https://rsshub.app/openai/research",
    "https://rsshub.app/anthropic/news",
    "https://blog.google/feed/",
]

KEYWORDS = [
    "release", "update", "model", "launch", "gpt", "gemini",
    "claude", "llama", "重大", "发布", "更新", "版本"
]

# =========================
# state.json 读写
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
# 飞书群机器人（Webhook）
# =========================

def sign(timestamp: str, secret: str) -> str:
    raw = f"{timestamp}\n{secret}"
    h = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest()
    return base64.b64encode(h).decode()


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
# 飞书 OpenAPI
# =========================

def get_tenant_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["tenant_access_token"]


def feishu_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# =========================
# 写入 Bitable（新增记录）
# =========================

def write_bitable(token: str, model: str, change_type: str, summary: str):
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{BITABLE_BASE_ID}/tables/{BITABLE_TABLE_ID}/records"
    )

    payload = {
        "fields": {
            "模型": model,
            "类型": change_type,
            "官方一句话": summary,
            "我的判断": ""
        }
    }

    r = requests.post(url, headers=feishu_headers(token), json=payload, timeout=20)
    r.raise_for_status()


# =========================
# 工具
# =========================

def hit_keywords(title: str, summary: str) -> bool:
    t = (title or "").lower()
    s = (summary or "").lower()
    return any(k.lower() in t or k.lower() in s for k in KEYWORDS)


def guess_model(title: str) -> str:
    t = title.lower()
    if "gpt" in t or "openai" in t:
        return "GPT"
    if "claude" in t or "anthropic" in t:
        return "Claude"
    if "gemini" in t or "google" in t:
        return "Gemini"
    if "llama" in t or "meta" in t:
        return "LLaMA"
    return "AI"


# =========================
# 主逻辑
# =========================

def main():
    token = get_tenant_token()

    state = load_state()
    seen = set(state.get("seen", []))
    new_items: List[Tuple[str, str, str]] = []

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
                model = guess_model(title)
                new_items.append((title, link, model))

    # 有新内容才推送 & 写表
    for title, link, model in new_items:
        post_feishu(f"【AI 更新】{title}\n{link}")
        write_bitable(
            token=token,
            model=model,
            change_type="更新",
            summary=title
        )

    state["seen"] = list(seen)[-3000:]
    save_state(state)


if __name__ == "__main__":
    main()
