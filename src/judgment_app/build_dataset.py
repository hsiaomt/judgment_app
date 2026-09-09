import json
import re
from pathlib import Path

import pandas as pd

RAW_DIR = Path("data/raw")
OUTPUT_FILE = Path("data/dataset.csv")

def extract_court_judgment(text: str) -> str:
    """只保留法院判決正文，移除後面的起訴書附件。"""

    marker = "附件："

    if marker in text:
        text = text.split(marker, 1)[0]

    return text.strip()

def extract_judgment(json_file: Path) -> dict:
    """從司法院 JSON 取得基本資料與裁判書全文。"""

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    content = data["JFULLX"]["JFULLCONTENT"]
    # 只保留法院判決正文
    content = extract_court_judgment(content)

    return {
        "jid": data["JID"],
        "year": data["JYEAR"],
        "case_type": data["JCASE"],
        "case_no": data["JNO"],
        "date": data["JDATE"],
        "title": data["JTITLE"],
        "text": content,
    }

def extract_law_and_crime(text: str) -> tuple[str | None, str | None]:
    """從裁判書中找法院最後認定的主要罪名。"""

    pattern = (
        r"核被告.*?所為.*?"
        r"均係犯"
        r"(刑法第\d+條之\d+第\d+項第\d+款)"
        r"之([^及。]+?)罪"
    )

    match = re.search(pattern, text, re.DOTALL)

    if not match:
        return None, None

    law = match.group(1)
    crime = match.group(2) + "罪"

    return law, crime

def build_dataset():
    rows = []

    for json_file in RAW_DIR.glob("*.json"):
        try:
            data = extract_judgment(json_file)

            law, crime = extract_law_and_crime(data["text"])

            rows.append({
                "jid": data["jid"],
                "year": data["year"],
                "case_type": data["case_type"],
                "case_no": data["case_no"],
                "date": data["date"],
                "title": data["title"],
                "text": data["text"],
                "law": law,
                "crime": crime,
            })

            print(f"完成：{json_file.name}")

        except Exception as e:
            print(f"失敗：{json_file.name}")
            print(e)

    df = pd.DataFrame(rows)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    df.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(f"Dataset 建立完成：{OUTPUT_FILE}")
    print(f"資料筆數：{len(df)}")

if __name__ == "__main__":
    build_dataset()