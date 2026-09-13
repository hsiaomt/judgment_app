import requests
import time
import json
import os
import re
from pathlib import Path
from datetime import date
from dotenv import load_dotenv, set_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

HTTP_TIMEOUT = (5, 30)
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {502, 503, 504}

load_dotenv(ENV_FILE)

def main() -> None:
    token = get_token()
    folder = get_judgments_folder()
    save_all_judgments(token, folder)

def save_all_judgments(token, folder) -> None:
    jid_json = get_jid_json(token)
    jid_date = jid_json["date"]
    jid_list = jid_json["list"]
    save_json(folder / f"{jid_date}.json", jid_json)

    folder = folder / jid_date
    folder.mkdir(parents=True, exist_ok=True)

    exist_num = 0
    not_criminal_num = 0
    finish_num = 0
    error_list = []
    except_dict = {}
    for i, jid in enumerate(jid_list, start=1):
        file_path = folder / f"{jid}.json"
        print(f"[{i}/{len(jid_list)}] {jid}")
        if file_path.exists():
            exist_num += 1
            print("  → 已存在，跳過")
            continue
        if not re.match(r"^...M", jid):
            not_criminal_num += 1
            print("  → 非刑事，跳過")
            continue

        try:
            judgment = get_judgment(token, jid)
            if judgment is None:
                print("  → error key")
                error_list.append(jid)
                continue
            save_json(file_path, judgment)
            finish_num += 1
            print("  → 完成")
        except Exception as e:
            print(f"  → 發生錯誤：{e}")
            except_dict[jid] = type(e).__name__

    print(f"total: {len(jid_list)}件")
    print(f"exist: {exist_num}件")
    print(f"not criminal: {not_criminal_num}件")
    print(f"finish: {finish_num}件")
    print(f"error: {len(error_list)}件")
    print(f"exception: {len(except_dict)}件")

    # 把失敗的 JID 存起來
    if error_list:
        save_json(
            folder / "error.json",
            error_list
        )
    if except_dict:
        save_json(
            folder / "except.json",
            except_dict
        )

def save_json(file_path, saved_json) -> None:
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(
            saved_json,
            f,
            ensure_ascii=False,
            indent=4
        )
    tmp_path.replace(file_path)
    
def get_judgment(token, jid) -> dict | None:
    url = "https://data.judicial.gov.tw/jdg/api/JDoc"
    payload  = {
        "token": token,
        "j": jid
    }
    data = post_json(url, payload, retry_safe=True)

    if not isinstance(data, dict):
        raise ValueError("JDoc 回應格式錯誤：預期為物件")
    if "error" in data:
        return None
    
    print(f"取得完成：{jid}")
    return data

def get_jid_json(token) -> dict:
    url = "https://data.judicial.gov.tw/jdg/api/JList"
    data = post_json(
        url,
        {"token": token},
        retry_safe=True,
    )

    if not isinstance(data, list) or not data:
        raise ValueError("JList 回應格式錯誤或清單為空")
    if not isinstance(data[0], dict):
        raise ValueError("JList 第一筆資料不是物件")
    
    return data[0]

def get_token() -> str:
    today = date.today().isoformat()
    token = os.getenv("API_TOKEN")
    token_date = os.getenv("API_TOKEN_DATE")
    # 今天已經取得過
    if token and token_date == today:
        return token
    # 今天還沒取得 → 重新取得
    token = request_new_token()
    # 更新 .env
    set_key(ENV_FILE, "API_TOKEN", token)
    set_key(ENV_FILE, "API_TOKEN_DATE", today)
    # 讓目前程式也能直接使用
    os.environ["API_TOKEN"] = token
    os.environ["API_TOKEN_DATE"] = today
    
    return token

def request_new_token() -> str:
    username = os.getenv("JUDICIAL_USERNAME")
    password = os.getenv("JUDICIAL_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "請在 .env 設定 JUDICIAL_USERNAME 和 JUDICIAL_PASSWORD"
        )

    url = "https://data.judicial.gov.tw/jdg/api/Auth"
    data = {
        "user": username,
        "password": password
    }
    result = post_json(url, data)
    token = result.get("Token")

    if not isinstance(token, str) or not token:
        raise ValueError("Auth 回應缺少有效 Token")

    return token

def post_json(
    url: str,
    payload: dict,
    *,
    retry_safe: bool = False,
) -> dict | list:
    """發送 JSON POST；符合條件時最多嘗試三次。"""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with requests.post(
                url,
                json=payload,
                timeout=HTTP_TIMEOUT,
            ) as response:
                response.raise_for_status()
                return response.json()

        except requests.exceptions.SSLError:
            # 憑證問題通常無法透過重試解決。
            raise

        except requests.exceptions.ConnectTimeout as exc:
            # 尚未成功建立連線，可以重試。
            reason = type(exc).__name__

        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if not retry_safe:
                raise
            reason = type(exc).__name__

        except requests.exceptions.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            if not retry_safe or status not in RETRY_STATUS_CODES:
                raise

            reason = f"HTTP {status}"

        # JSONDecodeError 等其他例外不會被上述 except 捕捉。
        # 會直接交給呼叫端處理。

        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                f"請求在 {MAX_ATTEMPTS} 次嘗試後仍失敗：{reason}"
            )

        delay = 2 ** (attempt - 1)
        print(
            f"  → {reason}，{delay} 秒後重試"
            f"（下一次 {attempt + 1}/{MAX_ATTEMPTS}）"
        )
        time.sleep(delay)

    raise RuntimeError("未預期的請求流程")

def get_judgments_folder() -> Path:
    value = os.getenv("JUDGMENTS_DIR")
    if not value:
        raise RuntimeError("請在 .env 設定 JUDGMENTS_DIR")

    folder = Path(value).expanduser()
    if not folder.is_absolute():
        folder = PROJECT_ROOT / folder

    folder.mkdir(parents=True, exist_ok=True)
    return folder

if __name__ == "__main__":
    main()

