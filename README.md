# Excel 案號讀取工具

## 程式結構

- `src/judgment_app/acquisition/`：Excel 案號匯入、判決網頁搜尋與 API 下載。
- `src/judgment_app/analysis/`：AI 分析、分析結果定義與 Excel 比對。
- `src/judgment_app/gui/`：桌面介面；`app.py` 管理主視窗，三個 `*_page.py` 分別處理下載、分析及比對，`common.py` 共用背景工作與路徑選擇。
- `src/judgment_app/case_number.py`、`paths.py`、`exceptions.py`：共用案號、路徑與例外。
- `src/judgment_app/self_test.py`：打包執行檔自我檢查。
- `tests/`：測試；`launcher.py`：PyInstaller 入口。

擷取模組位於 `judgment_app.acquisition.api` 與 `judgment_app.acquisition.web`，分析資料模型位於 `judgment_app.analysis.models`。外部程式的 import 與 `python -m` 指令須使用目前路徑，例如
`judgment_app.acquisition.read_case_numbers` 與 `judgment_app.analysis.analyzer`。
GUI 啟動指令與資料、`.env`、token 快取路徑維持不變。

## 判決書 AI 分析

每名被告保留一筆，`execution_groups` 每組保存八個 `executed_*` 刑度與折算標準，並以 `crime_ids` 引用該被告 `crimes` 內唯一的 `crime_id`。未定應執行刑時清單為空，相關罪仍保留。同一被告內其他欄位相同且所屬分組完全一致的罪才合併 `times`，引用會同步更新。既有 JSON 不自動轉換，需重新分析產生新版格式。

GUI「分析判決書」分頁可選 `openai` 或 `gemini`（預設 OpenAI）。使用 Gemini 時在 `.env` 填入 `GEMINI_API_KEY`，模型由 `GEMINI_MODEL` 指定，預設 `gemini-2.5-flash`；只需設定所選服務的金鑰。兩者沿用相同格式、歷審參考與 JSON 輸出及驗證流程。

```powershell
uv run python -m judgment_app.analysis.analyzer "判決書.json" --provider gemini
```

