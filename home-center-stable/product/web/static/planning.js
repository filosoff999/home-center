"use strict";

(() => {
  const CAPABILITIES = Object.freeze([
    Object.freeze({
      id: "node-lifecycle-v2",
      kicker: "Core Lifecycle v2",
      title: "Жизненный цикл узлов",
      description: "Безопасные переходы узла проходят через preflight и trust до состояния ready. План не применяет изменения автоматически.",
      facts: Object.freeze(["preflighted → trusted → configuring → ready", "degraded остаётся явным fail-closed состоянием", "production_mutation_enabled = false"]),
      contract: "home-center.node-transition-plan.v2",
    }),
    Object.freeze({
      id: "compute-framework",
      kicker: "Compute Framework",
      title: "Вычислительные ресурсы",
      description: "Единый планирующий слой для физических ресурсов, VM и LXC с проверкой capability и доступной ёмкости.",
      facts: Object.freeze(["VM и LXC — типизированные ресурсы", "provider health и runtime capability обязательны", "создание workload не выполняется из UI"]),
      contract: "home-center.compute-plan.v1",
    }),
    Object.freeze({
      id: "home-lab-manager",
      kicker: "Home Lab Manager",
      title: "Домашняя лаборатория",
      description: "Квоты лаборатории ограничивают количество сред, CPU, память и хранилище до передачи запроса в compute planner.",
      facts: Object.freeze(["лимит числа лабораторных сред", "CPU / RAM / storage quota", "compute planning без запуска ресурсов"]),
      contract: "home-center.home-lab-plan.v1",
    }),
    Object.freeze({
      id: "certificate-lifecycle",
      kicker: "Certificate Lifecycle",
      title: "Жизненный цикл сертификатов",
      description: "Инвентаризация и планирование обновления сертификатов выполняются без публикации закрытых ключей или автоматической активации.",
      facts: Object.freeze(["проверка срока и identity", "issuer и reload capability входят в preflight", "секретный материал в UI отсутствует"]),
      contract: "home-center.certificate-renewal-plan.v1",
    }),
  ]);

  function element(tag, className, text) {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  }

  function installStylesheet() {
    if (document.querySelector('link[data-home-center-planning="v1"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/planning.css";
    link.dataset.homeCenterPlanning = "v1";
    document.head.append(link);
  }

  function planningCard(capability) {
    const card = element("article", "planning-card");
    card.dataset.capability = capability.id;
    const head = element("div", "planning-card-head");
    const heading = element("div");
    heading.append(element("span", "panel-kicker", capability.kicker));
    heading.append(element("h3", "", capability.title));
    head.append(heading, element("span", "planning-state", "Только планирование"));
    card.append(head, element("p", "", capability.description));
    const facts = element("ul", "planning-facts");
    capability.facts.forEach((fact) => facts.append(element("li", "", fact)));
    card.append(facts, element("small", "planning-contract", capability.contract));
    return card;
  }

  function showPlanning(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === "planning"));
    document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.dataset.panel === "planning"));
    const eyebrow = document.querySelector("#viewEyebrow");
    const title = document.querySelector("#viewTitle");
    if (eyebrow) eyebrow.textContent = "Plan-only capabilities";
    if (title) title.textContent = "Планирование";
  }

  function installPlanningUI() {
    if (document.querySelector('[data-panel="planning"]')) return;
    const navigation = document.querySelector("#navigation");
    const main = document.querySelector("main");
    if (!navigation || !main) return;

    installStylesheet();

    const navButton = element("button", "nav-item");
    navButton.type = "button";
    navButton.dataset.view = "planning";
    navButton.setAttribute("aria-label", "Планирование возможностей");
    navButton.append(element("span", "nav-icon", "▦"), document.createTextNode("Планирование"));
    navButton.addEventListener("click", showPlanning, { capture: true });
    const jobsButton = navigation.querySelector('[data-view="jobs"]');
    navigation.insertBefore(navButton, jobsButton || null);

    const section = element("section", "view");
    section.dataset.panel = "planning";
    const intro = element("div", "section-intro");
    const introText = element("div");
    introText.append(element("p", "", "HC 0.14 · планирующий контур"), element("h2", "", "Возможности планирования"));
    intro.append(introText, element("span", "tag warning", "Изменения отключены"));
    section.append(intro);

    const grid = element("div", "planning-grid");
    CAPABILITIES.forEach((capability) => grid.append(planningCard(capability)));
    section.append(grid);

    const lock = element("aside", "planning-lock");
    const lockText = element("div");
    lockText.append(
      element("strong", "", "Production mutation остаётся заблокирован"),
      element("span", "", "Этот раздел показывает только поддерживаемые планирующие контракты. Здесь нет кнопок создания VM/LXC, установки модулей, применения node transition или ротации сертификатов."),
    );
    lock.append(lockText, element("span", "tag warning", "PLAN ONLY"));
    section.append(lock);
    main.append(section);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installPlanningUI, { once: true });
  else installPlanningUI();
})();
