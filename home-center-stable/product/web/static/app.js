"use strict";

const state = { overview: null, profile: null, backups: [], audit: [], tls: null, authProviders: null, session: null, currentView: "overview" };
const LOGIN_ERROR_MESSAGES = Object.freeze({
  invalid_credentials: "Неверное имя пользователя или пароль.",
  too_many_requests: "Слишком много попыток входа. Повторите позже.",
  unavailable: "Сервис входа временно недоступен.",
});
const API_PATHS = Object.freeze({
  authProviders: "/api/v1/auth/providers",
  session: "/api/v1/session",
  logout: "/api/v1/session/logout",
  passwordChange: "/api/v1/auth/local-admin/password/change",
  overview: "/api/v1/overview",
  deploymentProfile: "/api/v1/deployment-profile",
  backups: "/api/v1/backups",
  audit: "/api/v1/audit?limit=100",
  tls: "/api/v1/tls",
});
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function formatBytes(value) {
  if (!Number.isFinite(value)) return "—";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  let number = value;
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number.toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "medium" }).format(date);
}

function shortHash(value) {
  return typeof value === "string" && value.length > 20 ? `${value.slice(0, 20)}…` : (value || "—");
}

function displayText(value, fallback = "—") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function requireDocument(value, schema) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.schema !== schema) {
    throw new Error("api_contract_mismatch");
  }
  return value;
}

function requireInfrastructureOverview(value) {
  const overview = requireDocument(value, "home-center.overview.v1");
  if (
    !overview.cluster || typeof overview.cluster !== "object" || Array.isArray(overview.cluster)
    || !Number.isInteger(overview.cluster.expected_nodes) || overview.cluster.expected_nodes < 1
    || overview.cluster.expected_nodes > 64
    || !Array.isArray(overview.nodes) || overview.nodes.length > 64 || !Array.isArray(overview.jobs)
    || overview.nodes.some((item) => (
      !item || typeof item !== "object" || Array.isArray(item)
      || typeof item.name !== "string" || typeof item.role !== "string"
      || typeof item.address !== "string" || typeof item.status !== "string"
    ))
  ) {
    throw new Error("api_contract_mismatch");
  }
  return overview;
}

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", headers: { "Accept": "application/json", ...(options.headers || {}) }, ...options });
  if (response.status === 401) { showLogin(); throw new Error("authentication_required"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}

async function loadAuthProviders() {
  const providerInput = $("#providerInput");
  const adOption = $("#adProviderOption");
  let adEnabled = false;
  try {
    const response = await fetch(API_PATHS.authProviders, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" },
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.schema !== "home-center.auth-providers.v1" || !Array.isArray(data.providers)) {
      throw new Error("auth_provider_catalog_unavailable");
    }
    state.authProviders = data.providers;
    adEnabled = data.providers.some((item) => item?.id === "ad" && item.enabled === true);
  } catch (_) {
    state.authProviders = null;
  }
  adOption.disabled = !adEnabled;
  adOption.hidden = !adEnabled;
  if (!adEnabled) providerInput.value = "local";
}

async function optionalApi(path) {
  try { return await api(path); }
  catch (error) {
    if (error.message === "authentication_required") throw error;
    return null;
  }
}

function showLogin() {
  $("#loginLayer").hidden = false;
  $("#loginError").classList.remove("success");
  $("#loginError").textContent = "";
  window.setTimeout(() => $("#usernameInput").focus(), 0);
}

function hideLogin() {
  $("#loginLayer").hidden = true;
  $("#loginError").classList.remove("success");
  $("#loginError").textContent = "";
  $("#loginForm").reset();
}

async function authenticate(provider, username, password) {
  try {
    const response = await fetch(API_PATHS.session, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ provider, username, password }),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (response.ok) return;
    if (response.status === 401) throw new Error("invalid_credentials");
    if (response.status === 429) throw new Error("too_many_requests");
    throw new Error("unavailable");
  } catch (error) {
    if (LOGIN_ERROR_MESSAGES[error.message]) throw error;
    throw new Error("unavailable");
  }
}

async function login(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const provider = $("#providerInput").value;
  const username = $("#usernameInput").value;
  const passwordInput = $("#passwordInput");
  const password = passwordInput.value;
  button.disabled = true;
  $("#loginError").classList.remove("success");
  $("#loginError").textContent = "";
  try {
    await authenticate(provider, username, password);
    passwordInput.value = "";
    await api(API_PATHS.session);
    hideLogin();
    await refresh();
  } catch (error) {
    passwordInput.value = "";
    $("#loginError").textContent = LOGIN_ERROR_MESSAGES[error.message] || LOGIN_ERROR_MESSAGES.unavailable;
  } finally {
    button.disabled = false;
  }
}