Gemini 串接依據 [Google GenerateContent API](https://ai.google.dev/api/generate-content)，使用 JSON Schema 結構化輸出。

GUI「分析判決書」分頁可選單一 TXT／JSON 或資料夾，並指定獨立的結果資料夾。來源資料夾直屬的 TXT／JSON 逐份分析；子資料夾則以資料夾名稱的案號為目標（例如 `臺灣高等法院115年度上訴字第1299號`），只儲存該案號判決，其他歷審全文一併提供作為參考。同案號有多份判決會各存一筆；同路徑同檔名的 TXT／JSON 優先使用 JSON。目標比對使用 JSON JID 或下載檔名，不從正文引用的案號猜測。無法辨識資料夾案號或找不到目標時會記錄錯誤。GUI 結果存至指定結果資料夾，保留來源相對目錄，檔名為 `<原檔名>.analysis.json`；`analyze_folder()` 未指定 `output_dir` 時仍存至來源檔旁；重跑會更新該結果檔，產生的分析檔會自動排除，不當成判決再次分析。

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

也可以執行 `uv run python -m judgment_app.gui` 或 `uv run python launcher.py`。PyInstaller 仍使用 `launcher.py` 與既有 spec，無新增套件。

主視窗以三個分頁獨立操作：

- **下載判決書**：保留案號匯入、編輯、排序與所有下載設定；只下載原始檔，不觸發分析。
- **分析判決書**：來源可為單檔或資料夾，輸出指定為資料夾；可選 OpenAI／Gemini，顯示進度、成功／失敗數及原因。
- **結果比對**：Excel 與分析 JSON 各自可選檔案或資料夾；報告位置為 `.json` 檔案。完整報告保留所有列，表格僅顯示非 `equal` 列及讀取錯誤；雙擊可看完整明細。

各分頁的路徑、佇列、狀態與紀錄互相獨立，背景工作以 Queue／after 更新 GUI，工作期間可切換分頁。進度條表示工作進行中，文字顯示核心函式提供的檔案／工作表進度，並非精確百分比。執行中需等待完成才可關閉主視窗。

目前既有 `analyze_judgment()` 仍檢查 `result.jid`，但 `JudgmentAnalysis` 沒有此欄位，可能在模型回應後失敗。GUI 會顯示原始錯誤，不會產生替代結果；本次未修改資料模型、Prompt 或分析演算法。實際 LLM 成功流程需先另行修正這項既有不一致。

下載頁操作：

1. 按「瀏覽」選取 `.xls` 或 `.xlsx` 檔案。
2. 選擇工作表。
3. 輸入法院、年度、字別、號數所在的 Excel 欄位字母。
4. 設定第一筆資料的列號（從 1 起算，跳過標題）。預設為 AL、AM、AN、AO，第 4 列；請依自己的檔案調整。
5. 按「讀取案號」，表格會顯示轉換後的法院名稱、法院代碼及案號。

讀取邏輯沿用 `read_case_numbers.py`：略過全空列、去除重複案號，遇到無效資料則顯示原因與 Excel 列號。修改設定保留清單，再次匯入會合併去重。

可手動新增案號、多欄位排序，再依畫面順序搜尋刑事判決，下載全文 TXT、原始 PDF 或 API JSON。來源 Excel 不會被修改。網頁 TXT／PDF 不需要 API 帳密；API JSON 需要司法院資料開放平台帳密。

單筆案號的法院欄位可直接輸入名稱、簡稱或代碼搜尋，例如「台北」「北院」「tpd」，「台／臺」及英文字母大小寫皆可通用。按 ↓ 或點下拉箭頭選取法院；只有一筆符合時按 Enter 可確認，清空文字則顯示全部法院。Ctrl+A 可選取目前文字後重新輸入。

遇到「裁判書全文大於 1 M，請按此下載檔案」時，會使用頁面提供的原始 PDF 連結。即使只勾選網頁全文，也會改存 `.pdf` 並顯示提示，不會把下載通知存成 TXT；歷審下載亦適用，已有同名 PDF 時會跳過下載。

部分大型判決使用 `GetOverLimit.ashx` 提供 TXT 全文，程式會下載並儲存完整文字。若只勾選 PDF，但網站實際提供 TXT，會改存 `.txt` 並顯示提示；歷審下載同樣適用，已有同名檔案時不會覆寫。

下載結果會在「無符合結果或未公開的案號」前列出：只勾選 TXT 但改存 PDF、只勾選 PDF 但改存 TXT，以及同時勾選兩種格式卻未取得完整格式的案號。清單附裁判日期、缺少的格式及歷審判決的原始案號；已存在而跳過下載的檔案視為已取得。

下載完成的統計摘要下方會分別列出原始判決與歷審判決已存在而跳過的案號，附裁判日期、版本與跳過格式；歷審明細也會標示原始案號。

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

報告包含 Excel 檔案、工作表、列號、JSON 路徑、差異欄位與雙方值。`equal` 為可比較欄位相同、`different` 為有差異、`unmatched` 為未配對、`judgment_not_found` 為判決不存在（未找到可用的同案號 JSON）、`ambiguous` 為多個候選、`incomplete` 為欄位缺漏、`invalid` 為資料格式錯誤。未涵蓋的 Excel 欄位列於 `sheets.uncompared_columns`；本工具以 Excel 各列為基準，不檢查 JSON 是否有額外被告或罪名。

### 比對完整 JudgmentAnalysis（執行明細）

```bash
uv run python -m judgment_app.analysis.compare_judgment --excel data/執行明細.xlsx --json-dir data/judgments --output data/judgment_comparison_report.json
```

新模組依 `data/執行明細.xlsx` 的標題與格式讀取，也接受 `.xls`／`.xlsx` 資料夾並遞迴搜尋。Excel 同案號、同姓名的各工作表與各檔案資料會一起彙整，建立 `JudgmentAnalysis` 後，以 Excel 被告為範圍逐人比對。原本的定位比對仍使用 `compare.py`。

- 同一判決、同一被告內，「序號＋個案序號」相同代表同一 `CrimeAnalysis`，合併並去重法條；`times` 取「次數」，不按法條列數累加。
- 同一被告內依「序號＋個案裁判情形」整理 group 配對條件；Excel 的「應執裁判情形」完全不使用，也不是必填標題。JSON group 以個案裁判情形配對，不比較雙方 `execution_groups` 長度，可共用同一 group，JSON 額外 group 不影響結果。無罪、免訴、不受理、免刑只需存在同裁判情形的 group；科刑仍比較應執行刑度與折算標準。
- 先核對案號，再沿用 `compare.py` 的遮蔽姓名規則；每名 Excel 被告須在該份 JSON 中有唯一配對。JSON 額外被告及雙方被告總人數不影響結果。該被告的 `crimes` 不比較清單長度：每個 Excel 罪須有一個完整相符的 JSON 罪，可重用候選，JSON 額外罪不影響結果。單罪的 `times`、裁判情形、刑度、法條仍比較，不能從不同候選各取相符欄位；法條清單仍不計順序但保留數量。執行刑 group 採上述存在配對規則；無罪、免訴、不受理與免刑也比較。
- 「上訴類別」任何非空值都代表 `is_pros_appeal=True`。空白時，地方法院且案號冠字無「上」視為一審並填 `None`，其餘填 `False`。

忽略欄位：`lower_court_case_number`（需求中的 `lower_case_number`）、`confiscation`、`crime_ids`、`crime_id`、`crime_name`、`pros_appearing`、`appeal_result`、`addition_fine`、`addition_fine_to_detention`、`executed_addition_fine`、`executed_addition_fine_to_detention`。讀取 JSON 時先移除忽略的選填欄位，因此也能讀取仍含舊併科罰金欄位的資料；必要的 ID 與罪名仍接受模型驗證，只是不參與內容比較。

格式轉換依執行明細表：

| Excel 欄位 | 轉換方式 |
| --- | --- |
| 法條 | 九碼依序為條 3 碼、之 1 碼、條段 1 碼、項 1 碼、項段 1 碼、款 2 碼；例如 `3394x1x02` 為第 339 條之 4 第 1 項第 2 款，`000000000` 為無法條 |
| 個案案由 | 沿用 `LawReference` 的法規名稱正規化 |
| 個案徒刑／應執徒刑 | 年月日碼轉月數：`300` → 3 月、`10200` → 14 月；非零日數或月份不合法回報錯誤，不自行四捨五入 |
| 應執緩刑期間 | 年月碼轉年數：`300` → 3 年；非整年回報錯誤 |
| 易科／易勞標準 | `易科一仟折算`／`易勞一仟折算` → 1000，也支援二仟、三仟及數值 |
| 刑度與金額 0 | 轉為 `None`；「次數」必須大於 0 |

表中沒有未遂、幫助欄位，預設 `false`；可增補「未遂／幫助」欄位，支援原比對器的布林值格式。其他缺少的選填欄位採模型預設值，仍參與比較，不視為萬用值。報告的 `sheets.defaulted_fields` 列出缺少來源欄位而採預設的欄位，`sheets.uncompared_columns` 列出未使用的 Excel 標題。

每案的每名 Excel 被告各產生一筆結果，包含 `name`、`excel_rows` 與各份同案號 JSON 的 `document_results`；該被告在任一份同案號 JSON 中唯一配對且內容相同，即為 `equal`，未找到可用的同案號 JSON 時為 `judgment_not_found`（reason 保留「判決不存在」），其他不合為 `unmatched`。`differences` 列出差異路徑與 Excel／JSON 的值。同一罪、同一組或同一被告的資料若跨列衝突，僅該被告回報錯誤並附序號、個案序號，其他被告仍獨立比對。此工具另存 JSON 報告，並將各被告的結果回寫至該被告每一個來源列的 `status`、`reason` 欄位；統計以各案的 Excel 被告為單位（無法辨識案號的錯誤列另計），不檢查 JSON 資料夾的其他案號。


兩種比對器的 Excel 輸出方式相同：`.xlsx` 回寫原檔，`.xls` 另存為 `原檔名.status.xlsx`（已存在則加流水號）。`status` 固定填入 `equal`、`unmatched` 或 `judgment_not_found`，`reason` 填入原因，`equal` 的原因留空。重跑會更新既有欄位、清除已無資料列的舊結果，不重複增加欄位。輸出路徑記錄於報告的 `excel_outputs`。
