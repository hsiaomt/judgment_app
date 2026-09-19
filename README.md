# Excel 案號讀取工具

安裝環境並啟動視窗：

```powershell
uv sync
uv run judgment-app
```

也可以執行 `uv run python -m judgment_app.gui`。

1. 按「瀏覽」選取 `.xls` 或 `.xlsx` 檔案。
2. 選擇工作表。
3. 輸入法院、年度、字別、號數所在的 Excel 欄位字母。
4. 設定第一筆資料的列號（從 1 起算，跳過標題）。預設為 AL、AM、AN、AO，第 4 列；請依自己的檔案調整。
5. 按「讀取案號」，表格會顯示轉換後的法院名稱、法院代碼及案號。

讀取邏輯沿用 `read_case_numbers.py`：略過全空列、去除重複案號，遇到無效資料則顯示原因與 Excel 列號。修改設定會清除舊結果，需要重新讀取。

此介面只讀取 Excel，不會搜尋 JID、下載判決書或修改來源檔案。欄位目前使用字母輸入，尚未提供原始 Excel 預覽或設定保存。

執行讀取整合測試：

```powershell
uv run python -m unittest discover -s tests -v
```
