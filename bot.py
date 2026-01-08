import os, json, time, hmac, base64, hashlib
import requests
import feedparser

FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

BITABLE_BASE_ID = "EEHcbK8VYaYO2Vsja9eczYLBn5j"
BITABLE_TABLE_ID = "tblblt8COU2FNveR"


FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET  = os.environ.get("FEISHU_SECRET")  # 若没启用签名校验可不填

FEEDS = [
    # OpenAI：官方 News RSS（目前官方提供）
    "https://openai.com/news/rss.xml",

    # OpenAI：ChatGPT Release Notes（RSSHub 提供的可用路线）
    "https://rsshub.app/openai/chatgpt/release-notes",

    # OpenAI：Research（RSSHub 提供的可用路线）
    "https://rsshub.app/openai/research",

    # Anthropic：Newsroom（RSSHub 路线）
    "https://rsshub.app/anthropic/news",

    # Google：Google Blog 全站 Feed（包含 AI 分类；用关键词过滤即可）
    "https://blog.google/feed/",
]


KEYWORDS = [
    "release", "update", "model", "launch", "gpt", "gemini", "claude", "llama",
    "重大", "发布", "更新", "版本"
]

STATE_FILE = "state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def sign(timestamp: str, secret: str) -> str:
    # 飞书自定义机器人签名：base64(hmac_sha256(timestamp + "\n" + secret))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")

def post_feishu(text: str):
    payload = {
        "msg_type": "text",
        "content": {"text": text}
    }

    # 如果启用签名校验，需要在 URL 上带 timestamp & sign
    if FEISHU_SECRET:
        ts = str(int(time.time()))
        s = sign(ts, FEISHU_SECRET)
        url = f"{FEISHU_WEBHOOK}?timestamp={ts}&sign={requests.utils.quote(s)}"
    else:
        url = FEISHU_WEBHOOK

    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()

def get_tenant_token():
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()["tenant_access_token"]

def write_bitable(token, model, change_type, summary):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_BASE_ID}/tables/{BITABLE_TABLE_ID}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    data = {
        "fields": {
            "时间": int(time.time() * 1000),
            "模型": model,
            "类型": change_type,
            "官方一句话": summary,
            "我的判断": ""
        }
    }
    r = requests.post(url, headers=headers, json=data, timeout=20)
    r.raise_for_status()


def hit_keywords(title: str, summary: str) -> bool:
    t = (title or "").lower()
    s = (summary or "").lower()
    return any(k in t or k in s for k in KEYWORDS)

def main():
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
            if hit_keywords(title, summary):
                new_items.append((title, link))
            seen.add(link)

    # 只推送命中的，避免噪声
token = get_tenant_token()

for title, link in new_items[:10]:
    post_feishu(f"【AI重大更新】{title}\n{link}")
    write_bitable(
        token=token,
        model="GPT",              # 先写死，后面可优化
        change_type="大版本",     # 先写死
        summary=title
    )

    state["seen"] = list(seen)[-2000:]  # 保留最近2000条避免state过大
    save_state(state)

if __name__ == "__main__":
    main()
