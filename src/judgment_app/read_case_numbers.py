"""從 Excel 指定欄位讀取案號。"""

import re
import argparse
from pathlib import Path

import pandas as pd

from judgment_app.case_number import CaseNumber, normalize_court


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

    results: list[CaseNumber] = []
    seen: set[CaseNumber] = set()
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
        try:
            code = normalize_court(court)
        except ValueError as exc:
            raise ValueError(f"Excel 第 {row_number} 列{exc}") from exc
        integers = []
        for label, value in (("年度", year), ("號數", number)):
            if not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
                raise ValueError(f"Excel 第 {row_number} 列{label}必須是非負整數：{value}")
            integers.append(int(value.split(".")[0]))
        key = CaseNumber(code, integers[0], case, integers[1])
        if key in seen:
            continue
        seen.add(key)
        results.append(key)
    return results


def _column_index(column: str) -> int:
    if not isinstance(column, str) or not re.fullmatch(r"[A-Za-z]+", column):
        raise ValueError(f"Excel 欄位必須是英文字母，例如 AL：{column!r}")
    index = 0
    for letter in column.upper():
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


if __name__ == "__main__":
    main()
