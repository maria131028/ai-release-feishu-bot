import os
import json
import time
import hmac
import base64
import hashlib
import requests
import feedparser
from typing import Dict, List, Tuple, Optional
from bs4 import BeautifulSoup

# =========================
# 基础配置（环境变量）
# =========================

FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "").strip()

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

# Bitable
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

def write_bitable(
    token: str,
    model: str,
    change_type: str,
    summary: str,
    official_text: str,
):
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/"
        f"{BITABLE_BASE_ID}/tables/{BITABLE_TABLE_ID}/records"
    )

    payload = {
        "fields": {
            "模型": model,
            "类型": change_type,
            "官方一句话": summary,
            "官方原文": official_text,
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
    t = (title or "").lower()
    if "gpt" in t or "openai" in t or "chatgpt" in t:
        return "GPT"
    if "claude" in t or "anthropic" in t:
        return "Claude"
    if "gemini" in t or "google" in t:
        return "Gemini"
    if "llama" in t or "meta" in t:
        return "LLaMA"
    return "AI"


def classify_type(title: str, summary: str = "") -> str:
    """
    根据标题/摘要做一个稳定的规则分类，输出必须是你飞书下拉框里的中文选项之一
    """
    t = f"{title} {summary}".lower()

    # 1 模型发布
    if any(k in t for k in ["introducing", "announcing", "launch", "released", "release"]):
        return "模型发布"

    # 6 安全与政策
    if any(k in t for k in ["system card", "safety", "policy", "red team", "addendum"]):
        return "安全与政策"

    # 4 API与开发者更新
    if any(k in t for k in ["api", "developer", "developers", "sdk", "rate limit"]):
        return "API与开发者更新"

    # 5 价格与配额
    if any(k in t for k in ["pricing", "price", "cost", "billing", "quota", "usage limits"]):
        return "价格与配额"

    # 7 基准与评测
    if any(k in t for k in ["benchmark", "evaluation", "eval", "mmlu", "gpqa"]):
        return "基准与评测"

    # 8 研究发布
    if any(k in t for k in ["research", "paper", "technical report"]):
        return "研究发布"

    # 3 产品更新
    if any(k in t for k in ["chatgpt", "product", "app", "ui", "experience"]):
        return "产品更新"

    # 9 生态与合作
    if any(k in t for k in ["partner", "partnership", "collaboration", "collaborate"]):
        return "生态与合作"

    # 10 故障与状态
    if any(k in t for k in ["outage", "incident", "status", "degraded"]):
        return "故障与状态"

    # 2 模型能力升级（兜底）
    return "模型能力升级"


def _clean_text(s: str) -> str:
    s = (s or "").replace("\r", "\n")
    lines = [ln.strip() for ln in s.split("\n")]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def fetch_official_excerpt(url: str, max_chars: int = 1200, max_paragraphs: int = 6) -> str:
    """
    抓取文章正文的前若干段作为“官方原文”（不是摘要、不是改写）
    - 优先取 <article>，其次 <main>，最后 body
    - 只拼接 <p> 段落
    - 截断到 max_chars
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AIRSSBot/2.0; +https://github.com/)",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }

    try:
        r = requests.get(url, headers=headers, timeout=25)
        r.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(r.text, "lxml")

    # 去掉明显噪声
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    paragraphs = []
    for p in container.find_all("p"):
        txt = _clean_text(p.get_text(" ", strip=True))
        if not txt:
            continue
        # 过滤极短噪声段
        if len(txt) < 20:
            continue
        paragraphs.append(txt)
        if len(paragraphs) >= max_paragraphs:
            break

    text = "\n\n".join(paragraphs).strip()
    if not text:
        return ""

    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"

    return text


# =========================
# 主逻辑
# =========================

def main():
    token = get_tenant_token()

    state = load_state()
    seen = set(state.get("seen", []))

    # (title, link, model, change_type, official_text, summary)
    new_items: List[Tuple[str, str, str, str, str, str]] = []

    for feed_url in FEEDS:
        d = feedparser.parse(feed_url)
        for e in d.entries[:20]:
            link = getattr(e, "link", None)
            title = getattr(e, "title", "") or ""
            summary = getattr(e, "summary", "") or ""

            if not link or link in seen:
                continue

            seen.add(link)

            if hit_keywords(title, summary):
                model = guess_model(title)
                change_type = classify_type(title, summary)
                official_text = fetch_official_excerpt(link)

                new_items.append((title, link, model, change_type, official_text, summary))

    # 有新内容才推送 & 写表
    for title, link, model, change_type, official_text, summary in new_items:
        post_feishu(f"【AI 更新】{title}\n类型：{change_type}\n{link}")
        write_bitable(
            token=token,
            model=model,
            change_type=change_type,
            summary=title,              # 你表里的“官方一句话”目前就放标题（稳定、可靠）
            official_text=official_text # 正文摘录写入“官方原文”
        )

    state["seen"] = list(seen)[-3000:]
    save_state(state)


if __name__ == "__main__":
    main()
