"use strict";

// Apply theme instantly on load before DOM is fully ready
(function() {
  const savedTheme = localStorage.getItem("phonebot_theme") || "dark";
  if (savedTheme === "light") {
    document.documentElement.classList.add("light-theme");
  }
})();

const MODEL_SUGGESTIONS = {
  openai: ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"],
  gemini: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
  ollama: ["qwen2.5-coder:1.5b", "qwen2.5-coder:7b", "llama3.2:1b", "llama3:latest", "phi3:latest"]
};

const state = {
  view: "dashboard",
  config: {
    provider: localStorage.getItem("phonebot.provider") || "openai",
    apiKey: "",
    baseUrl: localStorage.getItem("phonebot.baseUrl") || "",
    model: localStorage.getItem("phonebot.model") || "gpt-5-mini",
    promptLimit: localStorage.getItem("phonebot.promptLimit") || "90000",
    redact: localStorage.getItem("phonebot.redact") !== "false",
    useDatabase: localStorage.getItem("phonebot.useDatabase") !== "false",
    reuseStrong: localStorage.getItem("phonebot.reuseStrong") !== "false"
  },
  files: [],
  runtimeFiles: [],
  runtimeSelectedPort: "",
  runtimeLastResponse: null,
  stats: null,
  dashboardCases: [],
  allCases: [],
  selectedCaseId: null,
  lastResponse: null,
  copilotChat: [],
  activeCase: null,
  health: null
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("vi-VN", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"
  });
}

function relativeDate(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return formatDate(value);
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "vừa xong";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} phút trước`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} giờ trước`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} ngày trước`;
  return formatDate(value);
}

function verdictLabel(verdict) {
  return ({
    correct: "Đúng",
    partially_correct: "Đúng một phần",
    likely_incorrect: "Có khả năng sai",
    insufficient_evidence: "Chưa đủ bằng chứng"
  })[verdict] || verdict || "Unknown";
}

function sourceLabel(mode) {
  return ({
    database_cache: "DB cache",
    ai_with_history: "AI + history",
    ai_new: "AI new",
    deterministic_only: "Local runtime",
    deterministic_plus_ai: "Runtime + AI"
  })[mode] || mode || "-";
}

function badge(value, label = null) {
  return `<span class="badge ${escapeHtml(value || "unknown")}">${escapeHtml(label || verdictLabel(value))}</span>`;
}

function confidenceCell(score) {
  const value = Math.max(0, Math.min(100, Number(score) || 0));
  return `<div class="confidence"><div class="confidence-bar"><i style="width:${value}%"></i></div><span>${value}%</span></div>`;
}

function toast(title, message = "", type = "info") {
  const stack = $("#toastStack");
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.innerHTML = `<i></i><div><strong>${escapeHtml(title)}</strong>${message ? `<span>${escapeHtml(message)}</span>` : ""}</div>`;
  stack.appendChild(item);
  setTimeout(() => item.remove(), 4200);
}

function setLoading(active, message = "Đang chọn evidence...") {
  $("#loadingOverlay").classList.toggle("hidden", !active);
  $("#loadingMessage").textContent = message;
}

function persistConfig() {
  localStorage.setItem("phonebot.provider", state.config.provider);
  localStorage.setItem("phonebot.baseUrl", state.config.baseUrl);
  localStorage.setItem("phonebot.model", state.config.model);
  localStorage.setItem("phonebot.promptLimit", state.config.promptLimit);
  localStorage.setItem("phonebot.redact", String(state.config.redact));
  localStorage.setItem("phonebot.useDatabase", String(state.config.useDatabase));
  localStorage.setItem("phonebot.reuseStrong", String(state.config.reuseStrong));
}

function defaultModel(provider) {
  const fromHealth = state.health?.default_models?.[provider];
  return fromHealth || MODEL_SUGGESTIONS[provider][0];
}

function updateModelSuggestions() {
  const list = $("#modelSuggestions");
  list.innerHTML = MODEL_SUGGESTIONS[state.config.provider]
    .map(model => `<option value="${escapeHtml(model)}"></option>`).join("");
}

function syncConfigControls() {
  updateModelSuggestions();
  $$('[data-config]').forEach(control => {
    const key = control.dataset.config;
    if (control.type === "checkbox") control.checked = Boolean(state.config[key]);
    else control.value = state.config[key] ?? "";
  });
  $$(".base-url-group").forEach(group => group.classList.toggle("hidden", state.config.provider === "gemini"));
  const providerLabel = state.config.provider === "gemini" ? "Gemini" : (state.config.provider === "ollama" ? "Ollama" : "OpenAI");
  $("#analysisProviderPill").textContent = `${providerLabel} · ${state.config.model || "chưa chọn model"}`;
  const runtimePill = $("#runtimeProviderPill");
  if (runtimePill) runtimePill.textContent = $("#runtimeUseAi")?.checked ? `${providerLabel} · ${state.config.model || "chưa chọn model"}` : "Local engine · miễn phí";
}

function updateConfig(key, control) {
  state.config[key] = control.type === "checkbox" ? control.checked : control.value;
  if (key === "provider") {
    const available = MODEL_SUGGESTIONS[state.config.provider];
    if (!available.includes(state.config.model)) {
      let belongsToOther = false;
      for (const [prov, models] of Object.entries(MODEL_SUGGESTIONS)) {
        if (prov !== state.config.provider && models.includes(state.config.model)) {
          belongsToOther = true;
          break;
        }
      }
      if (state.config.provider === "ollama" && (String(state.config.model).toLowerCase().includes("gemini") || String(state.config.model).toLowerCase().includes("gpt"))) {
        belongsToOther = true;
      }
      if (belongsToOther) {
        state.config.model = defaultModel(state.config.provider);
      }
    }
    
    if (state.config.provider === "gemini") {
      state.config.baseUrl = "";
    } else if (state.config.provider === "ollama" && !state.config.baseUrl) {
      state.config.baseUrl = "http://localhost:11434/v1";
    } else if (state.config.provider === "openai" && state.config.baseUrl === "http://localhost:11434/v1") {
      state.config.baseUrl = "";
    }
  }
  if (key !== "apiKey") persistConfig();
  syncConfigControls();
}

function switchView(name) {
  state.view = name;
  $$(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-item[data-view]").forEach(item => item.classList.toggle("active", item.dataset.view === name));
  $("#appShell").classList.remove("menu-open");
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (name === "dashboard") loadDashboard();
  if (name === "cases") loadCases();
  if (name === "reports") renderReportCenter(state.lastResponse);
  if (name === "runtime") loadRuntimeHistory();
  if (name === "settings") loadStats();
  if (name === "uph") initUphCalculator();
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const rawText = await response.text();
  let data = {};

  if (rawText.trim()) {
    try {
      data = JSON.parse(rawText);
    } catch {
      data = {
        detail: rawText.trim(),
        response_was_not_json: true
      };
    }
  }

  if (!response.ok) {
    const errorId = data.error_id ? ` [${data.error_id}]` : "";
    const logHint = data.log_file ? ` · Xem ${data.log_file}` : "";
    throw new Error(`${data.detail || `HTTP ${response.status}`}${errorId}${logHint}`);
  }
  return data;
}

async function checkHealth() {
  const statusDot = $(".status-dot");
  try {
    state.health = await apiJson("/api/health");
    $("#serverStatus").textContent = `Online · v${state.health.version || "0.9.5"}`;
    statusDot.classList.add("online");
    statusDot.classList.remove("offline");
    const configuredDefault = defaultModel(state.config.provider);
    if (!state.config.model) state.config.model = configuredDefault;
    syncConfigControls();
  } catch (error) {
    $("#serverStatus").textContent = "Không kết nối";
    statusDot.classList.add("offline");
    toast("Server không phản hồi", error.message, "error");
  }
}

async function loadStats() {
  try {
    const stats = await apiJson("/api/database/stats");
    state.stats = stats;
    const total = stats.total_cases || 0;
    $("#statTotal").textContent = total;
    $("#statNewAi").textContent = stats.new_ai_runs || 0;
    $("#statHistoryAi").textContent = stats.ai_with_history_runs || 0;
    $("#statCache").textContent = stats.cached_runs || 0;
    $("#statCacheRate").textContent = `${total ? Math.round((stats.cached_runs || 0) / total * 100) : 0}% tổng lượt`;
    $("#sidebarCaseCount").textContent = total > 999 ? "999+" : total;
    $("#settingsCaseCount").textContent = total;
    $("#settingsLatestCase").textContent = formatDate(stats.latest_case_at);
    const runtimeTotal = stats.total_runtime_cases || 0;
    if ($("#statRuntime")) $("#statRuntime").textContent = runtimeTotal;
    if ($("#statRuntimeSlow")) $("#statRuntimeSlow").textContent = `${stats.slow_cases || 0} case chậm`;
    if ($("#sidebarRuntimeCount")) $("#sidebarRuntimeCount").textContent = runtimeTotal > 999 ? "999+" : runtimeTotal;
    $("#settingsDatabasePath").textContent = stats.database_path || "data/phonebot_cases.db";
  } catch (error) {
    toast("Không đọc được database stats", error.message, "error");
  }
}

function caseRow(item, dashboard = false) {
  const columns = dashboard
    ? `<td class="id-cell">#${item.id}</td><td>${badge(item.verdict)}</td><td>${confidenceCell(item.confidence_score)}</td><td class="error-cell"><span class="cell-title">${escapeHtml(item.reported_error)}</span><span class="cell-subtitle">${escapeHtml(item.executive_summary || "")}</span></td><td>${escapeHtml(item.failure_stage || "-")}</td><td>${badge(item.source_mode, sourceLabel(item.source_mode))}</td><td>${escapeHtml(relativeDate(item.created_at))}</td><td><button class="row-action" data-open-case="${item.id}" title="Xem chi tiết"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></td>`
    : `<td class="id-cell">#${item.id}</td><td>${badge(item.verdict)}</td><td class="error-cell"><span class="cell-title">${escapeHtml(item.reported_error)}</span><span class="cell-subtitle">${escapeHtml(item.executive_summary || "")}</span></td><td>${confidenceCell(item.confidence_score)}</td><td>${escapeHtml(item.failure_stage || "-")}</td><td>${escapeHtml(item.provider || "-")}<span class="cell-subtitle">${escapeHtml(item.model || "")}</span></td><td>${badge(item.source_mode, sourceLabel(item.source_mode))}</td><td>${escapeHtml(formatDate(item.created_at))}</td><td><button class="row-action" data-open-case="${item.id}" title="Xem chi tiết"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></td>`;
  return `<tr data-case-row="${item.id}" class="${state.selectedCaseId === item.id ? "selected" : ""}">${columns}</tr>`;
}

