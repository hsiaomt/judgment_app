const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const fields = ["court", "year", "case", "number"];
const sortLabels = { court: "法院", code: "法院代碼", year: "年度", case: "字別", number: "號數" };
const storageKey = "judgmentWeb.workspace.v1";
const pageSize = 50;
const state = {
  ready: false, busy: false, running: false, token: "", courts: [], workbook: null,
  cases: [], selected: new Set(), sortRules: [], page: 1, logs: [], logFilter: "all",
  jobId: null, cursor: 0, report: null, job: null, pollTimer: null, pollFailures: 0,
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function normalize(text) {
  return String(text).replace(/\s/g, "").replace(/台/g, "臺").toLowerCase();
}

function caseKey(item) {
  return JSON.stringify([item.court, item.year, item.case, item.number]);
}

function caseLabel(item) {
  if (!item) return "";
  return `${item.court_name || item.court}${item.year}年度${item.case}字第${item.number}號`;
}

function announce(message, error = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", error);
  notice.hidden = !message;
}

function setConnection(connected, message) {
  $("#connection").classList.toggle("offline", !connected);
  $("#connection-text").textContent = message || (connected ? "本機服務已連線" : "本機服務連線中斷");
}

async function api(path, payload, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      method: payload === undefined ? "GET" : "POST",
      headers: { ...(payload === undefined ? {} : { "Content-Type": "application/json", "X-Request-Token": state.token }), ...options.headers },
      ...(payload === undefined ? {} : { body: options.raw ? payload : JSON.stringify(payload) }),
      signal: AbortSignal.timeout(options.timeout || 120000),
    });
  } catch (cause) {
    setConnection(false);
    throw new Error("無法連接本機服務，請確認網頁啟動程式仍在執行。", { cause });
  }
  let data;
  try { data = await response.json(); }
  catch (cause) { throw new Error("本機服務回傳的資料格式不正確，請重新整理頁面。", { cause }); }
  if (!response.ok) {
    const error = new Error(data.error || `作業失敗（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  setConnection(true);
  return data;
}

function persist() {
  if (!state.ready) return;
  try {
    sessionStorage.setItem(storageKey, JSON.stringify({
      cases: state.cases, sortRules: state.sortRules, jobId: state.jobId,
      output: $("#output").value,
      formats: { web: $("#format-web").checked, pdf: $("#format-pdf").checked, api: $("#format-api").checked, history: $("#history").checked },
    }));
  } catch { /* A full or disabled browser store must not block the workflow. */ }
}

function restore() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || "null");
    if (!saved || !Array.isArray(saved.cases)) return;
    const courts = new Map(state.courts.map((court) => [court.code, court.name]));
    const seen = new Set();
    state.cases = saved.cases.filter((item) => {
      if (!item || !courts.has(item.court) || !Number.isSafeInteger(item.year) || item.year < 0 || !Number.isSafeInteger(item.number) || item.number < 0 || typeof item.case !== "string" || !item.case.trim()) return false;
      const key = caseKey(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).map((item) => ({ ...item, court_name: courts.get(item.court) }));
    const sortSeen = new Set();
    state.sortRules = (Array.isArray(saved.sortRules) ? saved.sortRules : []).filter((rule) => {
      if (!rule || !Object.hasOwn(sortLabels, rule.field) || typeof rule.reverse !== "boolean" || sortSeen.has(rule.field)) return false;
      sortSeen.add(rule.field);
      return true;
    });
    if (typeof saved.output === "string") $("#output").value = saved.output;
    for (const format of ["web", "pdf", "api", "history"]) {
      if (typeof saved.formats?.[format] === "boolean") $(format === "history" ? "#history" : `#format-${format}`).checked = saved.formats[format];
    }
    if (typeof saved.jobId === "string" && /^[a-f0-9]{32}$/.test(saved.jobId)) state.jobId = saved.jobId;
  } catch { /* Invalid session data is ignored; source files are never involved. */ }
}