async function logout() {
  try { await api(API_PATHS.logout, { method: "POST" }); }
  catch (_) { /* the local UI still locks even when the server is unavailable */ }
  finally { showLogin(); }
}

async function changeLocalAdminPassword(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button");
  const currentInput = $("#currentAdminPassword");
  const newInput = $("#newAdminPassword");
  const confirmInput = $("#confirmAdminPassword");
  const message = $("#passwordChangeMessage");
  const currentPassword = currentInput.value;
  const newPassword = newInput.value;
  const confirmation = confirmInput.value;
  message.className = "form-message";
  message.textContent = "";
  if (newPassword !== confirmation) {
    newInput.value = "";
    confirmInput.value = "";
    message.classList.add("error");
    message.textContent = "Новый пароль и подтверждение не совпадают.";
    newInput.focus();
    return;
  }
  button.disabled = true;
  try {
    await api(API_PATHS.passwordChange, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schema: "home-center.local-admin-password-change.v1",
        current_password: currentPassword,
        new_password: newPassword,
      }),
    });
    form.reset();
    try { await api(API_PATHS.logout, { method: "POST" }); } catch (_) { /* lock locally */ }
    showLogin();
    $("#loginError").classList.add("success");
    $("#loginError").textContent = "Пароль изменён. Войдите с новым паролем.";
  } catch (error) {
    message.classList.add("error");
    message.textContent = error.message;
  } finally {
    currentInput.value = "";
    newInput.value = "";
    confirmInput.value = "";
    button.disabled = false;
  }
}

function switchView(view) {
  state.currentView = view;
  const titles = {
    overview: ["Управляемая инфраструктура", "Обзор"],
    nodes: ["Фактическое состояние", "Узлы"],
    cluster: ["Отказоустойчивость", "Кластер"],
    tls: ["HTTPS / trust / renewal", "Сертификаты"],
    backups: ["Восстановление", "Резервные копии"],
    jobs: ["Оркестрация", "Задания"],
    audit: ["Evidence", "Аудит"],
    settings: ["Локальная учётная запись", "Настройки"],
  };
  if (!titles[view]) return;
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.dataset.panel === view));
  $("#viewEyebrow").textContent = titles[view][0];
  $("#viewTitle").textContent = titles[view][1];
}

function render() {
  if (!state.overview) return;
  const cluster = state.overview.cluster;
  const nodes = state.overview.nodes || [];
  const ready = nodes.filter((item) => item.status === "ready").length;
  const percent = Math.min(100, Math.round((ready / cluster.expected_nodes) * 100));
  const healthy = cluster.status === "healthy";
  $("#clusterStatus").classList.toggle("degraded", !healthy);
  $("#clusterStatus").lastChild.textContent = healthy ? " Инфраструктура исправна" : " Деградированный режим";
  $("#clusterSummary").textContent = healthy
    ? "Все ожидаемые узлы доступны, идентичность подтверждена, peer-канал защищён взаимной TLS-аутентификацией."
    : "Не все ожидаемые узлы подтвердили readiness. Изменения остаются заблокированы.";
  $("#healthPercent").textContent = `${percent}%`;
  $("#healthRing").style.setProperty("--health", `${percent * 3.6}deg`);
  $("#nodesMetric").textContent = `${ready} / ${cluster.expected_nodes}`;
  $("#nodesMetricNote").textContent = `из ${cluster.expected_nodes} ожидаемых`;
  $("#replicationMetric").textContent = healthy ? "Связь есть" : "Нет связи";
  $("#backupMetric").textContent = state.backups.length ? formatTime(state.backups[0].created_at) : "Ещё не создана";
  $("#backupMetricNote").textContent = state.backups.length ? "integrity + restore gate" : "ожидается первый timer run";
  $("#jobsMetric").textContent = String((state.overview.jobs || []).filter((job) => !["succeeded", "failed", "cancelled"].includes(job.state)).length);
  $("#lastUpdate").textContent = `Обновлено ${formatTime(state.overview.observed_at)}`;
  $("#homeCenterInstance").textContent = displayText(cluster.id, "Локальная система");
  $("#clusterIdentity").textContent = displayText(cluster.profile, "Управляемая система");
  renderTls();
  renderOverviewNodes(nodes);
  renderNodeCards(nodes);
  renderTopology(nodes);
  renderBackups();
  renderJobs();
  renderAudit();
  $("#profileJson").textContent = JSON.stringify(state.profile, null, 2);
}

