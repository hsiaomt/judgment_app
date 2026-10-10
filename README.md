# Excel 案號讀取工具

## 程式結構

- `src/judgment_app/acquisition/`：Excel 案號匯入、判決網頁搜尋與 API 下載。
- `src/judgment_app/analysis/`：AI 分析、分析結果定義與 Excel 比對。
- `src/judgment_app/gui.py`：桌面介面。
- `src/judgment_app/case_number.py`、`paths.py`、`exceptions.py`：共用案號、路徑與例外。
- `src/judgment_app/self_test.py`：打包執行檔自我檢查。
- `tests/`：測試；`launcher.py`：PyInstaller 入口。

擷取模組位於 `judgment_app.acquisition.api` 與 `judgment_app.acquisition.web`，分析資料模型位於 `judgment_app.analysis.models`。外部程式的 import 與 `python -m` 指令須使用目前路徑，例如
`judgment_app.acquisition.read_case_numbers` 與 `judgment_app.analysis.analyzer`。
GUI 啟動指令與資料、`.env`、token 快取路徑維持不變。

## 判決書 AI 分析

每名被告保留一筆，`execution_groups` 每組保存八個 `executed_*` 刑度與折算標準，並以 `crime_ids` 引用該被告 `crimes` 內唯一的 `crime_id`。未定應執行刑時清單為空，相關罪仍保留。同一被告內其他欄位相同且所屬分組完全一致的罪才合併 `times`，引用會同步更新。既有 JSON 不自動轉換，需重新分析產生新版格式。

GUI 分析按鈕下方可選 `openai` 或 `gemini`（預設 OpenAI）。使用 Gemini 時在 `.env` 填入 `GEMINI_API_KEY`，模型由 `GEMINI_MODEL` 指定，預設 `gemini-2.5-flash`；只需設定所選服務的金鑰。兩者沿用相同格式、歷審參考與 JSON 輸出及驗證流程。

```powershell
uv run python -m judgment_app.analysis.analyzer "判決書.json" --provider gemini
```

Gemini 串接依據 [Google GenerateContent API](https://ai.google.dev/api/generate-content)，使用 JSON Schema 結構化輸出。

GUI 可直接按「選擇資料夾…」下方的「分析資料夾判決書」。儲存路徑直屬的 TXT／JSON 逐份分析；子資料夾則以資料夾名稱的案號為目標（例如 `臺灣高等法院115年度上訴字第1299號`），只儲存該案號判決，其他歷審全文一併提供作為參考。同案號有多份判決會各存一筆；同路徑同檔名的 TXT／JSON 優先使用 JSON。目標比對使用 JSON JID 或下載檔名，不從正文引用的案號猜測。無法辨識資料夾案號或找不到目標時會記錄錯誤。結果存至來源檔旁的 `<原檔名>.analysis.json`；重跑會更新該結果檔，產生的分析檔會自動排除，不當成判決再次分析。

在 `.env` 設定 `OPENAI_API_KEY`，可選填 `OPENAI_MODEL`（預設 `gpt-4o-mini`），再執行：

```powershell
uv run python -m judgment_app.analysis.analyzer "判決書.txt" --output data/result.json
uv run python -m judgment_app.analysis.analyzer "判決書.json" --output data/result.json
```

每次讀取一份判決，將全文送至所選 AI API，依 `models.py` 的 dataclass 型別、欄位 `metadata["description"]` 與 `ANALYSIS_RULES` 擷取資料。支援 UTF-8 TXT、司法院 API JSON（`JID`、`JFULLX.JFULLCONTENT`），以及網頁 JSON（`jid`、`text`）；其他編碼可用 `--encoding cp950`。TXT 沒有來源 JID 時使用空字串，可透過 `--jid` 指定。`--model` 可覆寫模型設定。

每份 JSON 直接包含 `models.py` 的完整巢狀欄位。不完整、拒絕或驗證失敗的回應不會寫入，也不會覆蓋既有結果。`penalty` 與 `executed_penalty` 以月為單位，例如 4 年 2 月為 `50`。省略 `--output` 時輸出至來源檔旁；指定相對輸出路徑時沿用專案使用者資料目錄規則。

程式也可匯入 `analyze_judgment()`，回傳 `JudgmentAnalysis`。`ANALYSIS_RESULT.md` 僅指向 Python 中的唯一規則來源，不再需要隨 EXE 打包，也不再提供 `--instructions`。

API 格式參考 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)。

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

### 比對 Excel 與分析 JSON

```bash
uv run python -m judgment_app.analysis.compare --excel data --json-dir data/judgments --output data/comparison_report.json
```

`--excel` 可指定單一 `.xls`／`.xlsx` 或資料夾，`--json-dir` 可指定單一分析 JSON 或資料夾；資料夾會遞迴搜尋。支援目前檢核表的標題列自動辨識與多工作表。

以裁判機關、年度、冠字、號數及被告姓名配對；裁判日期暫不參與配對或比較，列入未比較欄位。同案號多份判決、同法條多罪或遮蔽姓名無法唯一配對時，列出未配對／待確認原因，不任意選取。

刑度固定比較 `crimes` 各罪宣告刑，不比較應執行刑；先篩選 JSON 中有罪的 group，再定位法條、刑度、未遂、幫助並比較其他欄位；Excel「裁判情形」不參與比較。Excel 徒刑 YYMM 轉為月數（402 → 50）；刑度 0 視為 JSON null，未遂／幫助／累犯空白視為 false，法條空白或 x 視為 null。「案由」作為法規名稱，「毒品防制條例」對照「毒品危害防制條例」。

報告包含 Excel 檔案、工作表、列號、JSON 路徑、差異欄位與雙方值。`equal` 為可比較欄位相同、`different` 為有差異、`unmatched` 為未配對、`ambiguous` 為多個候選、`incomplete` 為欄位缺漏、`invalid` 為資料格式錯誤。未涵蓋的 Excel 欄位列於 `sheets.uncompared_columns`；本工具以 Excel 各列為基準，不檢查 JSON 是否有額外被告或罪名。