function updateControls() {
  const locked = !state.ready || state.busy || state.running;
  $$("[data-lock]").forEach((control) => { control.disabled = locked; });
  const noWorkbook = !state.workbook || !$("#sheet").value;
  ["#sheet", "#start-row", ...fields.map((field) => `#column-${field}`), "#import-button"].forEach((selector) => { $(selector).disabled = locked || noWorkbook; });
  $("#delete-button").disabled = locked || !state.selected.size;
  $("#select-all").disabled = locked || !filteredCases().length;
  $$("[data-sort]").forEach((button) => { button.disabled = locked || !state.cases.length; });
  $$(".row-select").forEach((input) => { input.disabled = locked; });
  $("#history").disabled = locked || !($("#format-web").checked || $("#format-pdf").checked);
  const formats = ["web", "pdf", "api"].filter((key) => $(`#format-${key}`).checked);
  $("#download-button").disabled = locked || !state.cases.length || !formats.length || !$("#output").value.trim();
  $("#download-button span").textContent = state.running ? "正在下載…" : "開始下載";
  $("#download-count").textContent = state.cases.length.toLocaleString();
  $("#format-summary").textContent = formats.map((format) => ({ web: "TXT", pdf: "PDF", api: "JSON" })[format]).join(" · ") || "尚未選擇";
  $("#history-hint").textContent = formats.includes("web") || formats.includes("pdf") ? "適用 TXT 與 PDF 格式" : "JSON 不支援歷審裁判";
  $("#download-hint").textContent = $("#case-search").value ? "搜尋僅篩選顯示，仍下載清單全部案號" : "依清單排序下載全部案號";
  $("#delete-label").textContent = state.selected.size ? `刪除 ${state.selected.size} 筆` : "刪除選取";
  $("#export-report").disabled = !state.logs.length && !state.report;
  const pages = Math.max(1, Math.ceil(filteredCases().length / pageSize));
  $("#previous-page").disabled = state.page <= 1;
  $("#next-page").disabled = state.page >= pages;
}

async function withBusy(operation) {
  if (state.busy || state.running || !state.ready) return;
  state.busy = true;
  updateControls();
  try { await operation(); }
  catch (error) { announce(error.message, true); addLog(error.message, "warning"); }
  finally { state.busy = false; updateControls(); }
}

function addLog(message, kind = null, render = true) {
  if (kind === null) {
    if (/失敗|錯誤|中斷|跳過|無符合|未下載|改存|重試|不足|找不到/.test(message)) kind = "warning";
    else if (/下載成功|完成/.test(message)) kind = "success";
    else kind = "info";
  }
  state.logs.push({ message, kind, time: new Date().toLocaleTimeString("zh-TW", { hour12: false }) });
  if (render) renderLogs();
  $("#export-report").disabled = false;
}

function renderLogs() {
  const log = $("#log");
  const wasAtBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const oldTop = log.scrollTop;
  log.replaceChildren();
  const matches = state.logs.filter((entry) => state.logFilter === "all" || entry.kind === "warning");
  if (!matches.length) {
    log.append(node("div", state.logs.length ? "目前沒有提醒或錯誤。" : "工作台已就緒，匯入或下載後會在這裡顯示紀錄。", "log-placeholder"));
  } else {
    const fragment = document.createDocumentFragment();
    if (matches.length > 500) fragment.append(node("div", "顯示最近 500 筆，完整紀錄可透過「匯出紀錄」儲存。", "log-placeholder"));
    for (const entry of matches.slice(-500)) {
      const line = node("div", undefined, `log-line ${entry.kind}`);
      line.append(node("time", entry.time), node("span", entry.message));
      fragment.append(line);
    }
    log.append(fragment);
  }
  $("#log-count").textContent = state.logs.length.toLocaleString();
  log.scrollTop = $("#auto-scroll").checked || wasAtBottom ? log.scrollHeight : oldTop;
}

function filteredCases() {
  const query = normalize($("#case-search").value);
  return state.cases.map((item, index) => ({ item, index })).filter(({ item }) => !query || normalize(`${caseLabel(item)} ${item.court}`).includes(query));
}

function applySort() {
  // Same stable lowest-priority-first sorting as DownloadPage.refresh_cases.
  // Do not localeCompare: Python compares the strings' Unicode code points.
  for (const { field, reverse } of [...state.sortRules].reverse()) {
    const key = field === "court" ? "court_name" : field === "code" ? "court" : field;
    state.cases.sort((a, b) => (a[key] < b[key] ? -1 : a[key] > b[key] ? 1 : 0) * (reverse ? -1 : 1));
  }
}

