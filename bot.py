def debug_list_bitable_apps(token: str):
    url = "https://open.feishu.cn/open-apis/bitable/v1/apps?page_size=50"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=20)
    print("LIST_APPS status:", r.status_code)
    print("LIST_APPS resp:", r.text)
    r.raise_for_status()

def debug_list_tables(token: str, app_token: str):
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables?page_size=50"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=20)
    print("LIST_TABLES status:", r.status_code)
    print("LIST_TABLES resp:", r.text)
    r.raise_for_status()
