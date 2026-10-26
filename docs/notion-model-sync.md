# 資料模型自動同步到 Notion

來源：`main` 的 `src/judgment_app/analysis/analysis_models.py`。
目的地：https://app.notion.com/p/405f9722246d80328fc4c98a3ab570d1

只同步模型檔全文、由 AST 取得的欄位及型別、commit 與內容 SHA256。
不執行模型、不呼叫 LLM、不讀取裁判書資料或 .env。
首次附加自動區域，後續只替換 `MODEL_SYNC_START` 到 `MODEL_SYNC_END`。
人工業務規則請保留在標記外；不要刪除或重複標記。
原頁的「歷史設計」仍是歷史說明，最新程式以自動區域及其 commit 連結為準。

## 一次性設定

1. 在 https://www.notion.so/profile/integrations 建立內部 integration，選擇對應工作區，開啟讀取、插入及更新內容能力。
2. 在 Notion「02｜資料模型」頁的 Connections 加入這個 integration。ChatGPT 的 Notion 連線不會自動授權 GitHub Actions。
3. 到 GitHub 儲存庫 Settings → Secrets and variables → Actions → New repository secret，新增 `NOTION_TOKEN`，值為 integration secret。不要貼進聊天、程式碼或 Notion 頁面。
4. 將這次新增檔案提交並合併至 `main`。首次 push 會觸發；也可以在 Actions → Sync analysis model to Notion → Run workflow 手動執行。
5. 確認 Actions 成功，再查看 Notion 自動區域中的 commit 是否對應 `main`。

後續模型檔 push 到 main 就會同步。本機尚未 push 的修改不會同步。
佇列中的每次執行會讀取執行時的 main，避免舊工作把新版覆蓋。
若 API 失敗或逾時，工作會失敗；先檢查 Notion 與 Actions，再手動重跑。
停用方式：在 GitHub Actions 停用此 workflow。

## 本機檢查

```bash
uv run python -m unittest discover -s tests -p test_sync_notion_model.py
uv run python scripts/sync_notion_model.py --dry-run
```

dry-run 不需要 Notion 金鑰，也不寫入 Notion。腳本讀取程序環境變數，不會自動載入 .env。

API 參考：https://developers.notion.com/reference/update-page-markdown