function renderCases() {
  const rows = $("#case-rows");
  rows.replaceChildren();
  const matches = filteredCases();
  const pages = Math.max(1, Math.ceil(matches.length / pageSize));
  state.page = Math.min(Math.max(1, state.page), pages);
  const fragment = document.createDocumentFragment();
  for (const { item, index } of matches.slice((state.page - 1) * pageSize, state.page * pageSize)) {
    const key = caseKey(item);
    const tr = node("tr", undefined, state.selected.has(key) ? "selected" : "");
    const selectCell = node("td", undefined, "checkbox-cell");
    const checkbox = node("input", undefined, "row-select");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(key);
    checkbox.setAttribute("aria-label", `選取${caseLabel(item)}`);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(key); else state.selected.delete(key);
      tr.classList.toggle("selected", checkbox.checked);
      updateSelection();
      updateControls();
    });
    selectCell.append(checkbox);
    const code = node("td"); code.append(node("span", item.court, "code-tag"));
    tr.append(selectCell, node("td", String(index + 1).padStart(2, "0"), "index-cell"), node("td", item.court_name, "court-cell"), code,
      node("td", item.year, "number-cell"), node("td", item.case), node("td", item.number, "number-cell"));
    fragment.append(tr);
  }
  rows.append(fragment);
  $("#empty-state").hidden = !!matches.length;
  $("#empty-state h3").textContent = state.cases.length ? "沒有符合的案號" : "從第一筆案號開始";
  $("#empty-state p").textContent = state.cases.length ? "試試其他法院名稱、年度或案號，清單中的資料仍然保留。" : "匯入 Excel 清單，或手動新增一筆案號。重複案號會自動合併，清單更乾淨。";
  $("#empty-add").hidden = !!state.cases.length;
  for (const id of ["#case-count", "#nav-count"]) $(id).textContent = state.cases.length.toLocaleString();
  $("#table-summary").textContent = $("#case-search").value ? `顯示 ${matches.length.toLocaleString()} / ${state.cases.length.toLocaleString()} 筆案號` : `共 ${state.cases.length.toLocaleString()} 筆不重複案號`;
  $("#page-label").textContent = `${state.page} / ${pages}`;
  $("#sort-description").textContent = state.sortRules.length ? `排序：${state.sortRules.map((rule) => `${sortLabels[rule.field]}${rule.reverse ? "降冪" : "升冪"}`).join(" → ")}` : "未設定排序，保留目前順序";
  $$("[data-sort]").forEach((button) => {
    const first = state.sortRules[0];
    const active = first?.field === button.dataset.sort;
    button.closest("th").setAttribute("aria-sort", active ? first.reverse ? "descending" : "ascending" : "none");
    button.querySelector("span").textContent = active ? first.reverse ? "↓" : "↑" : "↕";
  });
  updateSelection();
  updateControls();
  persist();
}

function updateSelection() {
  const matches = filteredCases();
  const selected = matches.filter(({ item }) => state.selected.has(caseKey(item))).length;
  $("#select-all").checked = !!matches.length && selected === matches.length;
  $("#select-all").indeterminate = selected > 0 && selected < matches.length;
}

function mergeCases(incoming) {
  const seen = new Set(state.cases.map(caseKey));
  let added = 0;
  for (const item of incoming) {
    const key = caseKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    state.cases.push(item);
    added++;
  }
  applySort();
  renderCases();
  return added;
}

async function detectLayout() {
  fields.forEach((field) => { $(`#column-${field}`).value = ""; });
  $("#start-row").value = "";
  $("#layout-hint").textContent = "正在辨識欄位與起始列…";
  try {
    const layout = await api("/api/layout", { workbook_id: state.workbook.workbook_id, sheet: $("#sheet").value });
    fields.forEach((field, index) => { $(`#column-${field}`).value = layout.columns[index]; });
    $("#start-row").value = layout.start_row;
    $("#layout-hint").textContent = "已自動辨識欄位，確認後即可讀取。";
    announce("Excel 已就緒，請確認工作表與欄位，再按「讀取並合併」。");
  } catch (error) {
    $("#layout-hint").textContent = "請手動填寫四個欄位與第一筆資料列。";
    announce(error.message, true);
    addLog(error.message, "warning");
  }
}

