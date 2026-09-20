import requests
import time
import json
import os
import re
from pathlib import Path
from datetime import date
from dotenv import set_key
from judgment_app.exceptions import ApiResponseError
from judgment_app.paths import config_file, jid_json_output_dir, load_settings, user_data_dir


HTTP_TIMEOUT = (5, 30)
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {502, 503, 504}

load_settings()

def main() -> None:
    token = get_token()
    folder = get_judgments_folder()
    #save_all_judgments(token, folder)
    jid_json = get_jid_json(token)
    jid_date = jid_json["date"]
    save_json(folder / f"{jid_date}.json", jid_json)
    #print(get_judgment(token, "TPSM,114,台上,6577,20260416,1"))


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
    error_dict = {}
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
            if "error" in judgment:
                error_dict[jid] = judgment["error"]
                print("  → error key")
                continue
            save_json(file_path, judgment)
            finish_num += 1
            print("  → 完成")
        except ApiResponseError as e:
            print(f"  → API 回應錯誤：{e}")
            except_dict[jid] = {
                "type": type(e).__name__,
                "message": str(e),
            }
        except Exception as e:
            print(f"  → 發生錯誤：{e}")
            cause = e.__cause__
            except_dict[jid] = {
                "type": type(e).__name__,
                "message": str(e),
                "cause": (
                    {
                        "type": type(cause).__name__,
                        "message": str(cause),
                    }
                    if cause is not None
                    else None
                ),
            }

    print(f"total: {len(jid_list)}件")
    print(f"exist: {exist_num}件")
    print(f"not criminal: {not_criminal_num}件")
    print(f"finish: {finish_num}件")
    print(f"error: {len(error_dict)}件")
    print(f"exception: {len(except_dict)}件")

    # 把失敗的 JID 存起來
    if error_dict:
        save_json(
            folder / "error.json",
            error_dict
        )
    if except_dict:
        save_json(
            folder / "except.json",
            except_dict
        )


def get_judgment(token, jid) -> dict:
    url = "https://data.judicial.gov.tw/jdg/api/JDoc"
    payload  = {
        "token": token,
        "j": jid
    }
    data = require_type(
        post_json(url, payload, retry_safe=True), 
        dict, 
        "JDoc",
    )

    actual_jid = require_type(data.get("JID"), str, "JDoc")
    if actual_jid != jid:
        raise ApiResponseError(
            f"JDoc：JID 不一致，預期 {jid}，實際 {actual_jid}"
        )

    required_fields = ("JFULLX", "JID", "JYEAR", "JCASE", "JNO", "JDATE", "JTITLE")
    missing = [key for key in required_fields if key not in data]
    if missing:
        raise ApiResponseError(
            f"JDoc：缺少欄位 {', '.join(missing)}"
        )

    full_x = require_type(data["JFULLX"], dict, "JDoc.JFULLX")
    require_type(full_x.get("JFULLCONTENT"), str, "JDoc.JFULLX.JFULLCONTENT")

    return data


def get_jid_json(token) -> dict:
    url = "https://data.judicial.gov.tw/jdg/api/JList"
    data = post_json(
        url,
        {"token": token},
        retry_safe=True,
    )
    require_type(data, list, "JList")
    batch = require_type(data[0], dict, "JList[0]")
    require_type(batch.get("date"), str, "JList[0].date")
    jids = batch.get("list")
    require_type(jids, list, "JList[0].list", allow_nothing=True)
    for index, jid in enumerate(jids):
        require_type(jid, str, f"JList[0].list[{index}]")
    
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
    # 讓目前程式也能直接使用
    os.environ["API_TOKEN"] = token
    os.environ["API_TOKEN_DATE"] = today
    # 快取失敗不影響本次下載，也不修改 EXE 旁的帳密設定。
    try:
        cache = user_data_dir() / "token.env"
        cache.parent.mkdir(parents=True, exist_ok=True)
        set_key(cache, "API_TOKEN", token)
        set_key(cache, "API_TOKEN_DATE", today)
    except OSError:
        pass
    
    return token


def get_judgments_folder() -> Path:
    folder = jid_json_output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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


def request_new_token() -> str:
    username = os.getenv("JUDICIAL_USERNAME")
    password = os.getenv("JUDICIAL_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            f"請在 {config_file()} 設定 JUDICIAL_USERNAME 和 JUDICIAL_PASSWORD"
        )

    url = "https://data.judicial.gov.tw/jdg/api/Auth"
    payload = {
        "user": username,
        "password": password
    }
    data = require_type(post_json(url, payload), dict, "Auth")

    return require_type(data.get("Token"), str, "Auth.Token")


def post_json(
    url: str,
    payload: dict,
    *,
    retry_safe: bool = False,
) -> dict | list:
    """發送 JSON POST；符合條件時最多嘗試三次。"""
    last_error: Exception | None = None
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
            last_error = exc

        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        ) as exc:
            if not retry_safe:
                raise
            reason = type(exc).__name__
            last_error = exc

        except requests.exceptions.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            if not retry_safe or status not in RETRY_STATUS_CODES:
                raise

            reason = f"HTTP {status}"
            last_error = exc

        # JSONDecodeError 等其他例外不會被上述 except 捕捉。
        # 會直接交給呼叫端處理。

        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(
                f"請求在 {MAX_ATTEMPTS} 次嘗試後仍失敗：{reason}"
            ) from last_error

        delay = 2 ** (attempt - 1)
        print(
            f"  → {reason}，{delay} 秒後重試"
            f"（下一次 {attempt + 1}/{MAX_ATTEMPTS}）"
        )
        time.sleep(delay)

    raise RuntimeError("未預期的請求流程")


def require_type[T](
    data: object,
    expected_type: type[T],
    endpoint: str,
    allow_nothing: bool = False,
) -> T:
    if isinstance(data, dict) and data.get("error") == "目前非本 API 服務時間。":
        raise ApiResponseError(
            data["error"]
        )
    if not isinstance(data, expected_type):
        raise ApiResponseError(
            f"{endpoint}：預期為 {expected_type.__name__}，"
            f"實際為 {type(data).__name__}"
        )
    if not allow_nothing :
        if expected_type is str and not data.strip():
            raise ApiResponseError(
                f"{endpoint}：空白字串"
            )
        elif not data:
            raise ApiResponseError(
                f"{endpoint}：空白"
            )
        
    return data

if __name__ == "__main__":
    main()