async function loadDashboard() {
  await loadStats();
  try {
    const data = await apiJson("/api/cases?limit=30");
    state.dashboardCases = data.cases || [];
    $("#dashboardCaseSubtitle").textContent = `${state.dashboardCases.length} case gần nhất`;
    $("#dashboardCasesBody").innerHTML = state.dashboardCases.length
      ? state.dashboardCases.map(item => caseRow(item, true)).join("")
      : `<tr><td colspan="8" class="empty-cell">Database chưa có case. Hãy tạo phân tích đầu tiên.</td></tr>`;
    bindCaseRows($("#dashboardCasesBody"), "dashboardDetailPanel");
    if (state.dashboardCases.length && !state.selectedCaseId) openCase(state.dashboardCases[0].id, "dashboardDetailPanel", false);
  } catch (error) {
    $("#dashboardCasesBody").innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadCases() {
  const query = encodeURIComponent($("#caseSearch")?.value.trim() || "");
  const limit = $("#caseLimit")?.value || "30";
  $("#allCasesBody").innerHTML = `<tr><td colspan="9" class="empty-cell">Đang tải case...</td></tr>`;
  try {
    const data = await apiJson(`/api/cases?limit=${limit}&query=${query}`);
    state.allCases = data.cases || [];
    state.stats = data.stats || state.stats;
    $("#allCasesBody").innerHTML = state.allCases.length
      ? state.allCases.map(item => caseRow(item, false)).join("")
      : `<tr><td colspan="9" class="empty-cell">Không tìm thấy case phù hợp.</td></tr>`;
    bindCaseRows($("#allCasesBody"), "caseDetailPanel");
    await loadStats();
  } catch (error) {
    $("#allCasesBody").innerHTML = `<tr><td colspan="9" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

function bindCaseRows(root, panelId) {
  root.onclick = event => {
    const target = event.target.closest("[data-open-case], [data-case-row]");
    if (!target) return;
    const id = Number(target.dataset.openCase || target.dataset.caseRow);
    if (id) openCase(id, panelId, true);
  };
}

async function openCase(id, panelId, highlight = true) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.innerHTML = `<div class="detail-empty"><div class="spinner"></div><h3>Đang tải case #${id}</h3></div>`;
  try {
    const item = await apiJson(`/api/cases/${id}`);
    state.selectedCaseId = id;
    state.activeCase = item;
    if (highlight) {
      $$('[data-case-row]').forEach(row => row.classList.toggle("selected", Number(row.dataset.caseRow) === id));
    }
    renderCaseDetail(item, panel);
  } catch (error) {
    panel.innerHTML = `<div class="detail-empty"><h3>Không mở được case</h3><p>${escapeHtml(error.message)}</p></div>`;
  }
}

function renderCaseDetail(item, panel) {
  const analysis = item.analysis || {};
  const files = item.file_names || [];
  const roots = (analysis.root_cause_candidates || []).slice(0, 4);
  const checks = (analysis.recommended_checks || []).slice(0, 6);
  panel.innerHTML = `
    <div class="detail-content">
      <div class="detail-head">
        <span class="detail-kicker">Case #${item.id} · ${escapeHtml(formatDate(item.created_at))}</span>
        <h3>${escapeHtml(item.reported_error)}</h3>
        <div class="detail-badges">${badge(item.verdict)} ${badge(item.source_mode, sourceLabel(item.source_mode))}</div>
      </div>
      <div class="detail-body">
        <div class="detail-grid">
          <div class="detail-field"><span>Confidence</span><strong>${Number(item.confidence_score || 0)}%</strong></div>
          <div class="detail-field"><span>Failure stage</span><strong>${escapeHtml(item.failure_stage || "-")}</strong></div>
          <div class="detail-field"><span>Provider</span><strong>${escapeHtml(item.provider || "-")}</strong></div>
          <div class="detail-field"><span>Model</span><strong>${escapeHtml(item.model || "-")}</strong></div>
        </div>
        <div class="detail-section"><h4>Executive summary</h4><p>${escapeHtml(item.executive_summary || analysis.executive_summary || "Không có tóm tắt.")}</p></div>
        <div class="detail-section"><h4>Root-cause candidates</h4>${roots.length ? `<ul class="detail-list">${roots.map(root => `<li><strong>${escapeHtml(root.cause)}</strong> · ${escapeHtml(root.confidence)}<br>${escapeHtml(root.reasoning || "")}</li>`).join("")}</ul>` : `<p>Không có dữ liệu.</p>`}</div>
        <div class="detail-section"><h4>Recommended checks</h4>${checks.length ? `<ul class="detail-list">${checks.map(check => `<li>${escapeHtml(check)}</li>`).join("")}</ul>` : `<p>Không có dữ liệu.</p>`}</div>
        <div class="detail-section"><h4>Log files</h4><div>${files.length ? files.map(file => `<span class="file-chip interactive-file-chip" style="cursor: pointer;" data-file="${escapeHtml(file)}">${escapeHtml(file)}</span>`).join("") : `<p>Không có tên file.</p>`}</div></div>
      </div>
      <div class="detail-actions">
        <button class="button ghost" data-case-report="${item.id}">Mở báo cáo</button>
        <button class="button ghost danger-button" data-delete-case="${item.id}">Xóa case</button>
      </div>
    </div>`;
  panel.querySelector("[data-case-report]").addEventListener("click", () => openCaseReport(item));
  panel.querySelector("[data-delete-case]").addEventListener("click", () => removeCase(item.id));
}

function openCaseReport(item) {
  state.lastResponse = { analysis: item.analysis || {}, meta: item.meta || { provider: item.provider, model: item.model, database: { source_mode: item.source_mode, saved_case_id: item.id } } };
  updateExportButtons();
  renderReportCenter(state.lastResponse);
  switchView("reports");
}

async function removeCase(id) {
  if (!window.confirm(`Xóa case #${id} khỏi database?`)) return;
  try {
    await apiJson(`/api/cases/${id}`, { method: "DELETE" });
    if (state.selectedCaseId === id) state.selectedCaseId = null;
    toast("Đã xóa case", `Case #${id} đã được xóa khỏi SQLite.`, "success");
    await Promise.all([loadStats(), loadDashboard(), loadCases()]);
  } catch (error) {
    toast("Không xóa được case", error.message, "error");
  }
}

function mergeFiles(fileList) {
  const supported = new Set(["log", "txt", "json", "csv", "xml", "plist"]);
  const map = new Map(state.files.map(file => [`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file]));
  let skipped = 0;
  [...fileList].forEach(file => {
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!supported.has(extension)) { skipped += 1; return; }
    map.set(`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file);
  });
  state.files = [...map.values()];
  renderFiles();
  if (skipped) toast("Đã bỏ qua file không hỗ trợ", `${skipped} file không thuộc định dạng log cho phép.`);
}

function renderFiles() {
  $("#selectedFilesCount").textContent = `${state.files.length} file được chọn`;
  $("#fileList").innerHTML = state.files.length ? state.files.map(file => {
    const path = file.webkitRelativePath || file.name;
    return `<div class="file-row"><svg viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5"/></svg><div><strong title="${escapeHtml(path)}">${escapeHtml(path)}</strong><span>${(file.size / 1024).toFixed(1)} KB</span></div><button class="row-action" data-remove-file="${escapeHtml(path)}" title="Bỏ file"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>`;
  }).join("") : `<div class="empty-inline">Chưa có file log.</div>`;
  $$("[data-remove-file]", $("#fileList")).forEach(button => button.addEventListener("click", () => {
    const path = button.dataset.removeFile;
    state.files = state.files.filter(file => (file.webkitRelativePath || file.name) !== path);
    renderFiles();
  }));
}

function resetAnalysis() {
  state.files = [];
  $("#folderInput").value = "";
  $("#filesInput").value = "";
  $("#reportedError").value = "";
  $("#analysisStatus").textContent = "Sẵn sàng.";
  $("#analysisStatus").className = "analysis-status";
  $("#resultShell").classList.add("hidden");
  renderFiles();
}

async function analyzeCase() {
  const reportedError = $("#reportedError").value.trim();
  if (!reportedError) { toast("Thiếu lỗi SW", "Hãy nhập thông báo lỗi được máy test báo.", "error"); return; }
  if (!state.files.length) { toast("Chưa có log", "Hãy chọn folder hoặc ít nhất một file log.", "error"); return; }
  if (!String(state.config.model).trim()) { toast("Thiếu model", "Hãy nhập model AI cần sử dụng.", "error"); return; }

  const form = new FormData();
  form.append("reported_error", reportedError);
  form.append("provider", state.config.provider);
  form.append("api_key", state.config.apiKey.trim());
  form.append("base_url", state.config.baseUrl.trim());
  form.append("model", state.config.model.trim());
  form.append("prompt_char_limit", state.config.promptLimit);
  form.append("redact_sensitive_data", state.config.redact);
  form.append("use_case_database", state.config.useDatabase);
  form.append("reuse_strong_match", state.config.reuseStrong);
  state.files.forEach(file => form.append("files", file, file.webkitRelativePath || file.name));

  const status = $("#analysisStatus");
  status.textContent = "Đang lọc log và tìm case tương tự...";
  status.className = "analysis-status";
  setLoading(true, "Đang pre-scan log và tìm case tương tự trong SQLite...");
  const messages = [
    "Đang pre-scan log và tìm case tương tự trong SQLite...",
    "Đang chọn các dòng evidence liên quan...",
    "Đang dựng timeline từ SW, Vision và trục Z...",
    "Đang xác minh verdict và root cause..."
  ];
  let messageIndex = 0;
  const timer = setInterval(() => {
    messageIndex = Math.min(messageIndex + 1, messages.length - 1);
    $("#loadingMessage").textContent = messages[messageIndex];
  }, 4500);

  try {
    const data = await apiJson("/api/analyze", { method: "POST", body: form });
    state.lastResponse = data;
    renderAnalysisResult(data);
    updateExportButtons();
    const mode = data.meta?.database?.source_mode;
    status.textContent = mode === "database_cache" ? "Hoàn tất bằng database cache." : mode === "ai_with_history" ? "Hoàn tất bằng AI và case tham khảo." : "Hoàn tất bằng AI phân tích mới.";
    status.className = "analysis-status success";
    toast("Phân tích hoàn tất", `Case #${data.meta?.database?.saved_case_id || "-"} đã được lưu vào database.`, "success");
    await Promise.all([loadStats(), loadDashboard()]);
  } catch (error) {
    status.textContent = `Lỗi: ${error.message}`;
    status.className = "analysis-status error";
    toast("Phân tích thất bại", error.message, "error");
  } finally {
    clearInterval(timer);
    setLoading(false);
  }
}

function renderStringList(targetId, values, emptyText = "Không có dữ liệu.") {
  const target = document.getElementById(targetId);
  target.innerHTML = values?.length ? values.map(value => `<div class="stack-item">${escapeHtml(value)}</div>`).join("") : `<div class="empty-inline">${escapeHtml(emptyText)}</div>`;
}

function renderEvidence(targetId, values) {
  const target = document.getElementById(targetId);
  target.innerHTML = values?.length ? values.map(item => `
    <article class="evidence-card clickable-evidence" style="cursor: pointer;" data-file="${escapeHtml(item.file || "")}" data-start="${item.line_start || 0}" data-end="${item.line_end || 0}">
      <div class="evidence-ref"><span>${escapeHtml(item.file || "-")}</span><span>L${item.line_start || "?"}–L${item.line_end || "?"}</span></div>
      <blockquote>“${escapeHtml(item.quote || "") }”</blockquote>
      <p>${escapeHtml(item.interpretation || "")}</p>
    </article>`).join("") : `<div class="empty-inline">Không có evidence.</div>`;
}

function renderAnalysisResult(data) {
  const analysis = data.analysis || {};
  const meta = data.meta || {};
  const mode = meta.database?.source_mode || "ai_new";
  $("#resultShell").classList.remove("hidden");
  $("#resultTitle").textContent = analysis.reported_error || "Kết quả phân tích";
  $("#resultVerdict").textContent = verdictLabel(analysis.verdict);
  $("#resultVerdict").className = analysis.verdict === "correct" ? "ok" : analysis.verdict === "likely_incorrect" ? "bad" : "warn";
  $("#resultConfidence").textContent = `${analysis.confidence_score ?? 0}%`;
  $("#resultFailureStage").textContent = analysis.failure_stage || "-";
  $("#resultSource").textContent = sourceLabel(mode);
  $("#resultModel").textContent = `${meta.provider || "-"} / ${meta.model || "-"}`;
  $("#resultSummary").textContent = analysis.executive_summary || "";
  $("#resultFailureExplanation").textContent = analysis.failure_explanation || "";
  $("#resultSuggestedMessage").textContent = analysis.suggested_error_message || "";
  renderStringList("resultChecks", analysis.recommended_checks);
  renderStringList("resultMissing", analysis.missing_logs_or_data);
  renderStringList("resultProven", analysis.what_is_proven);
  renderStringList("resultNotProven", analysis.what_is_not_proven);
  renderEvidence("resultSupporting", analysis.supporting_evidence);
  renderEvidence("resultAgainst", analysis.contradicting_or_limiting_evidence);

  // Render Verdict Banner
  const verdictBanner = $("#resultVerdictBanner");
  const verdictBannerIcon = $("#resultVerdictBannerIcon");
  const verdictBannerBadge = $("#resultVerdictBannerBadge");
  const verdictBannerTitle = $("#resultVerdictBannerTitle");
  const verdictBannerDesc = $("#resultVerdictBannerDesc");
  const verdictBannerEvidence = $("#resultVerdictBannerEvidence");

  if (analysis.verdict) {
    verdictBanner.className = `verdict-banner ${analysis.verdict}`;
    let icon = "⚠️";
    let title = "";
    let desc = "";
    
    if (analysis.verdict === "correct") {
      icon = "✅";
      title = "Xác nhận lỗi chính xác";
      desc = "Phân tích log và pre-scan hoàn toàn trùng khớp với lỗi do SW báo.";
    } else if (analysis.verdict === "partially_correct") {
      icon = "⚡";
      title = "Lỗi chính xác một phần";
      desc = "Lỗi do SW báo có phần đúng nhưng chưa đầy đủ hoặc có thêm nguyên nhân phụ quan trọng.";
    } else if (analysis.verdict === "likely_incorrect") {
      icon = "❌";
      title = "Phát hiện lỗi sai lệch";
      desc = "Bằng chứng log thực tế không khớp hoặc phủ định hoàn toàn lỗi do SW báo.";
    } else {
      icon = "❓";
      title = "Không đủ bằng chứng xác minh";
      desc = "Log được cung cấp thiếu thông tin để đưa ra kết luận chắc chắn.";
    }
    
    verdictBannerIcon.textContent = icon;
    verdictBannerBadge.textContent = analysis.verdict;
    verdictBannerTitle.textContent = title;
    verdictBannerDesc.textContent = analysis.failure_explanation || desc;
    
    const preScanText = meta.deterministic_pre_scan || "";
    const errorMatch = preScanText.match(/CÁC SỰ KIỆN LỖI\/CẢNH BÁO TRỰC TIẾP PHÁT HIỆN ĐƯỢC:\s*([\s\S]+)/);
    
    if (errorMatch && errorMatch[1].trim()) {
      const items = errorMatch[1].split("\n").filter(line => line.trim().startsWith("-"));
      if (items.length) {
        verdictBannerEvidence.innerHTML = `<strong>Bằng chứng lỗi trực tiếp phát hiện được:</strong>` + 
          items.map(item => `<span>${escapeHtml(item)}</span>`).join("");
        verdictBannerEvidence.classList.remove("hidden");
      } else {
        verdictBannerEvidence.classList.add("hidden");
      }
    } else {
      verdictBannerEvidence.classList.add("hidden");
    }
    verdictBanner.classList.remove("hidden");
  } else {
    verdictBanner.classList.add("hidden");
  }

  $("#resultTimeline").innerHTML = analysis.test_timeline?.length ? analysis.test_timeline.map(item => `
    <tr><td>${escapeHtml(item.time || "-")}</td><td>${escapeHtml(item.stage || "-")}</td><td>${badge(item.status, item.status)}</td><td>${escapeHtml(item.description || "")}</td><td>${(item.evidence || []).map(evidence => `<span class="cell-subtitle interactive-evidence-chip" style="cursor: pointer; text-decoration: underline;" data-file="${escapeHtml(evidence.file)}" data-start="${evidence.line_start}" data-end="${evidence.line_end}">${escapeHtml(evidence.file)} L${evidence.line_start}-${evidence.line_end}</span>`).join("") || "-"}</td></tr>`).join("") : `<tr><td colspan="5" class="empty-cell">Không có timeline.</td></tr>`;

  $("#resultRootCauses").innerHTML = analysis.root_cause_candidates?.length ? analysis.root_cause_candidates.map(root => `
    <article class="root-cause-card"><span class="root-confidence ${escapeHtml(root.confidence)}">${escapeHtml(root.confidence)}</span><h4>${escapeHtml(root.cause)}</h4><p>${escapeHtml(root.reasoning || "")}</p></article>`).join("") : `<div class="empty-inline">Không có root-cause candidate.</div>`;

  const matches = meta.database?.similar_cases || [];
  $("#resultSimilarCases").innerHTML = matches.length ? matches.map(item => {
    let scoreClass = "info";
    if (item.score_percent >= 85) scoreClass = "correct";
    else if (item.score_percent >= 70) scoreClass = "warning";
    else scoreClass = "fail";

    const reasonTags = (item.reasons || []).map(reason => {
      let extraClass = "normal-match-badge";
      if (reason.includes("ngữ nghĩa AI")) {
        extraClass = "semantic-badge";
      } else if (reason.includes("Trùng mã lỗi")) {
        extraClass = "code-match-badge";
      }
      return `<span class="reason-tag ${extraClass}">${escapeHtml(reason)}</span>`;
    }).join("");

    return `
      <article class="similar-card">
        <div class="similar-score-circle ${scoreClass}">
          <span class="score-num">${item.score_percent}</span>
          <span class="score-pct">%</span>
        </div>
        <div class="similar-details">
          <div class="similar-header">
            <span class="similar-id-badge">Case #${item.case_id}</span>
            ${badge(item.verdict)}
            ${item.failure_stage ? `<span class="similar-stage-badge">${escapeHtml(item.failure_stage)}</span>` : ""}
          </div>
          <strong class="similar-error">${escapeHtml(item.reported_error)}</strong>
          <div class="similar-reasons">
            ${reasonTags}
          </div>
        </div>
        <button class="row-action similar-action-btn" data-similar-case="${item.case_id}" title="Mở chi tiết case">
          <svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg>
        </button>
      </article>
    `;
  }).join("") : `<div class="empty-inline">Không có case cũ đạt ngưỡng tương đồng.</div>`;

  $$("[data-similar-case]", $("#resultSimilarCases")).forEach(button => button.addEventListener("click", async () => {
    switchView("cases");
    await openCase(Number(button.dataset.similarCase), "caseDetailPanel", true);
  }));
  $("#resultMetadata").textContent = JSON.stringify(meta, null, 2);
  state.copilotChat = [];
  renderCopilotChat();
  renderReportCenter(data);
  $("#resultShell").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderMarkdown(text) {
  if (!text) return "";
  let html = escapeHtml(text);
  
  // Code blocks: ``` ... ```
  html = html.replace(/```(?:[a-zA-Z0-9]+)?\n([\s\S]*?)\n```/g, '<pre><code>$1</code></pre>');
  
  // Inline code: `code`
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  
  // Bold: **text**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  
  // Bullet lists
  html = html.replace(/^\s*[-*+]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
  html = html.replace(/<\/ul>\s*<ul>/g, '');
  
  const paragraphs = html.split(/\n\n+/);
  return paragraphs.map(p => {
    if (p.trim().startsWith("<pre") || p.trim().startsWith("<ul")) {
      return p;
    }
    return `<p>${p.replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

function renderCopilotChat() {
  const container = $("#copilotMessages");
  if (!container) return;
  
  const welcomeHtml = `
    <div class="copilot-msg system">
      <div class="msg-sender">Copilot</div>
      <div class="msg-bubble">Xin chào! Tôi đã nạp toàn bộ logs và tóm tắt của case này vào ngữ cảnh trò chuyện. Bạn có câu hỏi cụ thể nào về lỗi hoặc giải pháp không?</div>
    </div>
  `;
  
  container.innerHTML = welcomeHtml + state.copilotChat.map(msg => {
    const sender = msg.role === "user" ? "Kỹ sư" : "Copilot";
    const bubbleContent = msg.role === "user" ? escapeHtml(msg.content) : renderMarkdown(msg.content);
    return `
      <div class="copilot-msg ${msg.role}">
        <div class="msg-sender">${sender}</div>
        <div class="msg-bubble">${bubbleContent}</div>
      </div>
    `;
  }).join("");
  container.scrollTop = container.scrollHeight;
}

async function sendCopilotMessage() {
  const inputEl = $("#copilotInput");
  const text = inputEl.value.trim();
  if (!text) return;
  if (!state.lastResponse) {
    toast("Lỗi", "Vui lòng chạy phân tích log trước khi trò chuyện với AI Copilot.", "error");
    return;
  }
  
  inputEl.value = "";
  inputEl.disabled = true;
  const sendBtn = $("#sendCopilotBtn");
  sendBtn.disabled = true;
  
  state.copilotChat.push({ role: "user", content: text });
  renderCopilotChat();
  
  const msgContainer = $("#copilotMessages");
  msgContainer.scrollTop = msgContainer.scrollHeight;
  
  const loaderId = "copilot-loader-" + Date.now();
  const loaderHtml = `
    <div class="copilot-msg system" id="${loaderId}">
      <div class="msg-sender">Copilot</div>
      <div class="msg-bubble" style="display:flex; align-items:center; gap:8px;">
        <span class="spinner" style="width:14px; height:14px; border-width:2px; margin:0;"></span>
        <span>AI Copilot đang phân tích câu hỏi của bạn...</span>
      </div>
    </div>
  `;
  msgContainer.insertAdjacentHTML("beforeend", loaderHtml);
  msgContainer.scrollTop = msgContainer.scrollHeight;
  
  try {
    const response = await fetch("/api/copilot-chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: state.config.provider,
        api_key: state.config.apiKey || null,
        base_url: state.config.baseUrl || null,
        model: state.config.model || null,
        reported_error: state.lastResponse.analysis?.reported_error || "",
        verdict: state.lastResponse.analysis?.verdict || "",
        executive_summary: state.lastResponse.analysis?.executive_summary || "",
        evidence_excerpt: (state.lastResponse.meta?.files || []).map(f => `=== FILE: ${f.name} ===\n${f.selected_text || ""}`).join("\n\n"),
        messages: state.copilotChat
      })
    });
    
    const data = await response.json();
    $(`#${loaderId}`)?.remove();
    
    if (!response.ok) {
      throw new Error(data.detail || "Giao tiếp Copilot thất bại.");
    }
    
    state.copilotChat.push({ role: "assistant", content: data.reply });
    renderCopilotChat();
  } catch (err) {
    $(`#${loaderId}`)?.remove();
    toast("Lỗi Copilot", err.message, "error");
    const errorBubble = `
      <div class="copilot-msg system">
        <div class="msg-sender">Copilot</div>
        <div class="msg-bubble" style="border-color:rgba(239,91,91,.22); background:rgba(239,91,91,.05); color:#ef7777;">
          Lỗi: Không thể kết nối với AI Copilot. Chi tiết: ${escapeHtml(err.message)}
        </div>
      </div>
    `;
    msgContainer.insertAdjacentHTML("beforeend", errorBubble);
  } finally {
    inputEl.disabled = false;
    sendBtn.disabled = false;
    inputEl.focus();
    msgContainer.scrollTop = msgContainer.scrollHeight;
  }
}

function renderReportCenter(response) {
  const center = $("#reportCenter");
  if (!response?.analysis) {
    center.innerHTML = `<div class="detail-empty"><div class="empty-icon"><svg viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M8 12h8M8 16h8"/></svg></div><h3>Chưa có báo cáo trong phiên này</h3><p>Thực hiện một lần phân tích mới hoặc mở một case trong database để xem báo cáo.</p><button class="button primary" data-go="analyze">Phân tích case mới</button></div>`;
    center.querySelector("[data-go]")?.addEventListener("click", () => switchView("analyze"));
    return;
  }
  const analysis = response.analysis;
  const meta = response.meta || {};
  center.innerHTML = `
    <div class="panel-header"><div><h2>${escapeHtml(analysis.reported_error || "Analysis report")}</h2><span>${escapeHtml(meta.provider || "-")} · ${escapeHtml(meta.model || "-")} · ${escapeHtml(sourceLabel(meta.database?.source_mode))}</span></div>${badge(analysis.verdict)}</div>
    <div class="result-card">
      <div class="detail-grid"><div class="detail-field"><span>Confidence</span><strong>${analysis.confidence_score || 0}%</strong></div><div class="detail-field"><span>Failure stage</span><strong>${escapeHtml(analysis.failure_stage || "-")}</strong></div></div>
      <h3>Executive summary</h3><p>${escapeHtml(analysis.executive_summary || "")}</p>
      <div class="callout"><strong>Failure explanation</strong><p>${escapeHtml(analysis.failure_explanation || "")}</p></div>
      <div class="two-column-result" style="margin-top:14px">
        <div><h3>What is proven</h3><div class="stack-list">${(analysis.what_is_proven || []).map(value => `<div class="stack-item">${escapeHtml(value)}</div>`).join("") || `<div class="empty-inline">Không có.</div>`}</div></div>
        <div><h3>Recommended checks</h3><div class="stack-list">${(analysis.recommended_checks || []).map(value => `<div class="stack-item">${escapeHtml(value)}</div>`).join("") || `<div class="empty-inline">Không có.</div>`}</div></div>
      </div>
      <div class="suggested-message"><span>Suggested error message</span><code>${escapeHtml(analysis.suggested_error_message || "")}</code></div>
    </div>`;
}

function updateExportButtons() {
  const enabled = Boolean(state.lastResponse);
  ["topExportButton", "reportExportJson", "reportExportTxt"].forEach(id => {
    const element = document.getElementById(id);
    if (element) element.disabled = !enabled;
  });
}

function exportJson() {
  if (!state.lastResponse) { toast("Chưa có dữ liệu", "Hãy phân tích hoặc mở một case trước."); return; }
  download("phonebot_ai_analysis.json", JSON.stringify(state.lastResponse, null, 2), "application/json");
}

function exportTxt() {
  if (!state.lastResponse?.analysis) { toast("Chưa có dữ liệu", "Hãy phân tích hoặc mở một case trước."); return; }
  const a = state.lastResponse.analysis;
  const text = `PHONEBOT FAILURE ANALYSIS\n=========================\nReported error: ${a.reported_error || ""}\nVerdict: ${a.verdict || ""}\nConfidence: ${a.confidence_score || 0}%\nFailure stage: ${a.failure_stage || ""}\n\nEXECUTIVE SUMMARY\n${a.executive_summary || ""}\n\nWHAT IS PROVEN\n${(a.what_is_proven || []).map(x => `- ${x}`).join("\n")}\n\nWHAT IS NOT PROVEN\n${(a.what_is_not_proven || []).map(x => `- ${x}`).join("\n")}\n\nFAILURE EXPLANATION\n${a.failure_explanation || ""}\n\nROOT CAUSES\n${(a.root_cause_candidates || []).map(x => `- [${x.confidence}] ${x.cause}: ${x.reasoning}`).join("\n")}\n\nRECOMMENDED CHECKS\n${(a.recommended_checks || []).map(x => `- ${x}`).join("\n")}\n\nSUGGESTED ERROR MESSAGE\n${a.suggested_error_message || ""}\n`;
  download("phonebot_ai_analysis.txt", text, "text/plain;charset=utf-8");
}

function download(name, content, type) {
  const anchor = document.createElement("a");
  anchor.href = URL.createObjectURL(new Blob([content], { type }));
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(anchor.href), 500);
}


function durationText(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "-";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m ${secs.toFixed(1)}s`;
  if (minutes) return `${minutes}m ${secs.toFixed(1)}s`;
  return `${secs.toFixed(1)}s`;
}

function runtimeClassLabel(value) {
  return ({ slow: "CHẬM", within_expected: "TRONG NGƯỠNG", insufficient_data: "THIẾU DỮ LIỆU" })[value] || value || "-";
}

function runtimeBadge(value) {
  return `<span class="badge runtime-${escapeHtml(value || "unknown")}">${escapeHtml(runtimeClassLabel(value))}</span>`;
}

function retryTimeoutLabel(value) {
  return ({
    retry: "RETRY",
    timeout: "TIMEOUT",
    suspected_timeout: "WAIT/TIMEOUT NGHI NGỜ",
    repeated_action: "LẶP THAO TÁC"
  })[value] || value || "-";
}

function retryTimeoutBadge(value, detection = "explicit") {
  const detectionLabel = detection === "explicit" ? "LOG TRỰC TIẾP" : detection === "mixed" ? "TRỰC TIẾP + SUY LUẬN" : "SUY LUẬN";
  return `<span class="rt-event-badge rt-${escapeHtml(value || "unknown")}">${escapeHtml(retryTimeoutLabel(value))}</span><span class="rt-detection ${escapeHtml(detection)}">${detectionLabel}</span>`;
}

function renderModuleCounts(id, values, emptyText) {
  const element = $(`#${id}`);
  if (!element) return;
  const entries = Object.entries(values || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
  element.innerHTML = entries.length ? entries.map(([module, count]) => `
    <div class="module-count-row"><span>${escapeHtml(module)}</span><strong>${Number(count) || 0}</strong></div>`).join("") : `<div class="empty-inline">${escapeHtml(emptyText)}</div>`;
}

function detectRuntimePort(pathValue) {
  const normalized = String(pathValue || "").replace(/\\/g, "/");
  const segments = normalized.split("/").filter(Boolean);
  const patterns = [
    /^port[ _-]*0*([1-9]\d?)$/i,
    /^(?:slot|channel|ch)[ _-]*0*([1-9]\d?)$/i,
    /^p[ _-]*0*([1-9]\d?)$/i
  ];
  for (const segment of segments.slice(0, -1)) {
    for (const pattern of patterns) {
      const match = segment.match(pattern);
      if (match) return `port${Number(match[1])}`;
    }
  }
  const basename = segments.at(-1) || normalized;
  const basenameMatch = basename.match(/(?:^|[_ .-])port[ _-]*0*([1-9]\d?)(?=$|[_ .-])/i);
  return basenameMatch ? `port${Number(basenameMatch[1])}` : "";
}

function runtimePortLabel(portKey) {
  const match = String(portKey || "").match(/^port(\d+)$/i);
  return match ? `Port ${Number(match[1])}` : (portKey || "Không xác định");
}

function detectedRuntimePorts() {
  const values = new Set();
  state.runtimeFiles.forEach(file => {
    const key = detectRuntimePort(file.webkitRelativePath || file.name);
    if (key) values.add(key);
  });
  return [...values].sort((a, b) => Number(a.replace("port", "")) - Number(b.replace("port", "")));
}

function refreshRuntimePortOptions() {
  const select = $("#runtimePortSelect");
  const help = $("#runtimePortHelp");
  if (!select) return;
  const ports = detectedRuntimePorts();
  const previous = state.runtimeSelectedPort || select.value;
  if (!state.runtimeFiles.length) {
    select.innerHTML = `<option value="">Chọn folder log để phát hiện Port</option>`;
    select.value = "";
    state.runtimeSelectedPort = "";
    if (help) help.textContent = "Mỗi port được tách độc lập. BEGIN/END TRANSACTION của cả batch không được dùng làm runtime riêng của phone.";
    return;
  }
  if (!ports.length) {
    select.innerHTML = `<option value="">Không phát hiện Port trong đường dẫn — phân tích các file hiện tại</option>`;
    select.value = "";
    state.runtimeSelectedPort = "";
    if (help) help.textContent = "Tên folder nên là port1, port2, port3, port4 để tách chính xác từng port.";
    return;
  }
  const placeholder = ports.length > 1
    ? `<option value="">-- Chọn đúng Port, không trộn dữ liệu --</option>`
    : "";
  select.innerHTML = placeholder + ports.map(port => `<option value="${port}">${runtimePortLabel(port)}</option>`).join("");
  if (ports.length === 1) {
    select.value = ports[0];
  } else if (ports.includes(previous)) {
    select.value = previous;
  } else {
    select.value = "";
  }
  state.runtimeSelectedPort = select.value;
  if (help) {
    help.textContent = ports.length > 1
      ? `Phát hiện ${ports.map(runtimePortLabel).join(", ")}. Chọn một port; runtime sẽ tính từ activity đầu tiên đến activity cuối cùng của đúng port, không dùng batch BEGIN/END.`
      : `Đã phát hiện ${runtimePortLabel(ports[0])}. Chỉ activity riêng của port này được dùng để tính runtime phone.`;
  }
}

function mergeRuntimeFiles(fileList) {
  const supported = new Set(["log", "txt", "json", "csv", "xml", "plist"]);
  const map = new Map(state.runtimeFiles.map(file => [`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file]));
  let skipped = 0;
  [...fileList].forEach(file => {
    const extension = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!supported.has(extension)) { skipped += 1; return; }
    map.set(`${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`, file);
  });
  state.runtimeFiles = [...map.values()];
  renderRuntimeFiles();
  refreshRuntimePortOptions();
  if (skipped) toast("Đã bỏ qua file không hỗ trợ", `${skipped} file không thuộc định dạng log cho phép.`);
}

function renderRuntimeFiles() {
  const count = $("#runtimeSelectedFilesCount");
  const list = $("#runtimeFileList");
  if (!count || !list) return;
  count.textContent = `${state.runtimeFiles.length} file được chọn`;
  list.innerHTML = state.runtimeFiles.length ? state.runtimeFiles.map(file => {
    const path = file.webkitRelativePath || file.name;
    return `<div class="file-row"><svg viewBox="0 0 24 24"><path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5"/></svg><div><strong title="${escapeHtml(path)}">${escapeHtml(path)}</strong><span>${(file.size / 1024).toFixed(1)} KB</span></div><button class="row-action" data-runtime-remove-file="${escapeHtml(path)}" title="Bỏ file"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>`;
  }).join("") : `<div class="empty-inline">Chưa có file log.</div>`;
  $$('[data-runtime-remove-file]', list).forEach(button => button.addEventListener("click", () => {
    const path = button.dataset.runtimeRemoveFile;
    state.runtimeFiles = state.runtimeFiles.filter(file => (file.webkitRelativePath || file.name) !== path);
    renderRuntimeFiles();
    refreshRuntimePortOptions();
  }));
}

function resetRuntimeAnalysis() {
  state.runtimeFiles = [];
  state.runtimeSelectedPort = "";
  state.runtimeLastResponse = null;
  $("#runtimeFolderInput").value = "";
  $("#runtimeFilesInput").value = "";
  $("#runtimeProcessLabel").value = "";
  $("#runtimeThreshold").value = "13";
  $("#runtimeGapThreshold").value = "30";
  $("#runtimeResultShell").classList.add("hidden");
  $("#runtimeStatus").textContent = "Sẵn sàng.";
  $("#runtimeStatus").className = "analysis-status";
  renderRuntimeFiles();
  refreshRuntimePortOptions();
}

function updateRuntimeAiConfig() {
  const enabled = Boolean($("#runtimeUseAi")?.checked);
  $("#runtimeAiConfig")?.classList.toggle("disabled-section", !enabled);
  const pill = $("#runtimeProviderPill");
  if (pill) pill.textContent = enabled ? `${state.config.provider === "gemini" ? "Gemini" : "OpenAI"} · ${state.config.model}` : "Local engine · miễn phí";
}

async function analyzeRuntimeCase() {
  if (!state.runtimeFiles.length) { toast("Chưa có log", "Hãy chọn folder hoặc file log để phân tích runtime.", "error"); return; }
  const threshold = Number($("#runtimeThreshold").value || 13);
  const gapThreshold = Number($("#runtimeGapThreshold").value || 30);
  const detectedPorts = detectedRuntimePorts();
  const selectedPort = $("#runtimePortSelect")?.value || "";
  if (detectedPorts.length > 1 && !selectedPort) {
    toast("Chưa chọn Port", `Folder có ${detectedPorts.map(runtimePortLabel).join(", ")}. Hãy chọn đúng port cần phân tích.`, "error");
    $("#runtimePortSelect")?.focus();
    return;
  }
  state.runtimeSelectedPort = selectedPort;
  if (!(threshold >= 1 && threshold <= 240)) { toast("Ngưỡng không hợp lệ", "Ngưỡng chậm phải từ 1 đến 240 phút.", "error"); return; }

  const form = new FormData();
  form.append("process_label", $("#runtimeProcessLabel").value.trim());
  form.append("selected_port", selectedPort);
  form.append("slow_threshold_minutes", threshold);
  form.append("gap_threshold_seconds", gapThreshold);
  form.append("use_ai_explanation", $("#runtimeUseAi").checked);
  form.append("provider", state.config.provider);
  form.append("api_key", state.config.apiKey.trim());
  form.append("base_url", state.config.baseUrl.trim());
  form.append("model", state.config.model.trim());
  form.append("redact_sensitive_data", state.config.redact);
  form.append("prompt_char_limit", state.config.promptLimit);
  state.runtimeFiles.forEach(file => form.append("files", file, file.webkitRelativePath || file.name));

  const status = $("#runtimeStatus");
  status.textContent = "Đang tách runtime riêng của phone khỏi thời gian cả batch...";
  status.className = "analysis-status";
  setLoading(true, "Đang tìm activity đầu tiên → cuối cùng của đúng port...");
  const messages = [
    "Đang tách batch BEGIN/END khỏi runtime riêng của phone...",
    "Đang phân loại thời gian theo USB, Trust, Vision, robot và device service...",
    "Đang xác định process có vượt 13 phút hay không...",
    "Đang xếp hạng nguyên nhân chạy lâu..."
  ];
  let index = 0;
  const timer = setInterval(() => { index = Math.min(index + 1, messages.length - 1); $("#loadingMessage").textContent = messages[index]; }, 3200);

  try {
    const data = await apiJson("/api/analyze-runtime", { method: "POST", body: form });
    state.runtimeLastResponse = data;
    renderRuntimeResult(data);
    status.textContent = data.analysis?.is_slow ? "Hoàn tất: process được xác định là chậm." : "Hoàn tất: process chưa vượt ngưỡng chậm.";
    status.className = "analysis-status success";
    toast("Phân tích runtime hoàn tất", `Runtime case #${data.meta?.runtime_database?.saved_case_id || "-"} đã lưu vào SQLite.`, "success");
    await Promise.all([loadStats(), loadRuntimeHistory()]);
  } catch (error) {
    status.textContent = `Lỗi: ${error.message}`;
    status.className = "analysis-status error";
    toast("Phân tích runtime thất bại", error.message, "error");
  } finally {
    clearInterval(timer);
    setLoading(false);
  }
}

function renderRuntimeResult(data) {
  const analysis = data.analysis || {};
  const meta = data.meta || {};
  $("#runtimeResultShell").classList.remove("hidden");
  const selectedPortLabel = meta.selected_port_label || (meta.selected_port ? runtimePortLabel(meta.selected_port) : "");
  $("#runtimeResultTitle").textContent = analysis.process_label || `${selectedPortLabel || "Process"} · #${analysis.primary_process_index || "-"}`;
  $("#runtimeClassification").innerHTML = runtimeBadge(analysis.classification);
  $("#runtimeTotal").textContent = analysis.total_duration_text || "-";
  $("#runtimeBatchTotal").textContent = analysis.batch_duration_text && analysis.batch_duration_text !== "-" ? analysis.batch_duration_text : "Không xác định";
  $("#runtimeThresholdResult").textContent = `> ${analysis.threshold_minutes || 13} phút`;
  $("#runtimeOver").textContent = analysis.is_slow ? `+ ${analysis.over_threshold_text || "-"}` : "Không vượt";
  $("#runtimeConfidence").textContent = `${analysis.confidence_score || 0}%`;
  $("#runtimeSummary").textContent = analysis.executive_summary || "";
  $("#runtimeReason").textContent = analysis.slow_reason_summary || "";

  $("#runtimePlainConclusion").textContent = analysis.plain_language_conclusion || analysis.slow_reason_summary || "Chưa đủ dữ liệu để kết luận nguyên nhân.";
  $("#runtimePlainConfidence").textContent = analysis.ai_used ? "Local + AI" : `Local · ${analysis.confidence_score || 0}%`;

  const diagnosisSequence = analysis.diagnosis_sequence || [];
  $("#runtimeDiagnosisSequence").innerHTML = diagnosisSequence.length ? diagnosisSequence.map((step, index) => `
    <div class="diagnosis-step"><span>${index + 1}</span><p>${escapeHtml(step)}</p></div>`).join("") : `<div class="empty-inline">Chưa dựng được chuỗi diễn biến.</div>`;

  const mainContributors = analysis.main_contributors || [];
  $("#runtimeMainContributors").innerHTML = mainContributors.length ? mainContributors.map(item => `
    <article class="plain-contributor-card">
      <div class="plain-contributor-rank">#${Number(item.rank || 0)}</div>
      <div class="plain-contributor-body">
        <div class="plain-contributor-title"><strong>${escapeHtml(item.title || "-")}</strong><span class="evidence-level ${escapeHtml(item.evidence_level || "inferred")}">${escapeHtml(item.evidence_level || "inferred")}</span></div>
        <p>${escapeHtml(item.explanation || "")}</p>
        <div class="plain-contributor-meta"><span>${Number(item.count || 0)} lần</span><span>${escapeHtml(item.observed_wait_text || "0 giây")}</span><span>${escapeHtml(item.test_item || "Không rõ test item")}</span></div>
        <small>${escapeHtml(item.impact_summary || "")}</small>
      </div>
    </article>`).join("") : `<div class="empty-inline">Không có contributor đủ rõ.</div>`;

  const notMain = analysis.not_main_contributors || [];
  $("#runtimeNotMainContributors").innerHTML = notMain.length ? notMain.map(item => `<p>• ${escapeHtml(item)}</p>`).join("") : `<p>Không có ghi chú.</p>`;
  const priorityChecks = analysis.priority_checks || analysis.recommended_checks || [];
  $("#runtimePriorityChecks").innerHTML = priorityChecks.length ? priorityChecks.map((item, index) => `<p><b>${index + 1}.</b> ${escapeHtml(item)}</p>`).join("") : `<p>Chưa có bước kiểm tra ưu tiên.</p>`;
  $("#runtimeCertaintyExplanation").textContent = analysis.certainty_explanation || "Marker trực tiếp và suy luận được hiển thị riêng.";
  $("#runtimeAiBadge").classList.toggle("hidden", !analysis.ai_used);
  renderStringList("runtimeChecks", analysis.recommended_checks || []);
  renderStringList("runtimeMissing", analysis.missing_logs_or_data || []);

  const timelineIntervals = data.timeline_intervals || [];
  const timelineChart = $("#runtimeTimelineChart");
  const timelineLegend = $("#runtimeTimelineLegend");
  if (timelineChart && timelineLegend) {
    if (timelineIntervals.length) {
      $("#runtimeTimelinePanel").classList.remove("hidden");
      const colors = {
        initialization: "#3b82f6",
        robot_z: "#8b7cf6",
        usb_connection: "#e08624",
        vision_ocr: "#22c7e8",
        functional_test: "#20c77a",
        device_service: "#e9528f",
        unknown: "#6f8195",
        gap: "#ef5b5b"
      };
      timelineChart.innerHTML = timelineIntervals.map(interval => {
        const color = colors[interval.stage] || "#94a3b8";
        const pct = Math.max(0.1, interval.percent_of_process);
        const title = `${interval.label}\nThời lượng: ${interval.duration_text} (${pct.toFixed(1)}%)\nBắt đầu: ${interval.start_time}\nKết thúc: ${interval.end_time}`;
        return `
          <div class="timeline-segment" 
               style="flex-grow: ${pct}; background-color: ${color}; cursor: pointer; position: relative; transition: filter 0.1s;"
               title="${escapeHtml(title)}"
               onmouseover="this.style.filter='brightness(1.2)'"
               onmouseout="this.style.filter='none'">
          </div>
        `;
      }).join("");
      const activeStages = [...new Set(timelineIntervals.map(i => i.stage))];
      timelineLegend.innerHTML = activeStages.map(stage => {
        const label = stage === "gap" ? "Khoảng chờ (Gap)" : (timelineIntervals.find(i => i.stage === stage)?.label || stage);
        const color = colors[stage] || "#94a3b8";
        return `
          <div style="display: flex; align-items: center; gap: 6px;">
            <i style="display: inline-block; width: 12px; height: 12px; border-radius: 3px; background-color: ${color};"></i>
            <span>${escapeHtml(label)}</span>
          </div>
        `;
      }).join("");
    } else {
      $("#runtimeTimelinePanel").classList.add("hidden");
    }
  }

  const stages = analysis.stage_breakdown || [];
  $("#runtimeStages").innerHTML = stages.length ? stages.map(stage => `
    <div class="runtime-stage-row"><div class="runtime-stage-head"><strong>${escapeHtml(stage.label)}</strong><span>${escapeHtml(stage.duration_text)} · ${Number(stage.percent_of_process || 0).toFixed(1)}%</span></div><div class="runtime-stage-track"><i style="width:${Math.min(100, Number(stage.percent_of_process || 0))}%"></i></div></div>`).join("") : `<div class="empty-inline">Không đủ event để chia stage.</div>`;

  $("#runtimeRootCauses").innerHTML = analysis.root_cause_candidates?.length ? analysis.root_cause_candidates.map(root => `
    <article class="root-cause-card"><span class="root-confidence ${escapeHtml(root.confidence)}">${escapeHtml(root.confidence)}</span><h4>${escapeHtml(root.cause)}</h4><p>${escapeHtml(root.reasoning || "")}</p></article>`).join("") : `<div class="empty-inline">Không có candidate.</div>`;

  renderModuleCounts("runtimeRetryModules", analysis.retry_by_module || {}, "Không phát hiện retry hoặc thao tác lặp.");
  renderModuleCounts("runtimeTimeoutModules", analysis.timeout_by_module || {}, "Không phát hiện timeout/wait đáng chú ý.");

  const retryGroups = analysis.retry_timeout_groups || [];
  $("#runtimeRetryTimeoutGroups").innerHTML = retryGroups.length ? retryGroups.map(group => `
    <tr>
      <td>${retryTimeoutBadge(group.event_type === "timeout" ? "timeout" : "retry", group.detection || "explicit")}</td>
      <td class="runtime-operation-cell"><strong>${escapeHtml(group.operation || "-")}</strong><span>${escapeHtml(group.execution_mode || "")}</span></td>
      <td>${escapeHtml(group.initiator || "-")}</td>
      <td><span class="module-chip">${escapeHtml(group.target_module || "-")}</span></td>
      <td>${escapeHtml(group.test_item || "-")}</td>
      <td><strong>${Number(group.count || 0)}</strong><span class="cell-subtitle">#${(group.occurrence_indexes || []).join(", #")}</span></td>
      <td>${escapeHtml(group.total_observed_wait_text || "-")}</td>
      <td class="runtime-explain-cell">${escapeHtml(group.explanation || "")}</td>
    </tr>`).join("") : `<tr><td colspan="8" class="empty-cell">Không phát hiện nhóm retry/timeout.</td></tr>`;

  const retryEvents = analysis.retry_timeout_events || [];
  $("#runtimeRetryTimeoutEvents").innerHTML = retryEvents.length ? retryEvents.map(item => {
    const waitParts = [];
    if (item.declared_timeout_text) waitParts.push(`Cấu hình: ${item.declared_timeout_text}`);
    if (item.observed_wait_text) waitParts.push(`Quan sát: ${item.observed_wait_text}`);
    const before = item.context_before?.length ? item.context_before[item.context_before.length - 1] : null;
    return `
      <tr>
        <td><strong>#${item.occurrence_index}</strong><span class="cell-subtitle">${escapeHtml(item.time || "-")}</span></td>
        <td>${retryTimeoutBadge(item.event_type, item.detection)}</td>
        <td class="runtime-operation-cell"><strong>${escapeHtml(item.what_was_happening || item.operation || "-")}</strong><span>${escapeHtml(item.stage_label || "")}</span></td>
        <td>${escapeHtml(item.initiator || "-")}<span class="cell-subtitle">Target: ${escapeHtml(item.target_module || "-")}</span></td>
        <td>${escapeHtml(item.execution_mode || "-")}</td>
        <td>${escapeHtml(item.test_item || "-")}${item.attempt_number ? `<span class="cell-subtitle">Attempt ${item.attempt_number}</span>` : ""}</td>
        <td>${waitParts.length ? waitParts.map(x => `<span class="wait-line">${escapeHtml(x)}</span>`).join("") : "-"}</td>
        <td class="runtime-explain-cell"><strong>Trigger:</strong> ${escapeHtml(item.likely_trigger || "-")}<br><strong>Impact:</strong> ${escapeHtml(item.impact || "-")}<span class="confidence-inline">Confidence: ${escapeHtml(item.confidence || "-")}</span></td>
        <td class="runtime-log-cell"><span>${escapeHtml(item.current_event?.file || "-")} L${item.current_event?.line_start || "-"}</span>${escapeHtml(item.current_event?.quote || "")}${before ? `<small>Trước đó: ${escapeHtml(before.quote || "")}</small>` : ""}</td>
      </tr>`;
  }).join("") : `<tr><td colspan="9" class="empty-cell">Không phát hiện retry/timeout hoặc thao tác lặp.</td></tr>`;

  const gaps = analysis.longest_gaps || [];
  $("#runtimeGaps").innerHTML = gaps.length ? gaps.map(gap => `
    <tr><td><strong>${escapeHtml(gap.duration_text)}</strong><span class="cell-subtitle">${escapeHtml(gap.start_time)} → ${escapeHtml(gap.end_time)}</span></td><td>${escapeHtml(gap.suspected_stage_label)}</td><td class="runtime-log-cell"><span>${escapeHtml(gap.before_event?.file || "-")} L${gap.before_event?.line_start || "-"}</span>${escapeHtml(gap.before_event?.quote || "")}</td><td class="runtime-log-cell"><span>${escapeHtml(gap.after_event?.file || "-")} L${gap.after_event?.line_start || "-"}</span>${escapeHtml(gap.after_event?.quote || "")}</td></tr>`).join("") : `<tr><td colspan="4" class="empty-cell">Không có gap lớn hơn ngưỡng cảnh báo.</td></tr>`;

  const processes = analysis.processes || [];
  $("#runtimeProcesses").innerHTML = processes.length ? processes.map(process => `
    <tr><td>#${process.process_index}</td><td><span class="module-chip">${escapeHtml(selectedPortLabel || "Không rõ")}</span></td><td>${runtimeBadge(process.classification)}</td><td><strong>${escapeHtml(process.total_duration_text)}</strong><span class="cell-subtitle">${escapeHtml(process.start_time)} → ${escapeHtml(process.end_time)}</span></td><td>${escapeHtml(process.result_status)}</td><td><strong>${process.retry_count}</strong><span class="cell-subtitle">${process.explicit_retry_count || 0} trực tiếp + ${process.inferred_repeated_action_count || 0} lặp</span></td><td><strong>${process.timeout_count}</strong><span class="cell-subtitle">${process.explicit_timeout_count || 0} trực tiếp + ${process.suspected_timeout_count || 0} nghi ngờ</span></td><td>${escapeHtml(process.boundary_source)}</td></tr>`).join("") : `<tr><td colspan="8" class="empty-cell">Không phát hiện transaction.</td></tr>`;
  $("#runtimeMetadata").textContent = JSON.stringify(meta, null, 2);
  $("#runtimeResultShell").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadRuntimeHistory() {
  const body = $("#runtimeHistoryBody");
  if (!body) return;
  body.innerHTML = `<tr><td colspan="8" class="empty-cell">Đang tải...</td></tr>`;
  try {
    const data = await apiJson("/api/runtime-cases?limit=50");
    const cases = data.cases || [];
    body.innerHTML = cases.length ? cases.map(item => `
      <tr><td class="id-cell">#${item.id}</td><td class="error-cell"><span class="cell-title">${escapeHtml(item.process_label || "Runtime process")}</span><span class="cell-subtitle">${escapeHtml(item.slow_reason_summary || "")}</span></td><td>${runtimeBadge(item.classification)}</td><td>${durationText(item.total_duration_seconds)}</td><td>${Number(item.over_threshold_seconds || 0) > 0 ? `+${durationText(item.over_threshold_seconds)}` : "-"}</td><td>${escapeHtml(sourceLabel(item.source_mode))}</td><td>${escapeHtml(formatDate(item.created_at))}</td><td><button class="row-action" data-open-runtime="${item.id}" title="Mở"><svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/></svg></button></td></tr>`).join("") : `<tr><td colspan="8" class="empty-cell">Chưa có runtime case.</td></tr>`;
    $$('[data-open-runtime]', body).forEach(button => button.addEventListener("click", () => openRuntimeHistory(Number(button.dataset.openRuntime))));
  } catch (error) {
    body.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function openRuntimeHistory(id) {
  try {
    const item = await apiJson(`/api/runtime-cases/${id}`);
    state.runtimeLastResponse = { analysis: item.analysis || {}, meta: item.meta || {} };
    renderRuntimeResult(state.runtimeLastResponse);
  } catch (error) {
    toast("Không mở được runtime case", error.message, "error");
  }
}

function exportRuntimeJson() {
  if (!state.runtimeLastResponse) { toast("Chưa có runtime result", "Hãy phân tích runtime trước."); return; }
  download("phonebot_runtime_analysis.json", JSON.stringify(state.runtimeLastResponse, null, 2), "application/json");
}

function exportRuntimeTxt() {
  const response = state.runtimeLastResponse;
  if (!response?.analysis) { toast("Chưa có runtime result", "Hãy phân tích runtime trước."); return; }
  const a = response.analysis;
  const text = `PHONEBOT RUNTIME ANALYSIS
=========================
Process: ${a.process_label || ""}
Classification: ${a.classification}
Threshold: > ${a.threshold_minutes} minutes
Phone runtime: ${a.total_duration_text}
Batch duration (reference only): ${a.batch_duration_text || "-"}
Timing scope: ${a.timing_scope || "-"}
Boundary: ${a.boundary_explanation || "-"}
Over threshold: ${a.over_threshold_text}
Confidence: ${a.confidence_score}%

SUMMARY
${a.executive_summary || ""}

PLAIN-LANGUAGE CONCLUSION
${a.plain_language_conclusion || a.slow_reason_summary || ""}

DIAGNOSIS SEQUENCE
${(a.diagnosis_sequence || []).map((x, i) => `${i + 1}. ${x}`).join("\n")}

MAIN CONTRIBUTORS
${(a.main_contributors || []).map(x => `- #${x.rank} ${x.title} | count=${x.count} | observed_wait=${x.observed_wait_text} | evidence=${x.evidence_level}\n  ${x.explanation}\n  ${x.impact_summary}`).join("\n")}

NOT MAIN CONTRIBUTORS
${(a.not_main_contributors || []).map(x => `- ${x}`).join("\n")}

PRIORITY CHECKS
${(a.priority_checks || []).map((x, i) => `${i + 1}. ${x}`).join("\n")}

CERTAINTY
${a.certainty_explanation || ""}

TECHNICAL WHY SLOW
${a.slow_reason_summary || ""}

ROOT CAUSES
${(a.root_cause_candidates || []).map(x => `- [${x.confidence}] ${x.cause}: ${x.reasoning}`).join("\n")}

RECOMMENDED CHECKS
${(a.recommended_checks || []).map(x => `- ${x}`).join("\n")}

RETRY / TIMEOUT BY OPERATION
${(a.retry_timeout_groups || []).map(x => `- [${x.event_type}] ${x.operation} | initiator=${x.initiator} | target=${x.target_module} | count=${x.count} | observed_wait=${x.total_observed_wait_text}`).join("\n")}

RETRY / TIMEOUT OCCURRENCES
${(a.retry_timeout_events || []).map(x => `- #${x.occurrence_index} ${x.time} [${x.event_type}/${x.detection}] ${x.what_was_happening} | initiator=${x.initiator} | target=${x.target_module} | mode=${x.execution_mode} | test=${x.test_item || "-"} | wait=${x.observed_wait_text || x.declared_timeout_text || "-"} | evidence=${x.current_event?.file || "-"} L${x.current_event?.line_start || "-"}`).join("\n")}

LONGEST GAPS
${(a.longest_gaps || []).map(x => `- ${x.duration_text} | ${x.suspected_stage_label} | ${x.start_time} -> ${x.end_time}`).join("\n")}
`;
  download("phonebot_runtime_analysis.txt", text, "text/plain;charset=utf-8");
}

// Interactive Log Viewer State
const logViewerState = {
  fileName: "",
  fileLines: [],
  highlightedLines: { start: 0, end: 0 },
  searchResults: [],
  currentSearchIndex: -1
};

async function openLogViewer(fileName, startLine = 0, endLine = 0) {
  const modal = $("#logViewerModal");
  if (!modal) return;
  
  $("#logViewerTitle").textContent = fileName;
  $("#logViewerSubtitle").textContent = startLine && endLine ? `Đang xem dòng ${startLine} đến ${endLine}` : "Đang xem toàn bộ file";
  $("#logViewerContent").innerHTML = `<div class="empty-inline"><div class="spinner"></div> Đang tải dữ liệu log...</div>`;
  modal.classList.remove("hidden");
  
  logViewerState.fileName = fileName;
  logViewerState.highlightedLines = { start: startLine, end: endLine };
  logViewerState.searchResults = [];
  logViewerState.currentSearchIndex = -1;
  $("#logViewerSearch").value = "";
  $("#logViewerMatchCount").textContent = "0 kết quả";
  $("#logViewerPrevMatch").disabled = true;
  $("#logViewerNextMatch").disabled = true;

  try {
    let logText = "";
    
    // 1. Try to find the file in memory from the uploaded list
    const uploadedFile = state.files.find(f => {
      const path = f.webkitRelativePath || f.name;
      return path.endsWith(fileName) || fileName.endsWith(f.name);
    });
    
    if (uploadedFile) {
      logText = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error("Lỗi khi đọc file local."));
        reader.readAsText(uploadedFile);
      });
    } else {
      // 2. Try to find in lastResponse meta or activeCase meta
      const metaFiles = (state.lastResponse?.meta?.files || state.activeCase?.meta?.files || []);
      const matchedMetaFile = metaFiles.find(f => f.name === fileName || f.name.endsWith(fileName) || fileName.endsWith(f.name));
      if (matchedMetaFile && matchedMetaFile.selected_text) {
        logText = matchedMetaFile.selected_text;
      }
    }
    
    if (!logText) {
      // 3. Fallback: check if the case is historical and has evidence excerpt
      const excerpt = state.activeCase?.evidence_excerpt || state.lastResponse?.meta?.deterministic_pre_scan || "";
      if (excerpt) {
        const fileRegex = new RegExp(`===== FILE: ${escapeRegExp(fileName)}[\\s\\S]+?=====(?:\\n|\\r\\n)([\\s\\S]+?)(?=\\n===== FILE:|$)`);
        const match = excerpt.match(fileRegex);
        if (match && match[1]) {
          logText = match[1];
        } else {
          logText = excerpt;
          $("#logViewerTitle").textContent = "Bằng chứng trích xuất từ SQLite";
        }
      }
    }

    if (!logText) {
      throw new Error("Không tìm thấy nội dung file. Hãy kéo thả thư mục log của case này vào trang web để xem log đầy đủ.");
    }
    
    renderLogLines(logText, startLine, endLine);
  } catch (err) {
    $("#logViewerContent").innerHTML = `<div class="empty-inline error-text">${escapeHtml(err.message)}</div>`;
  }
}

function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function renderLogLines(text, startLine, endLine) {
  const container = $("#logViewerContent");
  const rawLines = text.split(/\r?\n/);
  const parsedLines = [];
  
  rawLines.forEach((line, index) => {
    let lineNum = index + 1;
    let lineText = line;
    
    const numberedMatch = line.match(/^\[L(\d+)\] (.*)/);
    if (numberedMatch) {
      lineNum = parseInt(numberedMatch[1], 10);
      lineText = numberedMatch[2];
    }
    
    parsedLines.push({ num: lineNum, text: lineText });
  });
  
  logViewerState.fileLines = parsedLines;
  
  let html = "";
  parsedLines.forEach(line => {
    const isHighlighted = startLine && endLine && line.num >= startLine && line.num <= endLine;
    const highlightClass = isHighlighted ? " highlighted" : "";
    
    let colorClass = "";
    const lowerText = line.text.toLowerCase();
    if (lowerText.includes("fail") || lowerText.includes("error") || lowerText.includes("exception")) {
      colorClass = " error-line";
    } else if (lowerText.includes("success") || lowerText.includes("already trusted") || lowerText.includes("handshake succeeded")) {
      colorClass = " success-line";
    }
    
    html += `
      <div class="log-line${highlightClass}${colorClass}" data-line-num="${line.num}">
        <span class="log-line-num">${line.num}</span>
        <span class="log-line-text">${escapeHtml(line.text)}</span>
      </div>
    `;
  });
  
  container.innerHTML = html;
  
  if (startLine) {
    const highlightElem = container.querySelector(`[data-line-num="${startLine}"]`);
    if (highlightElem) {
      setTimeout(() => {
        highlightElem.scrollIntoView({ behavior: "auto", block: "center" });
      }, 80);
    }
  }
}

function performLogViewerSearch() {
  const query = $("#logViewerSearch").value.trim().toLowerCase();
  const container = $("#logViewerContent");
  const matches = [];
  
  container.querySelectorAll(".log-line").forEach(el => el.classList.remove("search-focused"));

  if (!query) {
    logViewerState.searchResults = [];
    logViewerState.currentSearchIndex = -1;
    $("#logViewerMatchCount").textContent = "0 kết quả";
    $("#logViewerPrevMatch").disabled = true;
    $("#logViewerNextMatch").disabled = true;
    return;
  }
  
  logViewerState.fileLines.forEach((line, index) => {
    if (line.text.toLowerCase().includes(query)) {
      matches.push({ index, num: line.num });
    }
  });
  
  logViewerState.searchResults = matches;
  $("#logViewerMatchCount").textContent = `${matches.length} kết quả`;
  
  if (matches.length > 0) {
    logViewerState.currentSearchIndex = 0;
    $("#logViewerPrevMatch").disabled = false;
    $("#logViewerNextMatch").disabled = false;
    highlightSearchMatch();
  } else {
    logViewerState.currentSearchIndex = -1;
    $("#logViewerPrevMatch").disabled = true;
    $("#logViewerNextMatch").disabled = true;
  }
}

function highlightSearchMatch() {
  const matches = logViewerState.searchResults;
  const currentIndex = logViewerState.currentSearchIndex;
  if (!matches || currentIndex < 0 || currentIndex >= matches.length) return;
  
  const match = matches[currentIndex];
  const container = $("#logViewerContent");
  
  container.querySelectorAll(".log-line").forEach(el => el.classList.remove("search-focused"));
  
  const matchElem = container.querySelector(`[data-line-num="${match.num}"]`);
  if (matchElem) {
    matchElem.classList.add("search-focused");
    matchElem.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  
  $("#logViewerMatchCount").textContent = `${currentIndex + 1} / ${matches.length} kết quả`;
}

function bindEvents() {
  $$(".nav-item[data-view]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
  $("#newAnalysisButton").addEventListener("click", () => switchView("analyze"));
  $("#menuButton").addEventListener("click", () => $("#appShell").classList.toggle("menu-open"));
  $("#refreshDashboardButton").addEventListener("click", loadDashboard);
  $("#refreshCasesButton").addEventListener("click", loadCases);
  $("#caseLimit").addEventListener("change", loadCases);

  let caseSearchTimer;
  $("#caseSearch").addEventListener("input", () => {
    clearTimeout(caseSearchTimer);
    caseSearchTimer = setTimeout(loadCases, 280);
  });
  $("#globalSearch").addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    $("#caseSearch").value = event.currentTarget.value;
    switchView("cases");
    loadCases();
  });

  $$('[data-config]').forEach(control => {
    const eventName = control.type === "checkbox" || control.tagName === "SELECT" ? "change" : "input";
    control.addEventListener(eventName, () => updateConfig(control.dataset.config, control));
  });
  $$(".key-toggle").forEach(button => button.addEventListener("click", () => {
    const input = button.parentElement.querySelector('[data-config="apiKey"]');
    const show = input.type === "password";
    $$('[data-config="apiKey"]').forEach(element => { element.type = show ? "text" : "password"; });
  }));

  $("#chooseFolderButton").addEventListener("click", () => $("#folderInput").click());
  $("#chooseFilesButton").addEventListener("click", () => $("#filesInput").click());
  $("#folderInput").addEventListener("change", event => mergeFiles(event.target.files));
  $("#filesInput").addEventListener("change", event => mergeFiles(event.target.files));
  $("#clearFilesButton").addEventListener("click", () => { state.files = []; renderFiles(); });
  const dropZone = $("#dropZone");
  ["dragenter", "dragover"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(name => dropZone.addEventListener(name, event => { event.preventDefault(); dropZone.classList.remove("drag"); }));
  dropZone.addEventListener("drop", event => mergeFiles(event.dataTransfer.files));

  $("#analyzeButton").addEventListener("click", analyzeCase);
  $("#resetAnalysisButton").addEventListener("click", resetAnalysis);
  $("#resultTabs").addEventListener("click", event => {
    const tab = event.target.closest("[data-tab]");
    if (!tab) return;
    $$(".tab", $("#resultTabs")).forEach(item => item.classList.toggle("active", item === tab));
    $$(".tab-content", $("#resultShell")).forEach(content => content.classList.toggle("active", content.id === `tab-${tab.dataset.tab}`));
  });

  $("#exportJsonButton").addEventListener("click", exportJson);
  $("#exportTxtButton").addEventListener("click", exportTxt);
  $("#topExportButton").addEventListener("click", exportTxt);
  $("#reportExportJson").addEventListener("click", exportJson);
  $("#reportExportTxt").addEventListener("click", exportTxt);

  $("#runtimeChooseFolderButton")?.addEventListener("click", () => $("#runtimeFolderInput").click());
  $("#runtimeChooseFilesButton")?.addEventListener("click", () => $("#runtimeFilesInput").click());
  $("#runtimeFolderInput")?.addEventListener("change", event => mergeRuntimeFiles(event.target.files));
  $("#runtimeFilesInput")?.addEventListener("change", event => mergeRuntimeFiles(event.target.files));
  $("#runtimeClearFilesButton")?.addEventListener("click", () => { state.runtimeFiles = []; state.runtimeSelectedPort = ""; renderRuntimeFiles(); refreshRuntimePortOptions(); });
  $("#runtimePortSelect")?.addEventListener("change", event => { state.runtimeSelectedPort = event.target.value; });
  const runtimeDrop = $("#runtimeDropZone");
  if (runtimeDrop) {
    ["dragenter", "dragover"].forEach(name => runtimeDrop.addEventListener(name, event => { event.preventDefault(); runtimeDrop.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(name => runtimeDrop.addEventListener(name, event => { event.preventDefault(); runtimeDrop.classList.remove("drag"); }));
    runtimeDrop.addEventListener("drop", event => mergeRuntimeFiles(event.dataTransfer.files));
  }
  $("#runtimeUseAi")?.addEventListener("change", updateRuntimeAiConfig);
  $("#runtimeAnalyzeButton")?.addEventListener("click", analyzeRuntimeCase);
  $("#runtimeResetButton")?.addEventListener("click", resetRuntimeAnalysis);
  $("#runtimeRefreshHistory")?.addEventListener("click", loadRuntimeHistory);
  $("#runtimeExportJson")?.addEventListener("click", exportRuntimeJson);
  $("#runtimeExportTxt")?.addEventListener("click", exportRuntimeTxt);

  // Bindings for Interactive Log Viewer
  document.addEventListener("click", event => {
    const evidenceCard = event.target.closest(".clickable-evidence");
    if (evidenceCard) {
      const file = evidenceCard.dataset.file;
      const start = parseInt(evidenceCard.dataset.start, 10);
      const end = parseInt(evidenceCard.dataset.end, 10);
      if (file) {
        openLogViewer(file, start, end);
        return;
      }
    }
    
    const chip = event.target.closest(".interactive-evidence-chip");
    if (chip) {
      const file = chip.dataset.file;
      const start = parseInt(chip.dataset.start, 10);
      const end = parseInt(chip.dataset.end, 10);
      if (file) {
        openLogViewer(file, start, end);
        return;
      }
    }

    const fileChip = event.target.closest(".interactive-file-chip") || event.target.closest(".file-chip");
    if (fileChip) {
      const file = fileChip.dataset.file || fileChip.textContent.trim();
      if (file && !fileChip.closest(".selected-files-header") && !fileChip.closest(".runtime-rule-banner")) {
        openLogViewer(file);
        return;
      }
    }
  });

  $("#closeLogViewerButton")?.addEventListener("click", () => {
    $("#logViewerModal")?.classList.add("hidden");
  });
  
  $("#logViewerModal")?.addEventListener("click", event => {
    if (event.target === $("#logViewerModal")) {
      $("#logViewerModal").classList.add("hidden");
    }
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      $("#logViewerModal")?.classList.add("hidden");
    }
  });

  $("#logViewerSearch")?.addEventListener("input", performLogViewerSearch);
  $("#logViewerSearch")?.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      performLogViewerSearch();
    }
  });

  $("#logViewerPrevMatch")?.addEventListener("click", () => {
    if (logViewerState.searchResults.length === 0) return;
    logViewerState.currentSearchIndex = (logViewerState.currentSearchIndex - 1 + logViewerState.searchResults.length) % logViewerState.searchResults.length;
    highlightSearchMatch();
  });

  $("#logViewerNextMatch")?.addEventListener("click", () => {
    if (logViewerState.searchResults.length === 0) return;
    logViewerState.currentSearchIndex = (logViewerState.currentSearchIndex + 1) % logViewerState.searchResults.length;
    highlightSearchMatch();
  });

  $("#sendCopilotBtn")?.addEventListener("click", sendCopilotMessage);
  $("#copilotInput")?.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendCopilotMessage();
    }
  });
  $("#clearCopilotChat")?.addEventListener("click", () => {
    state.copilotChat = [];
    renderCopilotChat();
  });
}

// ==========================================
// UPH Calculator Implementation
// ==========================================
let uphMachineCounter = 0;
let uphInitialized = false;

function initUphCalculator() {
  if (uphInitialized) return;
  uphInitialized = true;

  $("#uphAddMachineBtn")?.addEventListener("click", () => addUphMachine());
  $("#uphExportBtn")?.addEventListener("click", exportUphExcel);
  $("#uphClearBtn")?.addEventListener("click", () => {
    if (window.confirm("Xóa toàn bộ dữ liệu máy và transaction?")) {
      const list = $("#uphMachineList");
      if (list) list.innerHTML = "";
      const rows = $("#uphResultRows");
      if (rows) rows.innerHTML = "";
      uphMachineCounter = 0;
      localStorage.removeItem("pb_uph_v6_excel");
      addUphMachine();
      toast("Đã xóa dữ liệu", "Đã xóa toàn bộ dữ liệu UPH.", "info");
    }
  });

  if (!loadUphState()) {
    addUphMachine();
  }
}

function addUphMachine(data = {}) {
  uphMachineCounter++;
  const card = document.createElement("article");
  card.className = "uph-machine-card";
  card.dataset.machineId = String(uphMachineCounter);
  card.innerHTML = `
    <div class="uph-machine-header">
      <strong class="uph-machine-title">${escapeHtml(data.machine || `Machine ${uphMachineCounter}`)}</strong>
      <button class="button ghost compact danger-button uph-delete-btn" type="button">Xóa</button>
    </div>
    <div class="uph-machine-body">
      <div class="uph-fields">
        <div class="uph-field-full">
          <label class="field-label">Machine No.</label>
          <input class="control uph-machine-name" value="${escapeHtml(data.machine || "")}" placeholder="AIZ_TN_PB48_01">
        </div>
        <div>
          <label class="field-label">Total Phone</label>
          <input class="control uph-total-phone" type="number" min="0" step="1" value="${data.total ?? ""}">
        </div>
        <div>
          <label class="field-label">Process Finished</label>
          <input class="control uph-finished" type="number" min="0" step="1" value="${data.finished ?? ""}">
        </div>
        <div>
          <label class="field-label">Device Errors</label>
          <input class="control uph-device-errors" type="number" min="0" step="1" value="${data.deviceErrors ?? 0}">
        </div>
        <div>
          <label class="field-label">Note</label>
          <input class="control uph-note" value="${escapeHtml(data.note || "")}" placeholder="UPH Android phone">
        </div>
      </div>
      <div class="uph-paste-area">
        <label class="field-label">Dán hai cột Start Time và End Time</label>
        <textarea class="control textarea uph-transaction-text" placeholder="22 Jul 2026 09:14:29&#9;22 Jul 2026 09:27:27&#10;22 Jul 2026 09:14:29&#9;22 Jul 2026 09:26:50">${escapeHtml(data.transactions || "")}</textarea>
        <p class="field-help">Copy trực tiếp hai cột từ Google Sheets. Mỗi dòng là một transaction; các dòng chạy cùng lúc được tính song song.</p>
      </div>
      <div class="uph-auto-result">
        <div class="uph-metric"><span>Transactions</span><strong class="uph-transaction-count">0</strong></div>
        <div class="uph-metric"><span>Processing Time</span><strong class="uph-processing-time">0:00:00</strong></div>
        <div class="uph-metric"><span>Processing Hours</span><strong class="uph-processing-hours">0.00</strong></div>
        <div class="uph-metric"><span>All Gaps</span><strong class="uph-all-gaps">0:00:00</strong></div>
        <div class="uph-metric"><span>Idle Over 60s</span><strong class="uph-idle-60">0:00:00</strong></div>
        <div class="uph-metric highlight"><span>Processing-only UPH</span><strong class="uph-processing-uph">-</strong></div>
        <div class="uph-metric highlight"><span>UPH with 60s Max</span><strong class="uph-uph-60">-</strong></div>
      </div>
      <div class="uph-machine-message"></div>
    </div>
  `;

  card.querySelectorAll("input,textarea").forEach(el => el.addEventListener("input", () => {
    updateUphMachine(card);
    saveUphState();
  }));

  card.querySelector(".uph-delete-btn").addEventListener("click", () => {
    card.remove();
    updateAllUphResults();
    saveUphState();
  });

  const list = $("#uphMachineList");
  if (list) {
    list.appendChild(card);
    updateUphMachine(card);
  }
}

function getUphMachineInput(card) {
  return {
    machine: card.querySelector(".uph-machine-name").value.trim(),
    total: Number(card.querySelector(".uph-total-phone").value || 0),
    finished: Number(card.querySelector(".uph-finished").value || 0),
    deviceErrors: Number(card.querySelector(".uph-device-errors").value || 0),
    note: card.querySelector(".uph-note").value.trim(),
    transactions: card.querySelector(".uph-transaction-text").value
  };
}

function setUphMachineMessage(card, text = "", type = "") {
  const box = card.querySelector(".uph-machine-message");
  if (box) {
    box.textContent = text;
    box.className = type ? `uph-machine-message ${type}` : "uph-machine-message";
  }
}

function updateUphMachine(card) {
  const input = getUphMachineInput(card);
  card.querySelector(".uph-machine-title").textContent = input.machine || `Machine ${card.dataset.machineId}`;
  try {
    if ([input.total, input.finished, input.deviceErrors].some(v => !Number.isFinite(v) || v < 0 || !Number.isInteger(v))) {
      throw new Error("Total Phone, Process Finished và Device Errors phải là số nguyên không âm.");
    }
    if (input.finished > input.total) {
      throw new Error("Process Finished không được lớn hơn Total Phone.");
    }
    if (input.finished + input.deviceErrors > input.total && input.total > 0) {
      throw new Error("Process Finished + Device Errors không được lớn hơn Total Phone.");
    }
    const c = uphAnalyseTransactions(input.transactions);
    const counted = input.finished + input.deviceErrors;
    const denom1 = c.processingOnlyHours;
    const denom2 = c.effective60Hours;
    const uph1 = denom1 > 0 ? counted / denom1 : NaN;
    const uph2 = denom2 > 0 ? counted / denom2 : NaN;
    
    Object.assign(card.dataset, {
      valid: "true",
      processingSeconds: String(c.processing),
      allGapsSeconds: String(c.allGaps),
      idle60Seconds: String(c.idle60),
      processingHours: String(c.processingHours),
      allGapsHours: String(c.allGapsHours),
      idle60Hours: String(c.idle60Hours),
      processingOnlyHours: String(denom1),
      effective60Hours: String(denom2),
      countedDevices: String(counted)
    });

    card.querySelector(".uph-transaction-count").textContent = c.count;
    card.querySelector(".uph-processing-time").textContent = uphFormatDuration(c.processing);
    card.querySelector(".uph-processing-hours").textContent = c.processingHours.toFixed(2);
    card.querySelector(".uph-all-gaps").textContent = `${uphFormatDuration(c.allGaps)} (${c.allGapsHours.toFixed(2)}h)`;
    card.querySelector(".uph-idle-60").textContent = `${uphFormatDuration(c.idle60)} (${c.idle60Hours.toFixed(2)}h)`;
    card.querySelector(".uph-processing-uph").textContent = uphFormatNumber(uph1, 1);
    card.querySelector(".uph-uph-60").textContent = uphFormatNumber(uph2, 1);
    
    if (c.count > 0) {
      setUphMachineMessage(card, `Đã tính theo ${c.count} dòng Start–End và quy tắc làm tròn 2 chữ số giờ.`, "success");
    } else {
      setUphMachineMessage(card);
    }
  } catch (e) {
    Object.assign(card.dataset, {
      valid: "false",
      processingOnlyHours: "0",
      effective60Hours: "0",
      countedDevices: "0"
    });
    for (const s of [".uph-transaction-count", ".uph-processing-time", ".uph-processing-hours", ".uph-all-gaps", ".uph-idle-60"]) {
      card.querySelector(s).textContent = s === ".uph-transaction-count" ? "0" : s === ".uph-processing-hours" ? "0.00" : "0:00:00";
    }
    card.querySelector(".uph-processing-uph").textContent = "-";
    card.querySelector(".uph-uph-60").textContent = "-";
    setUphMachineMessage(card, e.message, "error");
  }
  updateAllUphResults();
}

function collectUphReportRows() {
  const rows = [];
  const list = $("#uphMachineList");
  if (!list) return rows;
  for (const card of list.querySelectorAll(".uph-machine-card")) {
    if (card.dataset.valid !== "true") continue;
    const i = getUphMachineInput(card);
    const has = i.machine || i.total || i.finished || i.deviceErrors || i.transactions.trim() || i.note;
    if (!has) continue;
    const counted = i.finished + i.deviceErrors;
    const ph = Number(card.dataset.processingHours || 0);
    const gh = Number(card.dataset.allGapsHours || 0);
    const ih = Number(card.dataset.idle60Hours || 0);
    const d1 = ph - gh;
    const d2 = ph - ih;
    rows.push({
      ...i,
      counted,
      processingSeconds: Number(card.dataset.processingSeconds || 0),
      allGapsSeconds: Number(card.dataset.allGapsSeconds || 0),
      idle60Seconds: Number(card.dataset.idle60Seconds || 0),
      processingHours: ph,
      allGapsHours: gh,
      idle60Hours: ih,
      processingOnlyHours: d1,
      effective60Hours: d2,
      productionPct: i.total > 0 ? i.finished / i.total : 0,
      processingUPH: d1 > 0 ? counted / d1 : NaN,
      uph60: d2 > 0 ? counted / d2 : NaN
    });
  }
  return rows;
}

function updateAllUphResults() {
  const rowsBody = $("#uphResultRows");
  if (!rowsBody) return;
  rowsBody.innerHTML = "";
  const rows = collectUphReportRows();
  let total = 0, finished = 0, counted = 0, den1 = 0, den2 = 0;
  for (const r of rows) {
    total += r.total;
    finished += r.finished;
    counted += r.counted;
    if (r.processingOnlyHours > 0) den1 += r.processingOnlyHours;
    if (r.effective60Hours > 0) den2 += r.effective60Hours;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(r.machine || "-")}</td>
      <td>${r.total}</td>
      <td>${r.finished}</td>
      <td style="color: var(--green); font-weight: 700;">${uphFormatNumber(r.productionPct * 100, 1)}%</td>
      <td style="color: var(--cyan); font-weight: 800;">${uphFormatNumber(r.processingUPH, 1)}</td>
      <td style="color: var(--cyan); font-weight: 800;">${uphFormatNumber(r.uph60, 1)}</td>
      <td>${escapeHtml(r.note)}</td>
    `;
    rowsBody.appendChild(tr);
  }
  if (rows.length) {
    const tr = document.createElement("tr");
    tr.className = "summary-row";
    tr.style.fontWeight = "bold";
    tr.style.background = "var(--panel-2)";
    tr.style.borderTop = "2px dashed var(--line)";
    tr.innerHTML = `
      <td>All Machines</td>
      <td>${total}</td>
      <td>${finished}</td>
      <td style="color: var(--green); font-weight: 700;">${uphFormatNumber(total > 0 ? finished / total * 100 : 0, 1)}%</td>
      <td style="color: var(--cyan); font-weight: 800;">${uphFormatNumber(den1 > 0 ? counted / den1 : NaN, 1)}</td>
      <td style="color: var(--cyan); font-weight: 800;">${uphFormatNumber(den2 > 0 ? counted / den2 : NaN, 1)}</td>
      <td></td>
    `;
    rowsBody.appendChild(tr);
  }
}

function saveUphState() {
  const list = $("#uphMachineList");
  if (!list) return;
  const data = [...list.querySelectorAll(".uph-machine-card")].map(c => {
    const i = getUphMachineInput(c);
    return {
      machine: i.machine,
      total: c.querySelector(".uph-total-phone").value,
      finished: c.querySelector(".uph-finished").value,
      deviceErrors: c.querySelector(".uph-device-errors").value,
      note: i.note,
      transactions: i.transactions
    };
  });
  localStorage.setItem("pb_uph_v6_excel", JSON.stringify(data));
}

function loadUphState() {
  const raw = localStorage.getItem("pb_uph_v6_excel");
  if (!raw) return false;
  try {
    const data = JSON.parse(raw);
    if (!Array.isArray(data) || !data.length) return false;
    const list = $("#uphMachineList");
    if (list) list.innerHTML = "";
    data.forEach(item => addUphMachine(item));
    return true;
  } catch {
    return false;
  }
}

// ZIP and Excel Binary Generator Helpers
function uphRound2(v){return Math.round((v+Number.EPSILON)*100)/100}
function uphFormatNumber(v,d=1){if(!Number.isFinite(v))return "-";return new Intl.NumberFormat("en-US",{minimumFractionDigits:d,maximumFractionDigits:d}).format(v)}
function uphFormatDuration(seconds){if(!Number.isFinite(seconds)||seconds<0)return"0:00:00";const t=Math.round(seconds),h=Math.floor(t/3600),m=Math.floor((t%3600)/60),s=t%60;return `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`}
function uphParseDateTime(text){
  const value=text.trim().replace(/\s+/g," ");
  let m=value.match(/^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if(m){
    const[,y,mo,d,h,mi,s="0"]=m;
    return new Date(+y,+mo-1,+d,+h,+mi,+s);
  }
  m=value.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if(m){
    const[,d,mo,y,h,mi,s="0"]=m;
    return new Date(+y,+mo-1,+d,+h,+mi,+s);
  }
  m=value.match(/^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if(m){
    const map={jan:0,january:0,feb:1,february:1,mar:2,march:2,apr:3,april:3,may:4,jun:5,june:5,jul:6,july:6,aug:7,august:7,sep:8,sept:8,september:8,oct:9,october:9,nov:10,november:10,dec:11,december:11};
    const[,d,mt,y,h,mi,s="0"]=m,mo=map[mt.toLowerCase()];
    if(mo!==undefined)return new Date(+y,mo,+d,+h,+mi,+s);
  }
  const p=new Date(value);
  return Number.isNaN(p.getTime())?null:p;
}
function uphSplitTransactionLine(line){
  for(const sep of["\t","|",";"]){
    const p=line.split(sep);
    if(p.length>=2)return[p[0].trim(),p.slice(1).join(sep).trim()];
  }
  const p=line.trim().split(/\s{2,}/);
  return p.length>=2?[p[0].trim(),p.slice(1).join(" ").trim()]:null;
}
function uphAnalyseTransactions(text){
  const lines=text.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
  if(!lines.length)return{count:0,processing:0,allGaps:0,idle60:0,processingHours:0,allGapsHours:0,idle60Hours:0,processingOnlyHours:0,effective60Hours:0};
  const tx=lines.map((line,i)=>{
    const parts=uphSplitTransactionLine(line);
    if(!parts)throw new Error(`Dòng ${i+1}: không tách được Start và End.`);
    const start=uphParseDateTime(parts[0]),end=uphParseDateTime(parts[1]);
    if(!start||!end)throw new Error(`Dòng ${i+1}: timestamp không hợp lệ.`);
    if(end<=start)throw new Error(`Dòng ${i+1}: End phải sau Start.`);
    return{start,end};
  }).sort((a,b)=>(a.start-b.start)||(b.end-a.end));
  const first=tx[0].start;
  let latest=tx[0].end,allGaps=0,idle60=0;
  for(let i=1;i<tx.length;i++){
    const t=tx[i];
    if(t.start>latest){
      const gap=(t.start-latest)/1000;
      allGaps+=gap;
      idle60+=Math.max(0,gap-60);
    }
    if(t.end>latest)latest=t.end;
  }
  const processing=(latest-first)/1000;
  const processingHours=uphRound2(processing/3600);
  const allGapsHours=uphRound2(allGaps/3600);
  const idle60Hours=uphRound2(idle60/3600);
  return{
    count:tx.length,
    processing,
    allGaps,
    idle60,
    processingHours,
    allGapsHours,
    idle60Hours,
    processingOnlyHours:uphRound2(processingHours-allGapsHours),
    effective60Hours:uphRound2(processingHours-idle60Hours)
  };
}

function uphXmlEscape(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&apos;")}
function uphColName(n){let s="";while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s}
function uphTextCell(ref,value,style=0){return `<c r="${ref}" s="${style}" t="inlineStr"><is><t xml:space="preserve">${uphXmlEscape(value)}</t></is></c>`}
function uphNumCell(ref,value,style=0,formula=""){const v=Number.isFinite(value)?value:0;return `<c r="${ref}" s="${style}">${formula?`<f>${uphXmlEscape(formula)}</f>`:""}<v>${v}</v></c>`}
function uphCrc32(bytes){let c=0xffffffff;for(const b of bytes){c^=b;for(let k=0;k<8;k++)c=(c>>>1)^((c&1)?0xedb88320:0)}return(c^0xffffffff)>>>0}
function uphU16(v){return new Uint8Array([v&255,(v>>>8)&255])}
function uphU32(v){return new Uint8Array([v&255,(v>>>8)&255,(v>>>16)&255,(v>>>24)&255])}
function uphConcatArrays(arrays){const len=arrays.reduce((s,a)=>s+a.length,0),out=new Uint8Array(len);let o=0;for(const a of arrays){out.set(a,o);o+=a.length}return out}
function uphZipStore(files){
  const enc=new TextEncoder(),locals=[],centrals=[];let offset=0;
  const now=new Date(),dosTime=(now.getHours()<<11)|(now.getMinutes()<<5)|(now.getSeconds()>>1),dosDate=((now.getFullYear()-1980)<<9)|((now.getMonth()+1)<<5)|now.getDate();
  for(const file of files){
    const name=enc.encode(file.name),data=typeof file.data==="string"?enc.encode(file.data):file.data,crc=uphCrc32(data);
    const local=uphConcatArrays([uphU32(0x04034b50),uphU16(20),uphU16(0),uphU16(0),uphU16(dosTime),uphU16(dosDate),uphU32(crc),uphU32(data.length),uphU32(data.length),uphU16(name.length),uphU16(0),name,data]);
    locals.push(local);
    const central=uphConcatArrays([uphU32(0x02014b50),uphU16(20),uphU16(20),uphU16(0),uphU16(0),uphU16(dosTime),uphU16(dosDate),uphU32(crc),uphU32(data.length),uphU32(data.length),uphU16(name.length),uphU16(0),uphU16(0),uphU16(0),uphU16(0),uphU32(0),uphU32(offset),name]);
    centrals.push(central);
    offset+=local.length;
  }
  const centralData=uphConcatArrays(centrals),end=uphConcatArrays([uphU32(0x06054b50),uphU16(0),uphU16(0),uphU16(files.length),uphU16(files.length),uphU32(centralData.length),uphU32(offset),uphU16(0)]);
  return uphConcatArrays([...locals,centralData,end]);
}

function uphBuildExcelWorkbook(rows){
  const startRow=5,endRow=startRow+rows.length-1,summaryRow=endRow+1,generated=new Date().toLocaleString("vi-VN");
  let sheetRows=[];
  sheetRows.push(`<row r="1" ht="30" customHeight="1">${uphTextCell("A1","PB UPH REPORT",1)}</row>`);
  sheetRows.push(`<row r="2" ht="22" customHeight="1">${uphTextCell("A2",`Generated: ${generated} | Intermediate hours rounded to 2 decimals before UPH calculation`,2)}</row>`);
  sheetRows.push(`<row r="4" ht="34" customHeight="1">${["Machine No.","Total phone","Process finished","Production Pct. (%)","Processing-only UPH","UPH with 60s Max","Note"].map((h,i)=>uphTextCell(`${uphColName(i+1)}4`,h,3)).join("")}</row>`);
  rows.forEach((r,idx)=>{
    const rr=startRow+idx,alt=idx%2===0,base=alt?5:4;
    sheetRows.push(`<row r="${rr}" ht="23" customHeight="1">${uphTextCell(`A${rr}`,r.machine||"-",base)}${uphNumCell(`B${rr}`,r.total,base)}${uphNumCell(`C${rr}`,r.finished,base)}${uphNumCell(`D${rr}`,r.productionPct,6,`IFERROR(C${rr}/B${rr},0)`)}${uphNumCell(`E${rr}`,r.processingUPH,7,`IFERROR((C${rr}+H${rr})/(I${rr}-J${rr}),0)`)}${uphNumCell(`F${rr}`,r.uph60,7,`IFERROR((C${rr}+H${rr})/(I${rr}-K${rr}),0)`)}${uphTextCell(`G${rr}`,r.note||"",alt?9:8)}${uphNumCell(`H${rr}`,r.deviceErrors,0)}${uphNumCell(`I${rr}`,r.processingHours,0)}${uphNumCell(`J${rr}`,r.allGapsHours,0)}${uphNumCell(`K${rr}`,r.idle60Hours,0)}${uphTextCell(`L${rr}`,uphFormatDuration(r.processingSeconds),0)}${uphTextCell(`M${rr}`,uphFormatDuration(r.allGapsSeconds),0)}${uphTextCell(`N${rr}`,uphFormatDuration(r.idle60Seconds),0)}</row>`);
  });
  const total=rows.reduce((s,r)=>s+r.total,0),finished=rows.reduce((s,r)=>s+r.finished,0),errors=rows.reduce((s,r)=>s+r.deviceErrors,0),sumP=rows.reduce((s,r)=>s+r.processingHours,0),sumG=rows.reduce((s,r)=>s+r.allGapsHours,0),sumI=rows.reduce((s,r)=>s+r.idle60Hours,0),sumD1=sumP-sumG,sumD2=sumP-sumI,prod=total?finished/total:0,uph1=sumD1>0?(finished+errors)/sumD1:0,uph2=sumD2>0?(finished+errors)/sumD2:0;
  sheetRows.push(`<row r="${summaryRow}" ht="25" customHeight="1">${uphTextCell(`A${summaryRow}`,"All Machines",10)}${uphNumCell(`B${summaryRow}`,total,10,`SUM(B${startRow}:B${endRow})`)}${uphNumCell(`C${summaryRow}`,finished,10,`SUM(C${startRow}:C${endRow})`)}${uphNumCell(`D${summaryRow}`,prod,11,`IFERROR(C${summaryRow}/B${summaryRow},0)`)}${uphNumCell(`E${summaryRow}`,uph1,12,`IFERROR((C${summaryRow}+H${summaryRow})/(I${summaryRow}-J${summaryRow}),0)`)}${uphNumCell(`F${summaryRow}`,uph2,12,`IFERROR((C${summaryRow}+H${summaryRow})/(I${summaryRow}-K${summaryRow}),0)`)}${uphTextCell(`G${summaryRow}`,"",10)}${uphNumCell(`H${summaryRow}`,errors,10,`SUM(H${startRow}:H${endRow})`)}${uphNumCell(`I${summaryRow}`,sumP,10,`SUM(I${startRow}:I${endRow})`)}${uphNumCell(`J${summaryRow}`,sumG,10,`SUM(J${startRow}:J${endRow})`)}${uphNumCell(`K${summaryRow}`,sumI,10,`SUM(K${startRow}:K${endRow})`)}</row>`);
  const noteRow=summaryRow+2;
  sheetRows.push(`<row r="${noteRow}" ht="36" customHeight="1">${uphTextCell(`A${noteRow}`,"Calculation note: Processing Time, All Gaps and Idle Over 60s are converted to hours and rounded to 2 decimals before subtraction. UPH is displayed with 1 decimal.",13)}</row>`);
  const sheetXml=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension ref="A1:N${noteRow}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="4" topLeftCell="A5" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/><cols><col min="1" max="1" width="24" customWidth="1"/><col min="2" max="3" width="15" customWidth="1"/><col min="4" max="4" width="19" customWidth="1"/><col min="5" max="6" width="21" customWidth="1"/><col min="7" max="7" width="28" customWidth="1"/><col min="8" max="14" hidden="1" width="12" customWidth="1"/></cols><sheetData>${sheetRows.join("")}</sheetData><autoFilter ref="A4:G${summaryRow}"/><mergeCells count="3"><mergeCell ref="A1:G1"/><mergeCell ref="A2:G2"/><mergeCell ref="A${noteRow}:G${noteRow}"/></mergeCells><pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/><pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>`;
  const styles=`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="0.0%"/><numFmt numFmtId="165" formatCode="0.0"/></numFmts><fonts count="5"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="20"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FF07343D"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font><font><i/><sz val="10"/><color rgb="FF7A2E0E"/><name val="Calibri"/></font></fonts><fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF075D75"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD8FBFC"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF16D8DF"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF2F4F7"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="3"><border><left/><right/><top/><bottom/><diagonal/></border><border><left style="thin"><color rgb="FF8D99A9"/></left><right style="thin"><color rgb="FF8D99A9"/></right><top style="thin"><color rgb="FF8D99A9"/></top><bottom style="thin"><color rgb="FF8D99A9"/></bottom><diagonal/></border><border><left style="thin"><color rgb="FF606B78"/></left><right style="thin"><color rgb="FF606B78"/></right><top style="medium"><color rgb="FF606B78"/></top><bottom style="thin"><color rgb="FF606B78"/></bottom><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="14"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="2" fillId="3" borderId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="2" fillId="4" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="5" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="164" fontId="3" fillId="5" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="165" fontId="3" fillId="5" borderId="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="0" fillId="5" borderId="1" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf><xf numFmtId="0" fontId="3" fillId="6" borderId="2" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="164" fontId="3" fillId="6" borderId="2" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="165" fontId="3" fillId="6" borderId="2" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf><xf numFmtId="0" fontId="4" fillId="0" borderId="0" applyAlignment="1"><alignment horizontal="left" vertical="center" wrapText="1"/></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`;
  const files=[
    {name:"[Content_Types].xml",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>`},
    {name:"_rels/.rels",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>`},
    {name:"docProps/app.xml",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>PB UPH Calculator</Application></Properties>`},
    {name:"docProps/core.xml",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>PB UPH Report</dc:title><dc:creator>PB UPH Calculator</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">${new Date().toISOString()}</dcterms:created></cp:coreProperties>`},
    {name:"xl/workbook.xml",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="UPH Report" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>`},
    {name:"xl/_rels/workbook.xml.rels",data:`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`},
    {name:"xl/styles.xml",data:styles},
    {name:"xl/worksheets/sheet1.xml",data:sheetXml}
  ];
  return new Blob([uphZipStore(files)],{type:"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});
}

function exportUphExcel(){
  const rows=collectUphReportRows();
  if(!rows.length){
    toast("Chưa có dữ liệu", "Chưa có Machine hợp lệ để xuất báo cáo.", "error");
    return;
  }
  const invalid=[...document.querySelectorAll("#uphMachineList .uph-machine-card")].some(c=>c.dataset.valid==="false");
  if(invalid){
    toast("Lỗi dữ liệu", "Có Machine đang lỗi dữ liệu. Hãy sửa trước khi xuất.", "error");
    return;
  }
  const blob=uphBuildExcelWorkbook(rows),url=URL.createObjectURL(blob),a=document.createElement("a"),d=new Date(),stamp=`${d.getFullYear()}${String(d.getMonth()+1).padStart(2,"0")}${String(d.getDate()).padStart(2,"0")}_${String(d.getHours()).padStart(2,"0")}${String(d.getMinutes()).padStart(2,"0")}`;
  a.href=url;
  a.download=`PB_UPH_Report_${stamp}.xlsx`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast("Xuất thành công", "Đã xuất báo cáo Excel.", "success");
}

// ==========================================
// Theme (Dark/Light) Switcher Implementation
// ==========================================
function initTheme() {
  const savedTheme = localStorage.getItem("phonebot_theme") || "dark";
  applyTheme(savedTheme);

  $("#themeBtnDark")?.addEventListener("click", () => {
    applyTheme("dark");
    localStorage.setItem("phonebot_theme", "dark");
  });

  $("#themeBtnLight")?.addEventListener("click", () => {
    applyTheme("light");
    localStorage.setItem("phonebot_theme", "light");
  });
}

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.classList.add("light-theme");
    $("#themeBtnLight")?.classList.add("active");
    $("#themeBtnDark")?.classList.remove("active");
  } else {
    document.documentElement.classList.remove("light-theme");
    $("#themeBtnDark")?.classList.add("active");
    $("#themeBtnLight")?.classList.remove("active");
  }
}

async function init() {
  initTheme();
  bindEvents();
  syncConfigControls();
  renderFiles();
  renderRuntimeFiles();
  refreshRuntimePortOptions();
  updateRuntimeAiConfig();
  updateExportButtons();
  await checkHealth();
  await loadDashboard();
}

init();