async function uploadFile(file) {
  if (!file) return;
  await withBusy(async () => {
    if (!/\.(xlsx|xls)$/i.test(file.name)) throw new Error("請選擇 .xlsx 或 .xls Excel 檔案。");
    if (file.size > 30 * 1024 * 1024) throw new Error("Excel 檔案超過 30 MB，請分割後再匯入。");
    announce("正在讀取 Excel 工作表…");
    const workbook = await api("/api/workbooks", file, { raw: true, headers: { "Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name) } });
    state.workbook = workbook;
    $("#file-title").textContent = workbook.filename;
    $("#file-subtitle").textContent = `${workbook.sheets.length} 個工作表 · 點擊更換檔案`;
    $("#sheet").replaceChildren(...workbook.sheets.map((sheet) => {
      const option = node("option", sheet); option.value = sheet; return option;
    }));
    addLog(`已載入 Excel：${workbook.filename}（${workbook.sheets.length} 個工作表）`);
    await detectLayout();
  });
  $("#file-input").value = "";
}

function updateCourtOptions() {
  const query = normalize($("#manual-court").value);
  const exact = state.courts.some((court) => normalize(`${court.name} (${court.code})`) === query);
  const matches = state.courts.filter((court) => !query || exact || court.keywords.some((keyword) => normalize(keyword).includes(query)));
  $("#court-options").replaceChildren(...matches.map((court) => {
    const option = node("option"); option.value = `${court.name} (${court.code})`; return option;
  }));
}

function resolveCourt() {
  const query = normalize($("#manual-court").value);
  if (!query) throw new Error("請輸入並選定法院。");
  const exact = state.courts.filter((court) => normalize(`${court.name} (${court.code})`) === query || court.keywords.some((keyword) => normalize(keyword) === query));
  if (exact.length === 1) return exact[0].code;
  const matches = state.courts.filter((court) => court.keywords.some((keyword) => normalize(keyword).includes(query)));
  if (matches.length === 1) return matches[0].code;
  throw new Error(matches.length ? "有多個符合法院，請從下拉選單選定法院。" : "找不到符合法院，請修改名稱、簡稱或代碼。");
}

function openManual() {
  if (!state.ready || state.busy || state.running) return;
  $("#manual-error").hidden = true;
  if (!$("#manual-court").value) $("#manual-court").value = "臺灣臺北地方法院 (TPD)";
  updateCourtOptions();
  $("#manual-dialog").showModal();
  $("#manual-year").focus();
}

function openSort() {
  const container = $("#sort-rules");
  container.replaceChildren();
  for (let index = 0; index < 5; index++) {
    const row = node("div", undefined, "sort-rule");
    const label = node("label", `第 ${index + 1} 優先`);
    label.htmlFor = `sort-field-${index}`;
    const select = node("select"); select.id = label.htmlFor;
    for (const [key, text] of [["", "不使用"], ...Object.entries(sortLabels)]) {
      const option = node("option", text); option.value = key; select.append(option);
    }
    select.value = state.sortRules[index]?.field || "";
    const direction = node("select"); direction.id = `sort-direction-${index}`;
    direction.setAttribute("aria-label", `第 ${index + 1} 優先排序方向`);
    for (const [value, text] of [["asc", "升冪"], ["desc", "降冪"]]) {
      const option = node("option", text); option.value = value; direction.append(option);
    }
    direction.value = state.sortRules[index]?.reverse ? "desc" : "asc";
    row.append(label, select, direction); container.append(row);
  }
  $("#sort-error").hidden = true;
  $("#sort-dialog").showModal();
}

function renderReport(report) {
  const metrics = [["原始判決・檔案", "files"], ["歷審判決・檔案", "history_files"], ["原始跳過・檔案", "skipped"], ["歷審跳過・檔案", "history_skipped"], ["無結果・案號", "empty"], ["錯誤・項", "errors"]];
  $("#report-stats").replaceChildren(...metrics.map(([label, key]) => {
    const count = (report[key] || []).length;
    const card = node("div", undefined, `report-stat${key === "errors" && count ? " has-errors" : ""}`);
    card.append(node("strong", count.toLocaleString()), node("span", label));
    return card;
  }));
  $("#report-stats").hidden = false;
  const details = $("#report-details"); details.replaceChildren();
  const groups = [["原始判決已存在而跳過", "skipped_documents"], ["歷審判決已存在而跳過", "history_skipped_documents"], ["只選 TXT，但改存 PDF", "txt_to_pdf"], ["只選 PDF，但改存 TXT", "pdf_to_txt"], ["已選 TXT、PDF，但有格式未下載", "missing_formats"], ["無符合結果或未公開的案號", "empty"], ["下載失敗的案號", "failed_cases"], ["失敗原因", "errors"]];
  for (const [title, key] of groups) {
    const entries = report[key] || [];
    if (!entries.length) continue;
    const section = node("details");
    section.append(node("summary", `${title}（${entries.length}）`));
    const list = node("ul");
    for (const entry of entries) {
      let text;
      if (typeof entry === "string") text = entry;
      else if (entry.jid) {
        const info = [`裁判日期：${entry.jid.date}`];
        if (entry.jid.sequence !== null && entry.jid.sequence !== undefined) info.push(`版本：${entry.jid.sequence}`);
        if (entry.skipped_format) info.push(`跳過格式：${entry.skipped_format}`);
        if (entry.missing_formats?.length) info.push(`未下載：${entry.missing_formats.join("、")}`);
        if (entry.original_case) info.push(`原始案號：${caseLabel(entry.original_case)}`);
        text = `${caseLabel(entry.jid.case_number)}（${info.join("；")}）`;
      } else text = `${caseLabel(entry)}${entry.original_case ? `（原始案號：${caseLabel(entry.original_case)}）` : ""}`;
      list.append(node("li", text));
    }
    section.append(list); details.append(section);
  }
  details.hidden = !details.children.length;
}

async function pollJob() {
  clearTimeout(state.pollTimer);
  if (!state.jobId) return;
  try {
    const job = await api(`/api/downloads/${state.jobId}?after=${state.cursor}`, undefined, { timeout: 15000 });
    state.job = job;
    state.pollFailures = 0;
    $("#resume-poll").hidden = true;
    for (const entry of job.events) {
      addLog(entry.message, null, false);
      if (entry.kind === "progress") $("#progress-label").textContent = entry.message;
    }
    if (job.events.length) renderLogs();
    state.cursor = job.next_cursor;
    $("#progress-area").hidden = false;
    $("#job-progress").max = Math.max(job.total, 1);
    $("#job-progress").value = job.completed_cases;
    $("#progress-count").textContent = `${job.completed_cases} / ${job.total} 筆已處理`;
    $("#job-output").textContent = `本次儲存位置：${job.output}`;
    if (job.status === "running") {
      state.running = true;
      $("#job-status").textContent = "下載中";
      $("#job-status").className = "job-status running";
      state.pollTimer = setTimeout(pollJob, 1000);
    } else {
      state.running = false;
      state.report = job.report;
      if (job.status === "completed") {
        const report = job.report;
        const errors = (report.errors || []).length;
        const summary = `下載完成：原始判決 ${report.files.length} 個檔案；歷審判決 ${(report.history_files || []).length} 個檔案；原始 ${report.skipped.length} 個已存在並跳過；歷審 ${(report.history_skipped || []).length} 個已存在並跳過；${report.empty.length} 個案號無符合結果；${errors} 項失敗。`;
        $("#job-status").textContent = errors ? "完成・有錯誤" : "已完成";
        $("#job-status").className = errors ? "job-status error" : "job-status";
        $("#progress-label").textContent = "已處理全部案號";
        addLog(summary, errors ? "warning" : "success");
        announce(summary, !!errors);
        renderReport(report);
      } else {
        $("#job-status").textContent = "作業中斷";
        $("#job-status").className = "job-status error";
        $("#progress-label").textContent = "作業中斷；已儲存的檔案仍保留";
        announce(`下載中斷：${job.error}。已儲存的檔案仍保留。`, true);
      }
    }
    updateControls();
    persist();
  } catch (error) {
    if (error.status === 404) {
      state.running = false;
      state.jobId = null;
      $("#job-status").textContent = "工作已失效";
      announce("找不到上次下載工作，本機服務可能已重新啟動；案號清單仍保留，請至輸出資料夾確認已下載檔案。", true);
      updateControls(); persist(); return;
    }
    state.pollFailures++;
    $("#job-status").textContent = "等待連線";
    $("#job-status").className = "job-status error";
    announce(`${error.message} 尚未確認下載是否完成，重新連線後將繼續取得紀錄。`, true);
    if (state.pollFailures < 5) state.pollTimer = setTimeout(pollJob, 2500);
    else $("#resume-poll").hidden = false;
  }
}

async function startDownload() {
  await withBusy(async () => {
    const payload = { cases: state.cases.map(({ court, year, case: word, number }) => ({ court, year, case: word, number })), output: $("#output").value.trim(), history: $("#history").checked };
    for (const key of ["web", "pdf", "api"]) payload[key] = $(`#format-${key}`).checked;
    const result = await api("/api/downloads", payload);
    state.jobId = result.job_id;
    state.cursor = 0;
    state.report = null;
    state.running = true;
    state.pollFailures = 0;
    $("#report-stats").hidden = true;
    $("#report-details").hidden = true;
    $("#progress-area").hidden = false;
    $("#progress-label").textContent = "正在準備下載…";
    $("#job-progress").value = 0;
    $("#job-progress").max = state.cases.length;
    $("#job-status").textContent = "下載中";
    $("#job-status").className = "job-status running";
    $("#job-output").textContent = `本次儲存位置：${result.output}`;
    addLog(`開始下載 ${state.cases.length} 筆案號，儲存位置：${result.output}`);
    announce("下載工作已開始，可在下方查看即時紀錄。");
    persist();
    $("#activity-section").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
    await pollJob();
  });
}

function bindEvents() {
  $("#dropzone").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", (event) => uploadFile(event.target.files[0]));
  for (const type of ["dragenter", "dragover"]) $("#dropzone").addEventListener(type, (event) => { event.preventDefault(); if (!$("#dropzone").disabled) $("#dropzone").classList.add("dragging"); });
  for (const type of ["dragleave", "drop"]) $("#dropzone").addEventListener(type, (event) => { event.preventDefault(); $("#dropzone").classList.remove("dragging"); });
  $("#dropzone").addEventListener("drop", (event) => { if (!$("#dropzone").disabled) uploadFile(event.dataTransfer.files[0]); });
  document.addEventListener("dragover", (event) => event.preventDefault());
  document.addEventListener("drop", (event) => event.preventDefault());
  $("#sheet").addEventListener("change", () => withBusy(detectLayout));
  for (const selector of ["#start-row", ...fields.map((field) => `#column-${field}`)]) $(selector).addEventListener("input", () => { $("#layout-hint").textContent = "設定已變更；再次讀取將合併至現有清單。"; });
  $("#import-button").addEventListener("click", () => withBusy(async () => {
    const startRow = Number($("#start-row").value);
    const columns = fields.map((field) => $(`#column-${field}`).value.trim().toUpperCase());
    if (!Number.isSafeInteger(startRow) || startRow < 1) throw new Error("第一筆資料列必須是大於 0 的整數。");
    if (columns.some((column) => !/^[A-Z]+$/.test(column)) || new Set(columns).size !== 4) throw new Error("請填寫四個不同的 Excel 英文欄位，例如 A、B、C、D。");
    announce("正在讀取與檢查案號…");
    const result = await api("/api/import", { workbook_id: state.workbook.workbook_id, sheet: $("#sheet").value, start_row: startRow, columns });
    const added = mergeCases(result.cases);
    for (const warning of result.warnings) addLog(warning, "warning", false);
    const message = `匯入完成：新增 ${added} 筆、${result.cases.length - added} 筆已在清單中；目前共 ${state.cases.length} 筆不重複案號。${result.warnings.length ? `另有 ${result.warnings.length} 列無效資料，請查看執行紀錄。` : ""}`;
    addLog(message, result.warnings.length ? "warning" : "success");
    announce(message, !!result.warnings.length);
  }));
  for (const id of ["#manual-open", "#empty-add"]) $(id).addEventListener("click", openManual);
  $("#manual-court").addEventListener("input", updateCourtOptions);
  $("#manual-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.busy || state.running) return;
    $("#manual-error").hidden = true;
    $("#manual-submit").disabled = true;
    state.busy = true; updateControls();
    try {
      const court = resolveCourt();
      const result = await api("/api/cases/validate", { court, year: $("#manual-year").value.trim(), case: $("#manual-case").value.trim(), number: $("#manual-number").value.trim() });
      const added = mergeCases([result.case]);
      const message = added ? `已新增：${caseLabel(result.case)}` : "此案號已在清單中，未重複新增。";
      announce(message); addLog(message);
      $("#manual-dialog").close();
      $("#manual-number").value = "";
    } catch (error) { $("#manual-error").textContent = error.message; $("#manual-error").hidden = false; }
    finally { state.busy = false; $("#manual-submit").disabled = false; updateControls(); }
  });
  $$("[data-close]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));
  $("#guide-open").addEventListener("click", (event) => { event.preventDefault(); $("#guide-dialog").showModal(); });
  $("#sort-open").addEventListener("click", openSort);
  $("#sort-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const rules = Array.from({ length: 5 }, (_, i) => ({ field: $(`#sort-field-${i}`).value, reverse: $(`#sort-direction-${i}`).value === "desc" })).filter((rule) => rule.field);
    if (new Set(rules.map((rule) => rule.field)).size !== rules.length) { $("#sort-error").textContent = "同一欄位只能設定一次。"; $("#sort-error").hidden = false; return; }
    state.sortRules = rules; applySort(); state.page = 1; renderCases(); $("#sort-dialog").close();
  });
  $$("[data-sort]").forEach((button) => button.addEventListener("click", () => {
    const field = button.dataset.sort;
    const reverse = state.sortRules.length === 1 && state.sortRules[0].field === field ? !state.sortRules[0].reverse : false;
    state.sortRules = [{ field, reverse }]; applySort(); state.page = 1; renderCases();
  }));
  $("#case-search").addEventListener("input", () => { state.page = 1; renderCases(); });
  $("#select-all").addEventListener("change", (event) => {
    for (const { item } of filteredCases()) { if (event.target.checked) state.selected.add(caseKey(item)); else state.selected.delete(caseKey(item)); }
    renderCases();
  });
  $("#delete-button").addEventListener("click", () => {
    const count = state.selected.size;
    state.cases = state.cases.filter((item) => !state.selected.has(caseKey(item)));
    state.selected.clear(); renderCases(); announce(`已從本次清單移除 ${count} 筆案號。`);
  });
  $("#previous-page").addEventListener("click", () => { state.page--; renderCases(); });
  $("#next-page").addEventListener("click", () => { state.page++; renderCases(); });
  for (const selector of ["#format-web", "#format-pdf", "#format-api", "#history", "#output"]) $(selector).addEventListener("input", () => { updateControls(); persist(); });
  $("#download-button").addEventListener("click", startDownload);
  $("#resume-poll").addEventListener("click", () => { state.pollFailures = 0; $("#resume-poll").hidden = true; pollJob(); });
  $$("[data-log-filter]").forEach((button) => button.addEventListener("click", () => {
    state.logFilter = button.dataset.logFilter;
    $$("[data-log-filter]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-pressed", String(item === button)); });
    renderLogs();
  }));
  $("#export-report").addEventListener("click", () => {
    const content = JSON.stringify({ exported_at: new Date().toISOString(), job_id: state.jobId, output: state.job?.output || null, status: state.job?.status || null, report: state.report, logs: state.logs }, null, 2);
    const url = URL.createObjectURL(new Blob([content], { type: "application/json;charset=utf-8" }));
    const link = node("a"); link.href = url; link.download = `裁判書下載紀錄_${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
    document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $$(".nav-link").forEach((link) => link.addEventListener("click", () => { $$(".nav-link").forEach((item) => item.classList.toggle("active", item === link)); }));
  window.addEventListener("beforeunload", persist);
}

async function main() {
  bindEvents();
  try {
    const config = await api("/api/config");
    state.token = config.request_token;
    state.courts = config.courts;
    $("#output").value = config.defaults.output;
    for (const key of ["web", "pdf", "api"]) $(`#format-${key}`).checked = config.defaults[key];
    $("#history").checked = config.defaults.history;
    restore();
    state.ready = true;
    state.running = !!state.jobId;
    renderCases();
    if (state.jobId) await pollJob();
  } catch (error) {
    announce(`${error.message} 請啟動 web_launcher.py 後重新整理此頁。`, true);
    setConnection(false);
  }
}

main();