function renderOverviewNodes(nodes) {
  const root = $("#overviewNodes");
  root.replaceChildren();
  for (const item of nodes) {
    const capability = item.capabilities || {};
    const hardware = capability.hardware || {};
    const storage = capability.storage?.root || {};
    const usedPercent = Number.isFinite(storage.used_bytes) && Number.isFinite(storage.total_bytes) && storage.total_bytes > 0
      ? Math.max(0, Math.min(100, Math.round(storage.used_bytes / storage.total_bytes * 100)))
      : 0;
    const row = node("div", "node-row");
    row.append(node("div", "server-icon", "▤"));
    const identity = node("div"); identity.append(node("strong", "", item.name)); identity.append(node("small", "", `${item.role} · ${item.address}`)); row.append(identity);
    const resource = node("div", "resource-cell"); resource.append(node("small", "", `${hardware.cpu_count || 0} CPU · ${formatBytes(hardware.memory_bytes)} · диск ${usedPercent}%`)); const bar = node("div", "resource-bar"); const fill = node("i"); fill.style.width = `${Math.min(100, usedPercent)}%`; bar.append(fill); resource.append(bar); row.append(resource);
    row.append(node("span", `node-state ${item.status === "ready" ? "" : "bad"}`, item.status === "ready" ? "Готов" : "Недоступен"));
    root.append(row);
  }
  if (!nodes.length) root.append(node("p", "", "Узлы ещё не обнаружены"));
}

function renderNodeCards(nodes) {
  const root = $("#nodeCards"); root.replaceChildren();
  for (const item of nodes) {
    const c = item.capabilities || {}, os = c.operating_system || {}, hw = c.hardware || {}, disk = c.storage?.root || {};
    const card = node("article", "node-card"); const head = node("div", "node-card-head"); head.append(node("span", "role-label" + (item.role === "standby" ? " standby" : ""), item.role.toUpperCase())); head.append(node("span", `node-state ${item.status === "ready" ? "" : "bad"}`, item.status)); card.append(head);
    card.append(node("h3", "", item.name)); card.append(node("p", "", `${item.address} · наблюдался ${formatTime(item.last_seen)}`));
    const details = node("div", "detail-grid");
    [["Операционная система", `${os.id || "—"} ${os.version || ""}`], ["Ядро", os.kernel || "—"], ["Процессоры", String(hw.cpu_count || "—")], ["Память", formatBytes(hw.memory_bytes)], ["Диск /", `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)}`], ["Идентичность", c.node?.machine_identity_hash || "—"]].forEach(([label,value]) => { const d=node("div","detail"); d.append(node("span","",label)); d.append(node("strong","",value)); details.append(d); });
    card.append(details);
    const chips=node("div","service-chips"); Object.entries(c.services || {}).forEach(([service,status])=>chips.append(node("span",`service-chip ${status === "active" ? "active" : ""}`,`${service.replace(".service","")} · ${status}`))); card.append(chips);
    root.append(card);
  }
}

function renderTopology(nodes) {
  const root = $("#clusterTopology");
  root.replaceChildren();
  if (!nodes.length) {
    root.append(node("p", "", "Данные об узлах пока недоступны"));
    return;
  }
  nodes.forEach((item, index) => {
    if (index > 0) {
      const link = node("div", "topology-link");
      link.append(node("span", "", "mTLS"), node("div"), node("small", "", "health · inventory · reconcile"));
      root.append(link);
    }
    const role = item.role === "leader" ? "LEADER" : (item.role === "standby" ? "STANDBY" : "NODE");
    const card = node("div", `topology-node${item.status === "ready" ? " ready" : ""}`);
    card.append(
      node("span", `role-label${item.role === "standby" ? " standby" : ""}`, role),
      node("strong", "", displayText(item.name)),
      node("small", "", displayText(item.address)),
      node("i"),
    );
    root.append(card);
  });
}

function tlsCheck(title, text, ok) {
  const item = node("li", ok ? "" : "warning");
  item.append(node("i", "", ok ? "✓" : "!"));
  const body = node("div");
  body.append(node("strong", "", title));
  body.append(node("span", "", text));
  item.append(body);
  return item;
}

