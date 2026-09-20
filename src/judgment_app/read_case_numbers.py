"""從 Excel 指定欄位讀取案號。"""

import re
import argparse
from pathlib import Path
from typing import TypedDict

import pandas as pd

COURT_MAP = {
    "": "所有法院",
    "JCC": "憲法法庭",
    "TPC": "司法院刑事補償法庭",
    "TPU": "司法院－訴願決定",
    "TPS": "最高法院",
    "TPA": "最高行政法院(含改制前行政法院)",
    "TPP": "懲戒法院－懲戒法庭",
    "TPJ": "懲戒法院－職務法庭",
    "TPH": "臺灣高等法院",
    "001": "臺灣高等法院－訴願決定",
    "TPB": "臺北高等行政法院 高等庭(含改制前臺北高等行政法院)",
    "TPT": "臺北高等行政法院 地方庭",
    "TCB": "臺中高等行政法院 高等庭(含改制前臺中高等行政法院)",
    "TCT": "臺中高等行政法院 地方庭",
    "KSB": "高雄高等行政法院 高等庭(含改制前高雄高等行政法院)",
    "KST": "高雄高等行政法院 地方庭",
    "IPC": "智慧財產及商業法院",
    "TCH": "臺灣高等法院 臺中分院",
    "TNH": "臺灣高等法院 臺南分院",
    "KSH": "臺灣高等法院 高雄分院",
    "HLH": "臺灣高等法院 花蓮分院",
    "TPD": "臺灣臺北地方法院",
    "SLD": "臺灣士林地方法院",
    "PCD": "臺灣新北地方法院",
    "ILD": "臺灣宜蘭地方法院",
    "KLD": "臺灣基隆地方法院",
    "TYD": "臺灣桃園地方法院",
    "SCD": "臺灣新竹地方法院",
    "MLD": "臺灣苗栗地方法院",
    "TCD": "臺灣臺中地方法院",
    "CHD": "臺灣彰化地方法院",
    "NTD": "臺灣南投地方法院",
    "ULD": "臺灣雲林地方法院",
    "CYD": "臺灣嘉義地方法院",
    "TND": "臺灣臺南地方法院",
    "KSD": "臺灣高雄地方法院",
    "CTD": "臺灣橋頭地方法院",
    "HLD": "臺灣花蓮地方法院",
    "TTD": "臺灣臺東地方法院",
    "PTD": "臺灣屏東地方法院",
    "PHD": "臺灣澎湖地方法院",
    "KMH": "福建高等法院金門分院",
    "KMD": "福建金門地方法院",
    "LCD": "福建連江地方法院",
    "KSY": "臺灣高雄少年及家事法院",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel", type=Path)
    args = parser.parse_args()
    cases = read_case_numbers(
        args.excel,
        "AL", "AM", "AN", "AO",  # 法院、年度、字別、號數
        start_row=4,            # 第一筆資料的 Excel 列號
    )

    print(len(cases))
    for case in cases:
        print(case)
    #print(cases[0])
    # {'court': 'TPS', 'year': 114, 'case': '台上', 'number': 6577}


class CaseNumber(TypedDict):
    court: str
    year: int
    case: str
    number: int


def read_case_numbers(
    excel_path: str | Path,
    court_column: str,
    year_column: str,
    case_column: str,
    number_column: str,
    *,
    sheet_name: str | int = 0,
    start_row: int = 2,
) -> list[CaseNumber]:
    """依序讀取法院、年度、字別、號數，回傳可供 search_judgments 使用的清單。

    欄位使用 Excel 字母（例如 AL），start_row 為第一筆資料的列號（從 1 起算）。
    預設讀取第一張工作表，略過第一列標題；範例檔案需設定 start_row=4。
    法院全名、地院／高院簡稱及台／臺寫法會依 COURT_MAP 轉換為代碼。
    年度、號數轉為整數；依四個欄位去除重複案號，保留首次出現的順序。
    四欄全空的列略過。
    部分欄位空白、未知法院或無效數字會拋出含 Excel 列號的 ValueError。
    .xls 檔使用 xlrd 讀取；其他格式由 pandas 選擇對應的讀取引擎。
    """
    if isinstance(start_row, bool) or not isinstance(start_row, int) or start_row < 1:
        raise ValueError("start_row 必須是大於 0 的整數")
    columns = (court_column, year_column, case_column, number_column)
    indices = [_column_index(column) for column in columns]
    if len(set(indices)) != 4:
        raise ValueError("法院、年度、字別、號數必須指定四個不同欄位")

    frame = pd.read_excel(excel_path, sheet_name=sheet_name, header=None, dtype=object)
    if max(indices) >= frame.shape[1]:
        raise ValueError(f"指定欄位超出工作表範圍：{columns}")

    codes = _court_codes()
    results: list[CaseNumber] = []
    seen: set[tuple[str, int, str, int]] = set()
    for row_number, values in enumerate(
        frame.iloc[start_row - 1:, indices].itertuples(index=False, name=None),
        start=start_row,
    ):
        parts = ["" if pd.isna(value) else str(value).strip() for value in values]
        if not any(parts):
            continue
        if not all(parts):
            raise ValueError(f"Excel 第 {row_number} 列案號欄位不完整：{parts}")
        court, year, case, number = parts
        code = codes.get(_normalize_court(court))
        if code is None:
            raise ValueError(f"Excel 第 {row_number} 列法院不在 COURT_MAP 中：{court}")
        integers = []
        for label, value in (("年度", year), ("號數", number)):
            if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
                raise ValueError(f"Excel 第 {row_number} 列{label}必須是非負整數：{value}")
            integers.append(int(value.split(".")[0]))
        key = (code, integers[0], case, integers[1])
        if key in seen:
            continue
        seen.add(key)
        results.append(CaseNumber(court=code, year=integers[0], case=case, number=integers[1]))
    return results


def _column_index(column: str) -> int:
    if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z]+", column):
        raise ValueError(f"Excel 欄位必須是英文字母，例如 AL：{column!r}")
    index = 0
    for letter in column.upper():
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def _court_codes() -> dict[str, str]:
    codes = {}
    for code, name in COURT_MAP.items():
        if not code:
            continue
        aliases = {name, name.replace("地方法院", "地院").replace("高等法院", "高院")}
        if name.startswith(("臺灣", "福建")) and name.endswith("地方法院"):
            aliases.update({name[2:], name[2:].replace("地方法院", "地院")})
        for alias in aliases:
            codes[_normalize_court(alias)] = code
        codes[code] = code
    return codes


def _normalize_court(name: str) -> str:
    return re.sub(r"\s+", "", name).replace("台", "臺")


if __name__ == "__main__":
    main()
