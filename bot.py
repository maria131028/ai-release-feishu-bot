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


def write_bitable(token: str, model: str, change_type: str, summary: str, official_text: str, my_note: str):
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
            "我的判断": my_note
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


def fetch_official_excerpt(url: str, max_chars: int = 1800) -> str:
    """
    稳定抓取文章正文原文
    - OpenAI /index/ 类页面：优先解析 Next.js 的 __NEXT_DATA__ JSON 抽文本
    - 其他页面：trafilatura 抽取，失败再 soup 兜底
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AIRSSBot/2.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }

    try:
        r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        html = r.text
    except Exception:
        return ""

    # ---------- OpenAI Next.js /index/ 特殊处理：从 __NEXT_DATA__ 抽文本 ----------
    if "openai.com/index/" in (r.url or url):
        try:
            soup = BeautifulSoup(html, "lxml")
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                import json
                data = json.loads(script.string)

                # 递归收集字符串
                texts = []
                def walk(x):
                    if x is None:
                        return
                    if isinstance(x, str):
                        s = x.strip()
                        if s:
                            texts.append(s)
                        return
                    if isinstance(x, list):
                        for i in x:
                            walk(i)
                        return
                    if isinstance(x, dict):
                        for v in x.values():
                            walk(v)

                # 通常正文在 props/pageProps 里，直接从根走也行
                walk(data.get("props") or data)

                # 过滤：去掉明显像键/短词的噪声，保留较长句子
                cleaned = []
                seen = set()
                for t in texts:
                    t2 = " ".join(t.split())
                    if len(t2) < 30:
                        continue
                    if t2 in seen:
                        continue
                    seen.add(t2)
                    cleaned.append(t2)
                    if sum(len(x) for x in cleaned) > max_chars * 2:
                        break

                if cleaned:
                    out = "\n\n".join(cleaned)
                    if len(out) > max_chars:
                        out = out[:max_chars].rstrip() + "…"
                    return out
        except Exception:
            # 特殊处理失败则继续走通用路径
            pass

    # ---------- 通用路径：trafilatura ----------
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            include_links=False,
            favor_precision=True
        )
        if text:
            text = text.strip()
            if len(text) > max_chars:
                text = text[:max_chars].rstrip() + "…"
            return text
    except Exception:
        pass

    # ---------- 通用兜底：BeautifulSoup ----------
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside", "form"]):
        tag.decompose()

    container = soup.find("article") or soup



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
                my_note = "" if official_text else f"official_text empty: {link}"
                new_items.append((title, link, model, change_type, official_text, summary, my_note))

    # 有新内容才推送 & 写表
    for title, link, model, change_type, official_text, summary, my_note in new_items:
        ...
        write_bitable(
            token=token,
            model=model,
            change_type=change_type,
            summary=title,
            official_text=official_text,
            my_note=my_note
        )

    state["seen"] = list(seen)[-3000:]
    save_state(state)


if __name__ == "__main__":
    main()
