import os
import json
import time
import hmac
import base64
import hashlib
import requests
import feedparser
from typing import Dict, Any, List, Tuple, Optional

# =========================
# 基础配置
# =========================

# 飞书群机器人 Webhook（用于群推送）
FEISHU_WEBHOOK = os.environ["FEISHU_WEBHOOK"]
FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "").strip()  # 未开启签名可不填

# 飞书应用（用于调用 Bitable OpenAPI）
FEISHU_APP_ID = os.environ["FEISHU_APP_ID"]
FEISHU_APP_SECRET = os.environ["FEISHU_APP_SECRET"]

# 你的 Bitable app_token（你已验证 EEHcb... 可用）
BITABLE_BASE_ID = os.environ.get("BITABLE_BASE_ID", "EEHcbK8VYaYO2Vsja9eczYLBn5j").strip()
BITABLE_TABLE_ID = os.environ.get("BITABLE_TABLE_ID", "tblblt8COU2FNveR").strip()

STATE_FILE = "state.json"

# 调试开关（建议先开着，跑通后再关）
DEBUG_BITABLE = os.environ.get("DEBUG_BITABLE", "1").strip() == "1"
DEBUG_WRITE_TEST = os.environ.get("DEBUG_WRITE_TEST", "1").strip() == "1"  # 强制写表联调用
DEBUG_PRINT_FIELDS = os.environ.get("DEBUG_PRINT_FIELDS", "1").strip() == "1"

# RSS 源
FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://rsshub.app/openai/chatgpt/release-notes",
    "https://rsshub.app/openai/research",
    "https://rsshub.app/anthropic/news",
    "https://blog.google/feed/",
]

# 关键词
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
    print("FEISHU_WEBHOOK status:", r.status_code)
    print("FEISHU_WEBHOOK resp:", r.text)
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


def feishu_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# =========================
# 调试：获取 app 元数据 / 表字段
# =========================

def debug_get_bitable_app_meta(token: str, app_token: str):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    print("=== DEBUG: GET_APP_META ===")
    print("url:", url)
    print("status:", r.status_code)
    print("resp:", r.text)
    r.raise_for_status()


def list_tables(token: str, app_token: str) -> List[Dict[str, Any]]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables?page_size=50"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    print("=== DEBUG: LIST_TABLES ===")
    print("url:", url)
    print("status:", r.status_code)
    print("resp:", r.text)
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("items", []) or []


def list_fields(token: str, app_token: str, table_id: str) -> List[Dict[str, Any]]:
    # 字段列表接口：用于判断哪些列是公式/只读，哪些可写
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields?page_size=200"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    print("=== DEBUG: LIST_FIELDS ===")
    print("url:", url)
    print("status:", r.status_code)
    print("resp:", r.text)
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("items", []) or []


def print_fields_brief(fields: List[Dict[str, Any]]):
    # 不同租户/版本字段结构可能略有差异，这里只做“尽量打印”
    print("=== DEBUG: FIELDS_BRIEF ===")
    for f in fields:
        name = f.get("field_name") or f.get("name") or ""
        ftype = f.get("type") or f.get("field_type") or ""
        fid = f.get("field_id") or f.get("id") or ""
        # is_primary / is_computed 等字段不保证存在
        flags = []
        for k in ["is_primary", "is_computed", "is_lookup", "is_rollup", "is_formula", "readonly"]:
            if k in f:
                flags.append(f"{k}={f.get(k)}")
        flags_s = (" " + " ".join(flags)) if flags else ""
        print(f"- {name} | type={ftype} | id={fid}{flags_s}")


# =========================
# 写入 Bitable（新增记录）
# =========================

def write_record_raw(token: str, fields_payload: Dict[str, Any]) -> requests.Response:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BITABLE_BASE_ID}/tables/{BITABLE_TABLE_ID}/records"
    r = requests.post(url, headers=feishu_headers(token), json={"fields": fields_payload}, timeout=20)
    print("=== DEBUG: WRITE_RECORD ===")
    print("url:", url)
    print("payload:", json.dumps({"fields": fields_payload}, ensure_ascii=False))
    print("status:", r.status_code)
    print("resp:", r.text)
    return r


def write_bitable(token: str, model: str, change_type: str, summary: str) -> None:
    """
    两段式写入：
    - 先尝试写入你希望的 4 个字段
    - 如果失败，再降级只写入 1 个字段（快速判断“字段类型/只读”问题）
    """
    full_fields = {
        "模型": model,
        "类型": change_type,
        "官方一句话": summary,
        "我的判断": ""
    }

    r = write_record_raw(token, full_fields)
    if r.ok:
        return

    # 降级：只写一个字段（选一个你最可能是“普通文本”的列）
    # 你表里“我的判断”看起来更可能是普通文本列，优先用它
    fallback_fields = {
        "我的判断": f"[写入降级测试] {summary}"
    }
    r2 = write_record_raw(token, fallback_fields)
    if r2.ok:
        print("=== DEBUG: WRITE_FALLBACK_OK ===")
        print("Full fields failed, but fallback succeeded => 说明字段类型/只读/字段名问题。")
        return

    # 两次都失败，抛出更明确异常
    r.raise_for_status()


def hit_keywords(title: str, summary: str) -> bool:
    t = (title or "").lower()
    s = (summary or "").lower()
    return any(k.lower() in t or k.lower() in s for k in KEYWORDS)


# =========================
# 主逻辑
# =========================

def main():
    token = get_tenant_token()

    # 0) Debug: app meta + tables + fields
    if DEBUG_BITABLE:
        debug_get_bitable_app_meta(token, BITABLE_BASE_ID)

        try:
            tables = list_tables(token, BITABLE_BASE_ID)
            if tables:
                print("=== DEBUG: TABLES_BRIEF ===")
                for tb in tables:
                    print(f"- name={tb.get('name')} table_id={tb.get('table_id')}")
        except Exception as e:
            print("=== DEBUG: LIST_TABLES_FAILED ===")
            print(e)

        if DEBUG_PRINT_FIELDS:
            try:
                fields = list_fields(token, BITABLE_BASE_ID, BITABLE_TABLE_ID)
                print_fields_brief(fields)
            except Exception as e:
                print("=== DEBUG: LIST_FIELDS_FAILED ===")
                print(e)

    # 1) 心跳
    post_feishu("✅心跳：Actions 已运行（本次会尝试写表联调）。")

    # 2) 强制写表联调（建议先开着，确保链路通）
    if DEBUG_WRITE_TEST:
        try:
            write_bitable(token=token, model="SYSTEM", change_type="心跳", summary="Actions write test")
            post_feishu("✅写表联调：已调用 write_bitable（请到多维表格查看是否新增记录 / 或看日志里的 WRITE_RECORD）")
        except Exception as e:
            post_feishu(f"❌写表联调失败：{e}（请看 Actions 日志里的 WRITE_RECORD resp 具体报错）")
            raise

    # 3) 正常 RSS 扫描（命中才推送 + 写表）
    state = load_state()
    seen = set(state.get("seen", []))
    new_items: List[Tuple[str, str]] = []

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
            write_bitable(token=token, model="GPT", change_type="大版本", summary=title)

    state["seen"] = list(seen)[-2000:]
    save_state(state)


if __name__ == "__main__":
    main()
