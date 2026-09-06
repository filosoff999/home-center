"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const appPath = process.argv[2];
if (!appPath) throw new Error("app.js path is required");

class FakeElement {
  constructor(id) {
    this.id = id;
    this.hidden = id === "loginLayer";
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.handlers = {};
    this.focusCount = 0;
    this.resetCount = 0;
  }

  addEventListener(name, callback) { this.handlers[name] = callback; }
  focus() { this.focusCount += 1; }
  querySelector(selector) { return selector === "button" ? elements.loginButton : null; }
  reset() {
    this.resetCount += 1;
    elements.tokenInput.value = "";
  }
}

const ids = [
  "loginLayer",
  "loginForm",
  "loginButton",
  "loginError",
  "tokenInput",
  "logoutButton",
  "mobileLogoutButton",
  "refreshButton",
];
const elements = Object.fromEntries(ids.map((id) => [id, new FakeElement(id)]));
const documentHandlers = {};
const requests = [];
let fetchImplementation = async () => { throw new Error("fetch not configured"); };

const document = {
  addEventListener(name, callback) { documentHandlers[name] = callback; },
  querySelector(selector) { return selector.startsWith("#") ? (elements[selector.slice(1)] || null) : null; },
  querySelectorAll() { return []; },
  createElement() { return new FakeElement(""); },
};

const context = vm.createContext({
  console,
  document,
  window: {},
  fetch: async (path, options = {}) => {
    requests.push({ path, options });
    return fetchImplementation(path, options);
  },
  setInterval(callback, milliseconds) { context.interval = { callback, milliseconds }; return 1; },
  clearInterval() {},
  Intl,
});
context.globalThis = context;
vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });
vm.runInContext("refresh = async function () { globalThis.__refreshCalls += 1; };", context);
context.__refreshCalls = 0;

function response(status, body = {}) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

function configureFetch(implementation) {
  requests.length = 0;
  fetchImplementation = implementation;
}

async function submit(token) {
  elements.tokenInput.value = token;
  let prevented = false;
  await context.login({
    preventDefault() { prevented = true; },
    currentTarget: elements.loginForm,
  });
  assert.equal(prevented, true);
}

async function main() {
  configureFetch(async () => response(401, {
    error: { code: "invalid_credentials", message: "SENSITIVE BACKEND DETAIL" },
  }));
  elements.loginLayer.hidden = false;
  await submit("x".repeat(40));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, "/api/v1/session");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.credentials, "same-origin");
  assert.deepEqual(JSON.parse(requests[0].options.body), { token: "x".repeat(40) });
  assert.equal(elements.loginError.textContent, "Неверный токен администратора.");
  assert.equal(elements.loginError.textContent.includes("SENSITIVE"), false);
  assert.equal(elements.tokenInput.value, "");

  configureFetch(async (path, options) => {
    if (path === "/api/v1/session" && options.method === "POST") return response(429);
    throw new Error(`unexpected request ${path}`);
  });
  await submit("y".repeat(40));
  assert.equal(elements.loginError.textContent, "Слишком много попыток входа. Подождите и попробуйте снова.");

  configureFetch(async (path, options) => {
    if (path === "/api/v1/session" && options.method === "POST") return response(204);
    if (path === "/api/v1/session" && options.method === undefined) return response(401);
    throw new Error(`unexpected request ${path}`);
  });
  await submit("z".repeat(40));
  assert.equal(elements.loginLayer.hidden, false, "a 2xx without a usable cookie must stay locked");
  assert.equal(
    elements.loginError.textContent,
    "Сервис авторизации временно недоступен. Повторите попытку позже.",
  );

  configureFetch(async (path, options) => {
    if (path === "/api/v1/session" && options.method === "POST") return response(204);
    if (path === "/api/v1/session" && options.method === undefined) {
      return response(200, { authenticated: true });
    }
    throw new Error(`unexpected request ${path}`);
  });
  context.__refreshCalls = 0;
  await submit("candidate-browser-fixture-token-000000000000000000000");
  assert.equal(elements.loginLayer.hidden, true);
  assert.equal(elements.loginError.textContent, "");
  assert.equal(context.__refreshCalls, 1);

  configureFetch(async (path, options) => {
    assert.equal(path, "/api/v1/session");
    assert.equal(options.method, undefined);
    return response(200, { authenticated: true });
  });
  elements.loginLayer.hidden = false;
  context.__refreshCalls = 0;
  await documentHandlers.DOMContentLoaded();
  assert.equal(elements.loginLayer.hidden, true, "cookie-backed session must survive reload");
  assert.equal(context.__refreshCalls, 1);
  assert.equal(context.interval.milliseconds, 15000);
  assert.equal(typeof elements.logoutButton.handlers.click, "function");
  assert.equal(typeof elements.mobileLogoutButton.handlers.click, "function");

  configureFetch(async (path, options) => {
    assert.equal(path, "/api/v1/session/logout");
    assert.equal(options.method, "POST");
    return response(204);
  });
  elements.loginLayer.hidden = true;
  await elements.logoutButton.handlers.click();
  assert.equal(elements.loginLayer.hidden, false, "desktop logout must restore overlay");

  configureFetch(async () => { throw new Error("logout backend unavailable"); });
  elements.loginLayer.hidden = true;
  await elements.mobileLogoutButton.handlers.click();
  assert.equal(elements.loginLayer.hidden, false, "mobile logout must restore overlay fail-closed");

  process.stdout.write("HC_WEB_070_TOKEN_SOURCE_ACCEPTANCE=PASS\n");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
