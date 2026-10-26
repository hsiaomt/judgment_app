# Project

Python 3.12 + uv 專案。
用途：下載、搜尋、解析台灣司法院裁判書。

## Structure

- src/: Python source
- data/: 資料位置
- tests/: tests

## Rules

- 設計方式(例如路徑)皆以日後要打包成執行檔為前提。
- 使用 pathlib，不要使用 os.path。
- HTTP 使用 requests。
- 優先修改既有程式，不要無故建立新架構。
- 不要修改與任務無關的檔案。
- 不要加入新 dependency，除非必要。
- 保持既有 function naming/style。
- 遇到錯誤要保留足夠 exception context。
- 有必要就一併修改測試檔。

## 裁判書專案知識來源

- 處理裁判書專案時，先讀取 Notion 專案總覽及與任務相關的業務規則：
  https://app.notion.com/p/405f9722246d8094aca7cba861d765ce

- 資料模型的實際欄位、型別與驗證器，以目前工作目錄中的
  src/judgment_app/analysis/models.py 為準。

- Notion 的模型內容是同步副本，使用前應確認來源 commit。
  如果 Notion 與程式碼不一致，明確指出差異；
  業務規則有衝突時，不得自行猜測。

- Notion 無法存取時，明確說明，不能聲稱已讀取。
  除非使用者要求，不修改 Notion。

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
