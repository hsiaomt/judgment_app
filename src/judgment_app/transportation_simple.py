import re
import json
import shutil

from pathlib import Path

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
SPLIT_FINISH_DIR = DATA_DIR / "split_finish"

PATTERNS = [
    ("head", re.compile(r"\d+年度.*?字第\d+號")),
    ("main", re.compile(r"主\s*文")),
    ("fact", re.compile(r"犯罪事實及理由|犯罪事實|事實及理由|理由|事\s*實")),
    ("convict", re.compile(
        r"(?m)^\s*(?:[一二三四五六七八九十]+、\s*)?論罪(?:科刑)?.*$"
    )),
    ("finish", re.compile(r"中\s*華\s*民\s*國")),
    ("attachment", re.compile(
        r"(?m)^\s*[^\w\u4e00-\u9fff]*附\s*件.*$"
    )),
]

TEST_PATTERN = re.compile(r"12345")

def main() -> None:
    process_files()

def process_files() -> None:
    for file_path in RAW_DIR.glob("*.json"):

        try:
            # 讀取原始 JSON
            with file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            # 取得裁判書內容
            content = data["JFULLX"]["JFULLCONTENT"]

            # 解析
            result = split_judgment(content)
            result = {
                "jid": data["JID"],
                "year": data["JYEAR"],
                "case_type": data["JCASE"],
                "case_no": data["JNO"],
                "date": data["JDATE"],
                "title": data["JTITLE"],
                **result,
            }

            # 儲存解析結果
            save_split_result(
                result,
                file_path.name,
            )

            # 成功後刪除 raw 原檔
            file_path.unlink()

            print(f"成功：{file_path.name}")

        except JudgmentTextError as e:
            print(f"解析失敗：{file_path.name} - {e}")

            move_error_file(
                file_path,
                e.section,
            )

        except Exception as e:
            print(f"其他錯誤：{file_path.name} - {e}")

            move_error_file(
                file_path,
                "unknown",
            )

def save_split_result(
    result: dict[str, str],
    filename: str,
) -> None:
    """儲存解析結果。"""

    SPLIT_FINISH_DIR.mkdir(parents=True, exist_ok=True)

    file_path = SPLIT_FINISH_DIR / filename

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            ensure_ascii=False,
            indent=4,
        )

def move_error_file(
    file_path: Path,
    section: str | None,
) -> None:
    error_dir = DATA_DIR / f"error_{section}"
    error_dir.mkdir(parents=True, exist_ok=True)

    shutil.move(
        str(file_path),
        str(error_dir / file_path.name),
    )

def split_judgment(content: str) -> dict[str, str]:

    matches: list[tuple[str, re.Match[str]]] = []

    # 依照 PATTERNS 順序尋找
    start = 0

    for name, pattern in PATTERNS:
        match = pattern.search(content, start)

        if match is None:
            raise JudgmentTextError(
                f"找不到區段：{name}，Pattern：{pattern.pattern}",
                section=name,
            )

        matches.append((name, match))

        # 下一個 Pattern 必須從目前 Pattern 後面開始找
        start = match.end()

    # 切割結果
    result: dict[str, str] = {}

    for i, (name, match) in enumerate(matches):
        if name == "finish":
            continue

        # 下一個 Pattern 的開始位置
        if i + 1 < len(matches):
            end = matches[i + 1][1].start()
        else:
            end = len(content)

        result[name] = content[match.end():end].strip()

    return result

'''def split_text(
    content: str,
    head: re.Pattern[str],
    tail: re.Pattern[str],
) -> str:
    
    # 找頭
    head_match = head.search(content)
    if head_match is None:
        raise JudgmentTextError(
            f"找不到頭：{head.pattern}",
            head=head.pattern,
            tail=tail.pattern,
        )

    # 找尾
    tail_match = tail.search(content, head_match.end())
    if tail_match is None:
        raise JudgmentTextError(
            f"找不到尾：{tail.pattern}",
            head=head.pattern,
            tail=tail.pattern,
        )

    # 取得頭尾中間的內容
    return content[head_match.end():tail_match.start()].strip(), content[tail_match.end():].strip()'''

class JudgmentTextError(Exception):

    def __init__(
        self,
        message: str,
        section: str | None = None,
    ):
        self.section = section
        super().__init__(message)

if __name__ == "__main__":

    main()
    #content = extract_judgment(RAW_DIR / "TPDM,115,交簡,760,20260827,1.json")["content"]

    #text = remove_whitespace(text)
    #head_split = split_text(text, HEAD_PATTERN, MAIN_PATTERN)
    #head_text = head_split[0]
    #main_split = split_text(head_split[1], MAIN_PATTERN, FACT_PATTERN)
    #main_text = main_split[0]
    #convict_split = split_text(main_split[1], CONVICT_PATTERN, FINISH_PATTERN)
    #print(main_text)

    #print(content)
    #print(split_judgment(content))
