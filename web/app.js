const fileInput = document.querySelector("#file-input");
const linkInput = document.querySelector("#link-input");
const langIn = document.querySelector("#lang-in");
const langOut = document.querySelector("#lang-out");
const pages = document.querySelector("#pages");
const pagesCustom = document.querySelector("#pages-custom");
const start = document.querySelector("#start");
const cancel = document.querySelector("#cancel");
const progress = document.querySelector("#progress");
const preview = document.querySelector("#preview");
const downloadMono = document.querySelector("#download-mono");
const downloadDual = document.querySelector("#download-dual");

let locale = {};
let jobId = "";
let busy = false;
let previewUrl = "";
const UI_LANG = "solarsystem-ui-lang";

function uiLang() {
  try {
    return localStorage.getItem(UI_LANG) === "en" ? "en" : "zh";
  } catch (error) {
    return "zh";
  }
}

function applyCopy() {
  document.documentElement.lang = uiLang() === "en" ? "en" : "zh-CN";
  document.title = locale.app_name || "SolarSystem";
  document.querySelector("h1").textContent = locale.header || "SolarSystem";
  preview.title = locale.preview_heading || "预览";
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const value = locale[node.dataset.i18n];
    if (typeof value === "string") {
      node.textContent = value;
    }
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => {
    const value = locale[node.dataset.i18nPlaceholder];
    if (typeof value === "string") {
      node.placeholder = value;
    }
  });
  if (progress.dataset.idle === "1") {
    progress.textContent = locale.progress_idle || "尚未开始";
  }
  if (locale.languages) {
    for (const select of [langIn, langOut]) {
      for (const option of select.options) {
        if (locale.languages[option.value]) {
          option.textContent = locale.languages[option.value];
        }
      }
    }
  }
}

function sourceKind() {
  const chosen = document.querySelector('input[name="source-kind"]:checked');
  return chosen ? chosen.value : "file";
}

function syncSource() {
  const useFile = sourceKind() === "file";
  document.querySelector("#file-field").hidden = !useFile;
  document.querySelector("#link-field").hidden = useFile;
}

function syncPages() {
  document.querySelector("#pages-custom-row").hidden = pages.value !== "custom";
}

function pageSpec() {
  if (pages.value === "custom") {
    return pagesCustom.value.trim();
  }
  return pages.value;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function poll(id) {
  for (;;) {
    const response = await fetch("/api/jobs/" + id);
    const body = await response.json();
    progress.textContent = body.progress || body.error || body.state;
    if (body.state === "done") {
      preview.src = body.mono;
      downloadMono.hidden = false;
      downloadDual.hidden = false;
      downloadMono.href = body.mono;
      downloadDual.href = body.dual;
      downloadMono.setAttribute("download", "");
      downloadDual.setAttribute("download", "");
      return;
    }
    if (body.state === "cancelled" || body.state === "error") {
      progress.textContent = body.error || body.progress;
      return;
    }
    await sleep(400);
  }
}

fileInput.addEventListener("change", () => {
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    return;
  }
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
  }
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
});

document.querySelectorAll('input[name="source-kind"]').forEach((node) => {
  node.addEventListener("change", syncSource);
});
pages.addEventListener("change", syncPages);

const settings = document.querySelector("#settings");
const settingsMenu = document.querySelector("#settings-menu");
const modelForm = document.querySelector("#model-form");
const NOTICE_KEY = "solarsystem-api-notice";

function showSettingsMenu() {
  settingsMenu.hidden = false;
  modelForm.hidden = true;
}

function syncNotice() {
  let closed = false;
  try {
    closed = localStorage.getItem(NOTICE_KEY) === "off";
  } catch (error) {
    closed = false;
  }
  document.querySelector("#api-notice").hidden = closed;
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const body = await response.json();
  if (!response.ok) {
    return;
  }
  document.querySelector("#base-url").value = body.base_url || "";
  document.querySelector("#model").value = body.model || "";
  document.querySelector("#api-key").value = "";
  document.querySelector("#api-key-set").hidden = !body.api_key_set;
}

