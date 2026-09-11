"use strict";

window.HOME_CENTER_RELEASE = Object.freeze({
  version: "0.55.0",
  revision: null,
});

function loadPlanningUI() {
  if (document.querySelector('script[data-home-center-planning="v1"]')) return;
  const script = document.createElement("script");
  script.src = "/static/planning.js";
  script.dataset.homeCenterPlanning = "v1";
  document.head.append(script);
}

function installReleaseUI() {
  const brand = document.querySelector(".brand > div:last-child");
  if (brand) {
    const current = document.querySelector("#homeCenterVersion");
    const label = current || document.createElement("small");
    label.id = "homeCenterVersion";
    const version = /^[0-9]+\.[0-9]+\.[0-9]+$/.test(window.HOME_CENTER_RELEASE.version)
      ? window.HOME_CENTER_RELEASE.version
      : "0.15.0";
    const revision = /^[0-9a-f]{40}$/.test(window.HOME_CENTER_RELEASE.revision || "")
      ? window.HOME_CENTER_RELEASE.revision
      : null;
    label.textContent = revision
      ? `v${version} · ${revision.slice(0, 12)}`
      : `v${version}`;
    if (!current) brand.append(label);
  }
  loadPlanningUI();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", installReleaseUI, { once: true });
} else {
  installReleaseUI();
}
