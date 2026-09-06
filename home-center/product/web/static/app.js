"use strict";

const state = { overview: null, profile: null, backups: [], audit: [], currentView: "overview" };
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

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", headers: { "Accept": "application/json", ...(options.headers || {}) }, ...options });
  if (response.status === 401) { showLogin(); throw new Error("authentication_required"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
  return data;
}

function showLogin() { $("#loginLayer").hidden = false; $("#tokenInput").focus(); }
function hideLogin() { $("#loginLayer").hidden = true; $("#loginError").textContent = ""; $("#loginForm").reset(); }

async function login(event) {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const token = $("#tokenInput").value;
  button.disabled = true;
  $("#loginError").textContent = "";
  try {
    await api("/api/v1/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    hideLogin();
    await refresh();
  } catch (error) {
    if (error.message !== "authentication_required") $("#loginError").textContent = error.message;
  } finally { button.disabled = false; }
}

async function logout() {
  try { await api("/api/v1/session/logout", { method: "POST" }); } catch (_) { /* cookie is cleared best effort */ }
  showLogin();
}

function switchView(view) {
  state.currentView = view;
  const titles = { overview: ["Управляемая инфраструктура", "Обзор"], nodes: ["Фактическое состояние", "Узлы"], cluster: ["Отказоустойчивость", "Кластер"], backups: ["Восстановление", "Резервные копии"], jobs: ["Оркестрация", "Задания"], audit: ["Evidence", "Аудит"] };
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
    const [overview, profile, backups, audit] = await Promise.all([api("/api/v1/overview"),api("/api/v1/deployment-profile"),api("/api/v1/backups"),api("/api/v1/audit?limit=100")]);
    state.overview=overview; state.profile=profile; state.backups=backups.items || []; state.audit=audit.items || []; render();
  } catch (error) {
    if (error.message !== "authentication_required") { $("#notice").textContent=`Не удалось обновить данные: ${error.message}`; $("#notice").classList.remove("hidden"); }
  } finally { $("#refreshButton").disabled=false; }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#loginForm").addEventListener("submit", login); $("#logoutButton").addEventListener("click", logout); $("#refreshButton").addEventListener("click", refresh);
  $$(".nav-item").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.view)));
  $$("[data-go]").forEach((item)=>item.addEventListener("click",()=>switchView(item.dataset.go)));
  try { await api("/api/v1/session"); hideLogin(); await refresh(); } catch (error) { if (error.message !== "authentication_required") showLogin(); }
  setInterval(()=>{ if ($("#loginLayer").hidden) refresh(); },15000);
});