document.querySelector("#open-settings").addEventListener("click", () => {
  showSettingsMenu();
  if (!settings.open) {
    settings.showModal();
  }
});
document.querySelector("#open-model").addEventListener("click", () => {
  settingsMenu.hidden = true;
  modelForm.hidden = false;
  document.querySelector("#config-status").textContent = "";
  loadConfig();
});
document.querySelector("#settings-back").addEventListener("click", showSettingsMenu);
document.querySelector("#close-api-notice").addEventListener("click", () => {
  try {
    localStorage.setItem(NOTICE_KEY, "off");
  } catch (error) {
    /* 关掉这一次即可。 */
  }
  document.querySelector("#api-notice").hidden = true;
});
modelForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = document.querySelector("#config-status");
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      base_url: document.querySelector("#base-url").value.trim(),
      api_key: document.querySelector("#api-key").value,
      model: document.querySelector("#model").value.trim(),
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    status.textContent = payload.error || response.statusText;
    return;
  }
  document.querySelector("#api-key").value = "";
  document.querySelector("#api-key-set").hidden = !payload.api_key_set;
  status.textContent = (locale.web && locale.web.config_saved) || "已保存。";
});
document.querySelector("#switch-language").addEventListener("click", () => {
  const next = uiLang() === "en" ? "zh" : "en";
  try {
    localStorage.setItem(UI_LANG, next);
  } catch (error) {
    return;
  }
  loadLocale(next);
});
document.querySelector("#close-settings").addEventListener("click", () => settings.close());
settings.addEventListener("click", (event) => {
  if (event.target === settings) {
    settings.close();
  }
});
if (location.hash === "#settings" || location.hash === "#api-key") {
  if (location.hash === "#api-key") {
    settingsMenu.hidden = true;
    modelForm.hidden = false;
    loadConfig();
  }
  settings.showModal();
}
syncNotice();

start.addEventListener("click", async () => {
  if (busy) {
    return;
  }
  const kind = sourceKind();
  const webCopy = locale.web || {};
  if (kind === "file" && !(fileInput.files && fileInput.files[0])) {
    progress.textContent = webCopy.need_source || "请选择一个文件，或填写链接。";
    return;
  }
  if (kind === "link" && !linkInput.value.trim()) {
    progress.textContent = webCopy.need_source || "请选择一个文件，或填写链接。";
    return;
  }
  busy = true;
  downloadMono.hidden = true;
  downloadDual.hidden = true;
  progress.textContent = webCopy.queued || "正在准备";
  const body = new FormData();
  if (kind === "file") {
    body.append("file", fileInput.files[0]);
  } else {
    body.append("link", linkInput.value.trim());
  }
  body.append("lang_in", langIn.value);
  body.append("lang_out", langOut.value);
  body.append("pages", pageSpec());
  body.append("thread", document.querySelector("#thread").value || "4");
  body.append("ignore_cache", document.querySelector("#ignore-cache").checked ? "1" : "0");
  body.append("skip_subset_fonts", document.querySelector("#skip-subset").checked ? "1" : "0");
  body.append("formula_font", document.querySelector("#formula-font").value);
  body.append("formula_char", document.querySelector("#formula-char").value);
  body.append("prompt", document.querySelector("#prompt").value);
  body.append("ui_lang", uiLang());
  progress.dataset.idle = "";
  try {
    const response = await fetch("/api/jobs", { method: "POST", body });
    const payload = await response.json();
    if (!response.ok) {
      progress.textContent = payload.error || response.statusText;
      return;
    }
    jobId = payload.id;
    await poll(jobId);
  } catch (error) {
    progress.textContent = String(error);
  } finally {
    busy = false;
  }
});

cancel.addEventListener("click", async () => {
  if (!jobId) {
    return;
  }
  await fetch("/api/jobs/" + jobId + "/cancel", { method: "POST" });
});

function loadLocale(lang) {
  return fetch("/locale/" + lang + ".json")
    .then((response) => response.json())
    .then((data) => {
      locale = data;
      applyCopy();
    });
}

loadLocale(uiLang()).catch(() => {
  locale = { web: { need_source: "请选择一个文件，或填写链接。", queued: "正在准备" } };
});

syncSource();
syncPages();