function renderTls() {
  const root = $("#tlsDetails");
  const checks = $("#tlsChecks");
  root.replaceChildren();
  checks.replaceChildren();
  if (!state.tls) {
    $("#tlsMetric").textContent = "Нет данных";
    $("#tlsMetricNote").textContent = "TLS status API недоступен";
    $("#tlsWebHostname").textContent = "Статус недоступен";
    $("#tlsViewTag").textContent = "Нет данных";
    $("#tlsViewTag").className = "tag warning";
    $("#tlsJson").textContent = "TLS status API недоступен на этом узле.";
    return;
  }
  const web = state.tls.web || {};
  const renewal = state.tls.renewal || {};
  const candidate = state.tls.candidate || {};
  const operational = state.tls.operational || {};
  const trust = state.tls.trust_anchor || {};
  const webCa = state.tls.web_ca || {};
  const chainOk = web.chain_valid === true;
  const hostnameOk = web.hostname_match === true;
  const profileOk = web.profile_valid === true;
  const sanOk = web.san_policy_valid === true;
  const webCaOk = webCa.profile_valid === true && Number(webCa.days_remaining) > Number(renewal.web_ca_threshold_days ?? 427);
  const separate = web.mode === "separate-web-identity";
  const due = renewal.due === true;
  const operationalHealthy = operational.healthy === true;
  const recoveryRequired = operational.recovery_required === true;
  const healthy = chainOk && hostnameOk && profileOk && sanOk && webCaOk && !due && separate && operationalHealthy;
  $("#tlsMetric").textContent = healthy ? "Исправен" : (recoveryRequired ? "Требует восстановления" : (due ? "Требует ротации" : "Проверка"));
  $("#tlsMetricNote").textContent = `${web.days_remaining ?? "—"} дн. · ${separate ? "separate Web ID" : "legacy fallback"}`;
  $("#tlsWebHostname").textContent = state.tls.web_url || state.tls.web_hostname || "—";
  $("#tlsViewTag").textContent = healthy ? "TLS HEALTHY" : (recoveryRequired ? "RECOVERY REQUIRED" : (due ? "RENEWAL DUE" : "CHECK"));
  $("#tlsViewTag").className = healthy ? "tag" : "tag warning";
  const candidateText = candidate.complete ? "готов к активации" : (candidate.invalid ? "недопустимый — восстановление обязательно" : (candidate.partial ? "неполный — восстановление обязательно" : "не подготовлен"));
  [
    ["Режим", web.mode || "—"],
    ["Web PKI", profileOk ? "ECDSA P-256 / SHA-256" : (web.profile || "несовместимый профиль")],
    ["Издатель", web.issuer || "—"],
    ["SAN DNS", (web.san_dns || []).join(", ") || "—"],
    ["Действителен до", formatTime(web.not_after)],
    ["Осталось", Number.isFinite(web.days_remaining) ? `${web.days_remaining} дн.` : "—"],
    ["Web SHA-256", shortHash(web.fingerprint_sha256)],
    ["Trust anchor SHA-256", shortHash(trust.fingerprint_sha256)],
    ["Web CA SHA-256", shortHash(webCa.fingerprint_sha256)],
    ["Candidate", candidateText],
    ["Операционное состояние", operational.state || "—"],
  ].forEach(([label, value]) => { const d=node("div","detail"); d.append(node("span","",label)); d.append(node("strong","",value)); root.append(d); });
  checks.append(tlsCheck("Цепочка доверия", chainOk ? "Строгая проверка CA проходит" : "Цепочка не подтверждена", chainOk));
  checks.append(tlsCheck("Имя узла", hostnameOk ? `${web.expected_hostname} присутствует в SAN` : "Hostname не соответствует SAN", hostnameOk));
  checks.append(tlsCheck("Профиль браузера", profileOk ? "ECDSA P-256 / SHA-256" : "Алгоритм Web-сертификата несовместим", profileOk));
  checks.append(tlsCheck("SAN policy", sanOk ? "Node FQDN, reserved VIP и management IP присутствуют" : "Неполный обязательный SAN", sanOk));
  checks.append(tlsCheck("Web CA horizon", webCaOk ? "CA покрывает полный срок следующего leaf" : "Требуется ротация Web CA до выпуска leaf", webCaOk));
  checks.append(tlsCheck("Отдельная Web identity", separate ? "Peer mTLS ключ не используется Web listener" : "Работает миграционный peer-cert fallback", separate));
  checks.append(tlsCheck("Срок действия", due ? `Ротация требуется при пороге ${renewal.threshold_days ?? 30} дней` : "Ротация пока не требуется", !due));
  checks.append(tlsCheck("Staged candidate", candidate.partial ? "Обнаружена неполная пара cert/key" : candidateText, !candidate.partial));
  checks.append(tlsCheck("Состояние ротации", operationalHealthy ? "Нет незавершённой или неопределённой мутации" : (operational.reason || "Требуется проверка"), operationalHealthy));
  $("#tlsJson").textContent = JSON.stringify(state.tls, null, 2);
}

