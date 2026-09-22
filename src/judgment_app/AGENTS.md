# Project

Python 3.12 + uv 專案。
用途：下載、搜尋、解析台灣司法院裁判書。

## Structure

- src/: Python source
- data/raw/: 原始裁判書
- data/split_finish/: 處理完成資料
- tests/: tests

## Rules

- 設計方式(例如路徑)皆以日後要打包成執行檔為前提
- 使用 pathlib，不要使用 os.path。
- HTTP 使用 requests。
- 優先修改既有程式，不要無故建立新架構。
- 不要修改與任務無關的檔案。
- 不要加入新 dependency，除非必要。
- 保持既有 function naming/style。
- 遇到錯誤要保留足夠 exception context。

## Style

- 程式碼順序依照：def main() -> 外部使用方法(或使用者呼叫方法) -> 內部方法 -> if __name__ == "__main__"

## Workflow

修改前：
1. 先找相關檔案。
2. 只讀完成任務必要的程式碼。

修改後：
1. 執行相關測試。
2. 有錯誤就修正並重新測試。
3. 最後簡短回報修改檔案、修改內容、測試結果。

告訴我使用了哪些AGENTS.md
不要輸出長篇解釋。