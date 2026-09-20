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

讀取邏輯沿用 `read_case_numbers.py`：略過全空列、去除重複案號，遇到無效資料則顯示原因與 Excel 列號。修改設定保留清單，再次匯入會合併去重。

可手動新增案號、多欄位排序，再依畫面順序搜尋刑事判決，下載全文 TXT、原始 PDF 或 API JSON。來源 Excel 不會被修改。網頁 TXT／PDF 不需要 API 帳密；API JSON 需要司法院資料開放平台帳密。

## Windows EXE

直接開啟 `dist/JudgmentApp.exe`，不需另外安裝 Python 或 Microsoft Excel。這是 Windows x64 單檔版本，第一次啟動需等待解壓縮套件；搜尋與下載仍需要網路。

預設下載位置為 `%LOCALAPPDATA%\JudgmentApp\data\judgments`，可在介面變更。相對下載路徑以 `%LOCALAPPDATA%\JudgmentApp` 為基準，不受捷徑的「開始位置」影響；從原始碼執行則以專案根目錄為基準。

使用 API 時，把 `.env.example` 複製為 EXE 同目錄的 `.env`，填入 `JUDICIAL_USERNAME` 與 `JUDICIAL_PASSWORD`。也可放在 `%LOCALAPPDATA%\JudgmentApp\.env`，EXE 旁的設定優先。系統環境變數優先於設定檔。當日 token 快取存於使用者資料目錄的 `token.env`，快取寫入失敗仍可繼續本次下載。設定檔、帳密、既有 Excel 與判決資料不會包入 EXE。

重新建置（Windows）：

```powershell
uv sync --locked
uv run python -m PyInstaller --noconfirm JudgmentApp.spec
```

打包設定明確包含 `openpyxl` 與 `xlrd`，其餘套件、Tk/Tcl 和 HTTPS 憑證由 PyInstaller hooks 收集。路徑處理依 [PyInstaller 執行時文件](https://pyinstaller.org/en/stable/runtime-information.html)，區分 EXE 所在目錄與暫存解壓目錄。

離線驗證實際 EXE（可選第三個參數傳入現有 `.xls` 路徑）：

```powershell
Start-Process .\dist\JudgmentApp.exe -ArgumentList '--self-test', 'smoke-test.json' -Wait
Get-Content smoke-test.json -Encoding UTF8
```

驗證會建立隱藏視窗、產生並讀取暫存 XLSX、檢查憑證及中文檔案寫入；不呼叫線上服務。

執行讀取整合測試：

```powershell
uv run python -m unittest discover -s tests -v
```
