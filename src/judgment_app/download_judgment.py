import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def main() -> None:
    token = get_token()
    #folder = Path(r"C:\Users\user\Desktop\Judgments")
    #save_all_judgments(token, folder)

def save_all_judgments(token, folder):
    jid_json = get_jid_json(token)
    jid_date = jid_json["date"]
    jid_list = jid_json["list"]
    save_json(folder / f"{jid_date}.json", jid_json)

    folder = folder / jid_date
    folder.mkdir(parents=True, exist_ok=True)

    error_file = 0
    for i, jid in enumerate(jid_list, start=1):
        print(f"[{i}/{len(jid_list)}] {jid}")
        judgment = get_judgment(token, jid)
        if judgment == None:
            error_file += 1
        else:
            save_json(folder / f"{jid}.json", judgment)
    print(f"error: {error_file}件")

def save_json(file_path, saved_json):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            saved_json,
            f,
            ensure_ascii=False,
            indent=4
        )
    
def get_judgment(token, jid):
    url = "https://data.judicial.gov.tw/jdg/api/JDoc"
    data = {
        "token": token,
        "j": jid
    }
    response = requests.post(
        url,
        json=data,
        timeout=30
    )
    print(f"取得完成：{jid}")
    print(f"status code: {response.status_code}")
    response.raise_for_status()
    if response.text[2:7] == "error":
        print("error")
        return None
    return response.json()

def get_jid_json(token):
    url = "https://data.judicial.gov.tw/jdg/api/JList"
    data = {
        "token": token
    }
    response = requests.post(url, json=data)
    return response.json()[0]

def get_token():
    username = os.getenv("JUDICIAL_USERNAME")
    password = os.getenv("JUDICIAL_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "請設定 JUDICIAL_USERNAME 和 JUDICIAL_PASSWORD 環境變數"
        )

    url = "https://data.judicial.gov.tw/jdg/api/Auth"
    data = {
        "user": username,
        "password": password
    }
    response = requests.post(
        url,
        json=data,
        timeout=30
    )
    response.raise_for_status()
    return response.json()["Token"]

if __name__ == "__main__":
    main()

