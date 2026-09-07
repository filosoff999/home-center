"use strict";

const state = { overview: null, profile: null, backups: [], audit: [], tls: null, modulePermissionReview: null, modulePermissionAcknowledgements: null, moduleLifecycle: null, authProviders: null, currentView: "overview" };
const LOGIN_ERROR_MESSAGES = Object.freeze({
  invalid_credentials: "Неверное имя пользователя или пароль.",
  too_many_requests: "Слишком много попыток входа. Повторите позже.",
  unavailable: "Сервис входа временно недоступен.",
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
    const response = await fetch("/api/v1/auth/providers", {
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
  $("#loginError").textContent = "";
  window.setTimeout(() => $("#usernameInput").focus(), 0);
}

function hideLogin() {
  $("#loginLayer").hidden = true;
  $("#loginError").textContent = "";
  $("#loginForm").reset();
}

async function authenticate(provider, username, password) {
  try {
    const response = await fetch("/api/v1/session", {
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
  $("#loginError").textContent = "";
  try {
    await authenticate(provider, username, password);
    passwordInput.value = "";
    await api("/api/v1/session");
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
  try { await api("/api/v1/session/logout", { method: "POST" }); }
  catch (_) { /* the local UI still locks even when the server is unavailable */ }
  finally { showLogin(); }
}

function switchView(view) {
  state.currentView = view;
  const titles = {
    overview: ["Управляемая инфраструктура", "Обзор"],
    nodes: ["Фактическое состояние", "Узлы"],
    cluster: ["Отказоустойчивость", "Кластер"],
    modules: ["Market / least privilege", "Разрешения модулей"],
    tls: ["HTTPS / trust / renewal", "Сертификаты"],
    backups: ["Восстановление", "Резервные копии"],
    jobs: ["Оркестрация", "Задания"],
    audit: ["Evidence", "Аудит"],
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
  const percent = Math.round((ready / Math.max(cluster.expected_nodes, 1)) * 100);
  const healthy = cluster.status === "healthy";
  $("#clusterStatus").classList.toggle("degraded", !healthy);
  $("#clusterStatus").lastChild.textContent = healthy ? " Инфраструктура исправна" : " Деградированный режим";
  $("#clusterSummary").textContent = healthy ? "Оба сервера доступны, идентичность подтверждена, peer-канал защищён взаимной TLS-аутентификацией." : "Локальный узел работает, но один из участников не подтвердил readiness. Изменения остаются заблокированы.";
  $("#healthPercent").textContent = `${percent}%`;
  $("#healthRing").style.setProperty("--health", `${percent * 3.6}deg`);
  $("#nodesMetric").textContent = `${ready} / ${cluster.expected_nodes}`;
  $("#replicationMetric").textContent = healthy ? "Связь есть" : "Нет связи";
  $("#backupMetric").textContent = state.backups.length ? formatTime(state.backups[0].created_at) : "Ещё не создана";
  $("#backupMetricNote").textContent = state.backups.length ? "integrity + restore gate" : "ожидается первый timer run";
  $("#jobsMetric").textContent = String((state.overview.jobs || []).filter((job) => !["succeeded", "failed", "cancelled"].includes(job.state)).length);
  $("#lastUpdate").textContent = `Обновлено ${formatTime(state.overview.observed_at)}`;
  renderTls();
  renderOverviewNodes(nodes);
  renderNodeCards(nodes);
  renderTopology(nodes);
  renderModulePermissionReview();
  renderModulePermissionAcknowledgements();
  renderModuleLifecycle();
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
    const usedPercent = storage.total_bytes ? Math.round(storage.used_bytes / storage.total_bytes * 100) : 0;
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
  for (const name of ["dc01","dc02"]) {
    const found = nodes.find((item) => item.name === name);
    $(`#topology${name[0].toUpperCase()}${name.slice(1)}`).classList.toggle("ready", found?.status === "ready");
  }
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

function permissionReviewEmpty(title, message, icon = "▦") {
  const article = node("article", "panel");
  const empty = node("div", "empty-state");
  empty.append(node("span", "", icon));
  empty.append(node("h3", "", title));
  empty.append(node("p", "", message));
  article.append(empty);
  return article;
}

function renderModulePermissionReview() {
  const root = $("#modulePermissionReview");
  if (!root) return;
  root.replaceChildren();
  const review = state.modulePermissionReview;
  if (!review) {
    root.append(permissionReviewEmpty("Статус недоступен", "Не удалось получить безопасный статус проверки разрешений.", "!"));
    return;
  }
  const inert = review.decision_persistence_enabled === false
    && review.permission_grants_applied === false
    && review.production_activation_enabled === false;
  if (!inert) {
    root.append(permissionReviewEmpty("Ответ отклонён", "Сервер вернул недопустимое состояние полномочий.", "!"));
    return;
  }
  if (review.schema === "home-center.module-permission-review-status.v1" && review.status === "no-pending-review" && review.review === null) {
    root.append(permissionReviewEmpty("Нет плана на рассмотрении", "Проверка появится здесь после выбора точной версии модуля в Market. Никакие права сейчас не запрошены."));
    return;
  }
  if (review.schema !== "home-center.module-permission-review.v1" || review.status !== "review-required" || !Array.isArray(review.permissions) || !Array.isArray(review.modules)) {
    root.append(permissionReviewEmpty("Ответ отклонён", "Формат проверки разрешений не соответствует закрытому контракту.", "!"));
    return;
  }

  const summary = node("div", "permission-summary");
  [
    ["Модули", review.modules.length],
    ["Только чтение", review.summary?.read_only ?? 0],
    ["Изменения", review.summary?.mutation ?? 0],
    ["Опасные / неизвестные", (review.summary?.destructive ?? 0) + (review.summary?.unclassified ?? 0)],
  ].forEach(([label, value]) => {
    const item = node("article", "permission-metric");
    item.append(node("span", "", label));
    item.append(node("strong", "", String(value)));
    summary.append(item);
  });
  root.append(summary);

  const panel = node("article", "panel");
  const heading = node("div", "panel-head");
  const title = node("div");
  title.append(node("span", "panel-kicker", "Точный план"));
  title.append(node("h3", "", "Запрошенные разрешения"));
  heading.append(title);
  heading.append(node("span", "tag warning", "Требуется ознакомление"));
  panel.append(heading);
  const list = node("div", "permission-list");
  const riskLabels = { "read-only": "Только чтение", mutation: "Изменение", destructive: "Разрушительное", unclassified: "Не классифицировано" };
  review.permissions.forEach((permission) => {
    const risk = Object.prototype.hasOwnProperty.call(riskLabels, permission.risk) ? permission.risk : "unclassified";
    const card = node("div", `permission-card risk-${risk}`);
    const cardHead = node("div", "permission-card-head");
    cardHead.append(node("strong", "mono", typeof permission.id === "string" ? permission.id : "invalid.permission"));
    cardHead.append(node("span", "permission-risk", riskLabels[risk]));
    card.append(cardHead);
    const modules = Array.isArray(permission.modules) ? permission.modules.join(", ") : "—";
    const actionCount = Array.isArray(permission.actions) ? permission.actions.length : 0;
    card.append(node("p", "", `${modules} · ${actionCount ? `типизированных действий: ${actionCount}` : "нет связанного типизированного действия"}`));
    list.append(card);
  });
  panel.append(list);
  panel.append(node("div", "review-identity mono", `Review ID: ${shortHash(review.review_id)}`));
  root.append(panel);
}

function renderModulePermissionAcknowledgements() {
  const root = $("#moduleAcknowledgementHistory");
  if (!root) return;
  root.replaceChildren();
  const history = state.modulePermissionAcknowledgements;
  if (!history) {
    root.append(permissionReviewEmpty("История недоступна", "Не удалось получить подтверждения ознакомления.", "!"));
    return;
  }
  const safeList = history.schema === "home-center.module-permission-acknowledgement-list.v1"
    && Array.isArray(history.items)
    && history.items.length <= 100
    && history.acknowledgement_persistence_enabled === true
    && history.authorization_decisions_enabled === false
    && history.permission_grants_applied === false
    && history.lifecycle_execution_enabled === false
    && history.production_activation_enabled === false
    && history.items.every((item) => item
      && ["recorded", "expired", "superseded"].includes(item.status)
      && item.authorization_decision_persisted === false
      && item.acknowledgement_persistence_enabled === true
      && item.permission_grants_applied === false
      && item.lifecycle_execution_enabled === false
      && item.production_activation_enabled === false
      && item.lifecycle_handoff?.consumable === false
      && typeof item.lifecycle_handoff.reference === "string"
      && Array.isArray(item.review?.requested_modules)
      && item.review.requested_modules.every((module) => module
        && typeof module.id === "string"
        && typeof module.version === "string"));
  if (!safeList) {
    root.append(permissionReviewEmpty("История отклонена", "Сервер вернул недопустимое состояние полномочий.", "!"));
    return;
  }
  if (!history.items.length) {
    root.append(permissionReviewEmpty("Ознакомлений ещё нет", "Запись появится после явного ознакомления с точным review в будущем Market-процессе."));
    return;
  }

  const panel = node("article", "panel");
  const list = node("div", "acknowledgement-list");
  const statusLabels = { recorded: "Действует", expired: "Истекло", superseded: "Заменено" };
  history.items.forEach((item) => {
    const card = node("div", `acknowledgement-card status-${item.status}`);
    const head = node("div", "permission-card-head");
    head.append(node("strong", "", statusLabels[item.status]));
    head.append(node("span", "permission-risk", `до ${formatTime(item.expires_at)}`));
    card.append(head);
    const requested = Array.isArray(item.review?.requested_modules)
      ? item.review.requested_modules.map((module) => `${module.id}@${module.version}`).join(", ")
      : "—";
    card.append(node("p", "", requested));
    card.append(node("small", "mono", `Review: ${shortHash(item.review_id)}`));
    card.append(node("small", "mono", `Handoff: ${item.lifecycle_handoff.reference}`));
    list.append(card);
  });
  panel.append(list);
  root.append(panel);
}

function renderModuleLifecycle() {
  const root = $("#moduleLifecyclePlan");
  if (!root) return;
  root.replaceChildren();
  const lifecycle = state.moduleLifecycle;
  if (!lifecycle) {
    root.append(permissionReviewEmpty("Статус недоступен", "Не удалось получить границу жизненного цикла.", "!"));
    return;
  }
  const inert = lifecycle.acknowledgement_consumption_enabled === false
    && lifecycle.authorization_decisions_enabled === false
    && lifecycle.artifact_mutation_enabled === false
    && lifecycle.lifecycle_persistence_enabled === false
    && lifecycle.lifecycle_execution_enabled === false
    && lifecycle.production_activation_enabled === false;
  if (!inert) {
    root.append(permissionReviewEmpty("План отклонён", "Сервер вернул недопустимое состояние выполнения.", "!"));
    return;
  }
  if (lifecycle.schema === "home-center.module-lifecycle-status.v1"
      && lifecycle.status === "no-pending-lifecycle"
      && lifecycle.plan === null) {
    root.append(permissionReviewEmpty("Нет плана жизненного цикла", "Точный заблокированный план появится после выбора модуля и действующего ознакомления. Выполнение сейчас отключено."));
    return;
  }
  const blockerLabels = {
    artifact_publication_unverified: "Артефакт не связан с доверенным хранилищем",
    authorization_contract_unavailable: "Контракт авторизации ещё не доступен",
    lifecycle_executor_unavailable: "Исполнитель жизненного цикла отключён",
    placement_unresolved: "Размещение по узлам не определено",
  };
  const safePlan = lifecycle.schema === "home-center.module-install-lifecycle-plan.v1"
    && lifecycle.status === "blocked"
    && lifecycle.operation === "install"
    && lifecycle.acknowledgement?.consumable === false
    && Array.isArray(lifecycle.blockers)
    && lifecycle.blockers.length === 4
    && lifecycle.blockers.every((item) => Object.prototype.hasOwnProperty.call(blockerLabels, item))
    && Array.isArray(lifecycle.steps)
    && lifecycle.steps.length > 0
    && lifecycle.steps.length <= 128
    && lifecycle.steps.every((step) => step?.state === "blocked"
      && typeof step.module?.id === "string"
      && typeof step.module?.version === "string"
      && typeof step.action?.id === "string"
      && step.action?.risk === "mutation"
      && step.action?.idempotent === true)
    && lifecycle.recovery?.strategy === "reverse-order-rollback"
    && lifecycle.recovery?.state === "planned"
    && lifecycle.recovery?.data_policy === "preserve"
    && Array.isArray(lifecycle.recovery?.steps)
    && lifecycle.recovery.steps.length === lifecycle.steps.length;
  if (!safePlan) {
    root.append(permissionReviewEmpty("План отклонён", "Формат preflight или recovery не соответствует закрытому контракту.", "!"));
    return;
  }
  const panel = node("article", "panel");
  const heading = node("div", "panel-head");
  const title = node("div");
  title.append(node("span", "panel-kicker", "Только предварительный просмотр"));
  title.append(node("h3", "", `${lifecycle.steps.length} шагов установки`));
  heading.append(title);
  heading.append(node("span", "tag warning", "BLOCKED"));
  panel.append(heading);
  const blockers = node("div", "lifecycle-blockers");
  lifecycle.blockers.forEach((item) => blockers.append(node("div", "lifecycle-blocker", blockerLabels[item])));
  panel.append(blockers);
  const steps = node("div", "acknowledgement-list");
  lifecycle.steps.forEach((step) => {
    const card = node("div", "acknowledgement-card status-superseded");
    const head = node("div", "permission-card-head");
    head.append(node("strong", "", `${step.module.id}@${step.module.version}`));
    head.append(node("span", "permission-risk", "Заблокировано"));
    card.append(head);
    card.append(node("p", "mono", step.action.id));
    card.append(node("small", "", `${step.postconditions?.length || 0} health postconditions`));
    steps.append(card);
  });
  panel.append(steps);
  panel.append(node("div", "review-identity mono", `Recovery: ${lifecycle.recovery.steps.length} шагов в обратном порядке · данные сохраняются`));
  root.append(panel);
}

async function refresh() {
  $("#refreshButton").disabled=true; $("#notice").classList.add("hidden");
  try {
    const [overview, profile, backups, audit, tls, modulePermissionReview, modulePermissionAcknowledgements, moduleLifecycle] = await Promise.all([
      api("/api/v1/overview"),
      api("/api/v1/deployment-profile"),
      api("/api/v1/backups"),
      api("/api/v1/audit?limit=100"),
      optionalApi("/api/v1/tls"),
      api("/api/v1/modules/permission-review"),
      api("/api/v1/modules/permission-review/acknowledgements?limit=20"),
      api("/api/v1/modules/lifecycle"),
    ]);
    state.overview=overview; state.profile=profile; state.backups=backups.items || []; state.audit=audit.items || []; state.tls=tls; state.modulePermissionReview=modulePermissionReview; state.modulePermissionAcknowledgements=modulePermissionAcknowledgements; state.moduleLifecycle=moduleLifecycle; render();
  } catch (error) {
    if (error.message !== "authentication_required") { $("#notice").textContent=`Не удалось обновить данные: ${error.message}`; $("#notice").classList.remove("hidden"); }
  } finally { $("#refreshButton").disabled=false; }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#loginForm").addEventListener("submit", login); $("#logoutButton").addEventListener("click", logout); $("#mobileLogoutButton").addEventListener("click", logout); $("#refreshButton").addEventListener("click", refresh);
  $$(".nav-item").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.view)));
  $$("[data-go]").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.go)));
  await loadAuthProviders();
  try { await api("/api/v1/session"); hideLogin(); await refresh(); } catch (error) { if (error.message !== "authentication_required") showLogin(); }
  setInterval(()=>{ if ($("#loginLayer").hidden) refresh(); },15000);
});
