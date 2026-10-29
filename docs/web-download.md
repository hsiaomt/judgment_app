# 裁判書下載中心：本機網頁版

獨立的瀏覽器介面，沿用 `gui/download_page.py` 的欄位與操作規則，以及現有 `read_case_numbers`、`CaseNumber`、`download_cases`。原本的 `launcher.py` 與桌面介面保持不變，沒有新增專案依賴。

## 啟動

在專案根目錄執行：

```sh
uv run python web_launcher.py
```

預設自動開啟 `http://127.0.0.1:8765`。也可使用模組入口：

```sh
uv run python -m judgment_app.web_ui --no-browser --port 8766
```

已有虛擬環境時可直接執行 `.venv/bin/python web_launcher.py`。終端機須保持執行；按 `Ctrl+C` 停止服務。服務僅監聽本機，無須上傳至雲端，網頁資源也不依賴外部 CDN。下載時仍需要網際網路，API JSON 沿用既有 `.env` 與 token 設定。

## 使用方式

1. 拖入或選擇 `.xlsx`／`.xls` 檔案，最大 30 MB。選取工作表後，自動辨識第一筆法院及右側三欄；可以修改欄位字母與起始列，再按「讀取並合併」。
2. 使用「新增單筆」補入案號。法院支援全名、簡稱與代碼；年度、號數須為 ASCII 正整數。Excel 匯入保留原本允許零的規則，錯誤資料列略過並記錄。
3. 點表頭切換升冪／降冪，或設定最多五個不同欄位的排序優先順序。年度與號數按數值排序，其他欄位使用與 Python 相同的字串比較。取消排序保留目前順序。
4. 勾選清單項目可刪除；不會刪除 Excel 或已下載文件。搜尋只影響顯示，下載包含清單全部案號，順序沿用排序結果。「全選」涵蓋所有符合搜尋的案號，包括其他頁。
5. 選擇 TXT、PDF、JSON，可複選。歷審僅適用 TXT／PDF。填入儲存路徑後開始下載，在執行紀錄查看結果與本次實際輸出位置；可匯出 JSON 紀錄。

## 資料與統計

- Excel 先複製到作業系統暫存區供解析，原始檔案不會回寫。服務關閉時清除暫存副本。
- 預設輸出位於 `data/web_downloads/時間_工作識別碼/`。指定其他儲存位置時也會另建唯一子資料夾。歷審裁判沿用既有以原始案號建立資料夾的方式，不掃描、搬移或更改既有判決資料。
- 網頁以本分頁的 `sessionStorage` 保留清單、排序、下載選項與工作識別碼，重新整理會恢復。Excel 需重新選取；下載工作在同一服務程序中可繼續查看。停止服務會中斷尚未完成的工作，重新啟動後不會自動續傳。
- 進度計算為「已處理案號／總案號」，搜尋某案開始時不提前計入完成；處理完不代表每案成功取得檔案。
- 完成統計直接使用原回傳報告的清單長度：`files`、`history_files`、`skipped`、`history_skipped`、`empty`、`errors`。前四項為檔案數，`empty` 為案號數，`errors` 為錯誤項數；不混用件數。
- 格式替代、未提供格式、無公開結果、失敗原因及原始案號均保留於詳細報告。每次獨立輸出，因此不同工作之間不會共用既有檔案的跳過結果。

## 檔案

- `web_launcher.py`：獨立啟動入口。
- `src/judgment_app/web_ui/server.py`：標準函式庫 HTTP 服務、暫存匯入、背景下載與 API。
- `src/judgment_app/web_ui/static/`：原生 HTML、CSS、JavaScript；桌面與手機版響應式介面。
- `tests/test_web_ui.py`：HTTP 整合測試，使用暫存資料與模擬下載，不對司法院發送請求。

```sh
uv run python -m unittest discover -s tests -p 'test_web_ui.py' -v
```

服務使用 localhost、Host／Origin 檢查與請求權杖限制本機操作，並限定靜態檔案清單；這是單機工具，不適合直接公開為多人網路服務。