function table(headers, rows) {
  const t=node("table"); const thead=node("thead"), hr=node("tr"); headers.forEach((h)=>hr.append(node("th","",h))); thead.append(hr); t.append(thead); const tbody=node("tbody");
  rows.forEach((cells)=>{ const row=node("tr"); cells.forEach((cell)=>{ const td=node("td",cell.className || "",cell.text); row.append(td); }); tbody.append(row); }); t.append(tbody); return t;
}

function renderBackups() {
  const root=$("#backupList"); root.replaceChildren();
  if (!state.backups.length) { root.append(node("div","empty-state", "Первый проверенный restore point появится после запуска backup timer.")); return; }
  root.append(table(["Создана","Узел","Размер","Проверка","SHA-256"], state.backups.map((b)=>[{text:formatTime(b.created_at)},{text:b.node_id},{text:formatBytes(b.archive_bytes)},{text:b.verification?.sqlite_integrity === "ok" ? "PASS" : "UNVERIFIED"},{text:(b.archive_sha256 || "—").slice(0,20)+"…",className:"mono"}])));
}

function renderJobs() {
  const jobs=state.overview.jobs || []; if (!jobs.length) return;
  const root=$("#jobList"); root.className="table-wrap"; root.replaceChildren(table(["ID","Тип","Состояние","Создано"],jobs.map((j)=>[{text:j.job_id,className:"mono"},{text:j.job_type},{text:j.state},{text:formatTime(j.created_at)}])));
}

function renderAudit() {
  const root=$("#auditList"); root.replaceChildren();
  root.append(table(["№","Время","Действие","Цель","Результат","Correlation"],state.audit.map((a)=>[{text:String(a.seq)},{text:formatTime(a.occurred_at)},{text:a.action},{text:a.target},{text:a.outcome},{text:a.correlation_id,className:"mono"}])));
}

async function refresh() {
  $("#refreshButton").disabled=true; $("#notice").classList.add("hidden");
  try {
    const [overviewDocument, profileDocument, backupsDocument, auditDocument, tlsDocument] = await Promise.all([
      api(API_PATHS.overview),
      api(API_PATHS.deploymentProfile),
      api(API_PATHS.backups),
      api(API_PATHS.audit),
      optionalApi(API_PATHS.tls),
    ]);
    const overview = requireInfrastructureOverview(overviewDocument);
    const profile = requireDocument(profileDocument, "home-center.deployment-profile.v1");
    const backups = requireDocument(backupsDocument, "home-center.backup-list.v1");
    const audit = requireDocument(auditDocument, "home-center.audit-list.v1");
    if (!Array.isArray(backups.items) || !Array.isArray(audit.items)) throw new Error("api_contract_mismatch");
    state.overview = overview;
    state.profile = profile;
    state.backups = backups.items;
    state.audit = audit.items;
    state.tls = tlsDocument?.schema === "home-center.tls-status.v1" ? tlsDocument : null;
    render();
  } catch (error) {
    if (error.message !== "authentication_required") { $("#notice").textContent=`Не удалось обновить данные: ${error.message}`; $("#notice").classList.remove("hidden"); }
  } finally { $("#refreshButton").disabled=false; }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#loginForm").addEventListener("submit", login); $("#passwordChangeForm").addEventListener("submit", changeLocalAdminPassword); $("#logoutButton").addEventListener("click", logout); $("#mobileLogoutButton").addEventListener("click", logout); $("#refreshButton").addEventListener("click", refresh);
  $$(".nav-item").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.view)));
  $$("[data-go]").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.go)));
  await loadAuthProviders();
  try { state.session = await api(API_PATHS.session); hideLogin(); await refresh(); } catch (error) { if (error.message !== "authentication_required") showLogin(); }
  setInterval(()=>{ if ($("#loginLayer").hidden) refresh(); },15000);
});
