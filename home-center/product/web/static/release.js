"use strict";

window.HOME_CENTER_RELEASE = Object.freeze({
  version: "0.7.0",
  revision: null,
});

document.addEventListener("DOMContentLoaded", () => {
  const brand = document.querySelector(".brand > div:last-child");
  if (!brand) return;
  const current = document.querySelector("#homeCenterVersion");
  const label = current || document.createElement("small");
  label.id = "homeCenterVersion";
  const revision = window.HOME_CENTER_RELEASE.revision;
  label.textContent = revision
    ? `v${window.HOME_CENTER_RELEASE.version} · ${revision.slice(0, 12)}`
    : `v${window.HOME_CENTER_RELEASE.version}`;
  if (!current) brand.append(label);
});
