/* Студия Dewiar, основа: общее состояние, сохранение настроек и переключение разделов.
   S — снимок настроек из ядра (приходит в "hello" и событии "settings" — см. dewiar.js), personas/active/entities — личности.
   save(patch) шлёт core.call("settings_save", { patch }): ядро сливает частичный patch с настройками и возвращает их целиком.
   Клик по разделу слева подгружает его данные функциями из других файлов студии.
   ui/settings.html подключает файлы студии по порядку, и все они делят одну глобальную область: этот файл идёт первым,
   поэтому всё, что он вызывает сразу при загрузке, объявлено здесь же или раньше (assets/*.js); функции других файлов
   вызываются только из обработчиков кликов, когда всё уже загружено. */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
// экранирование для innerHTML — общее для всех файлов студии (здесь, в первом файле: 24.10 у человека «escH is not defined»
// при подключении по коду — общий помощник жил в folder.js и пропал вместе с ним)
function escH(t) { return String(t == null ? "" : t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
// ошибки страницы — в журнал ядра (в отчёт): какой файл не загрузился и почему видно сразу, без догадок
window.__jsErr = window.__jsErr || [];
window.addEventListener("error", (e) => { window.__jsErr.push(`${e.message} @ ${(e.filename || "").split("/").pop()}:${e.lineno || 0}`); });
let S = null, personas = [], active = "lira", entities = [];
const brand = new DewiarEntity($("#brand"), { style: "nexus", color: "#8A9BFF", ring: false });
const COLORS = ["#FFB547", "#3DF5C8", "#4FC3FF", "#9BD4FF", "#B78CFF", "#FF7AB6", "#FF6B5A", "#9BE15D", "#F5E663", "#7C8CFF", "#E6EEFF"];
const STYLE_NAMES = { nexus: "Нейросфера", aura: "Сфера", holobrain: "Голограмма мозга", lattice: "Кристалл", orbital: "Орбиты", tactical: "Радар", rings: "Кольца", strings: "Струны", particles: "Частицы" };

/* Брендовая шапка — компактный переключатель окна агента. Студия уже открыта рядом
   с виджетом, поэтому состояние считаем включённым до первого ответа tray_state. */
let agentWindowVisible = true;
function agentToggleState(visible) {
  agentWindowVisible = !!visible;
  const b = $("#agent-toggle");
  if (!b) return;
  b.setAttribute("aria-pressed", String(agentWindowVisible));
  b.title = agentWindowVisible
    ? "Свернуть окно агента и убрать его с экрана. Повторное нажатие снова покажет окно."
    : "Показать окно агента. Повторное нажатие свернёт его и уберёт с экрана.";
  b.classList.toggle("agent-hidden", !agentWindowVisible);
}
async function toggleAgentWindow() {
  const a = window.api && window.api();
  if (!a || typeof a.show !== "function" || typeof a.hide !== "function") { toast("Окно агента доступно из запущенного Dewiar.app"); return; }
  const next = !agentWindowVisible;
  try {
    await (next ? a.show("card") : a.hide("card"));
    agentToggleState(next);
  } catch (e) { toast(e.message || "Не удалось изменить окно агента"); }
}
const agentToggle = $("#agent-toggle");
if (agentToggle) {
  agentToggle.onclick = toggleAgentWindow;
  agentToggle.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleAgentWindow(); } };
  const a = window.api && window.api();
  if (a && typeof a.tray_state === "function") a.tray_state().then(s => agentToggleState(!s?.in_tray)).catch(() => {});
}

function toast(t, ms, kind = "") {
  const el = $("#toast");
  el.textContent = t; el.className = "toast show";
  if (kind) el.classList.add(kind);
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms || 1800);
}
const get = (path) => path.split(".").reduce((o, k) => (o || {})[k], S);
function patchOf(path, v) { const keys = path.split("."), out = {}; let cur = out; keys.slice(0, -1).forEach(k => cur = cur[k] = {}); cur[keys.at(-1)] = v; return out; }
let saveT = {};
function save(patch, quiet) {
  return core.call("settings_save", { patch }).then(s => { S = s; if (!quiet) toast("Сохранено"); refreshStatic(); }).catch(e => toast(e.message));
}

/* «Безопасность» → «Ваши данные»: общий текст assets/privacy.js (тот же — на телефоне и у самой помощницы) */
if (window.DEWIAR_PRIVACY && $("#priv-box")) {
  const e = (t) => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  $("#priv-box").innerHTML = ($("#priv-box").closest("details") ? "" : `<h3 style="font-size:15px;margin-bottom:8px">${e(DEWIAR_PRIVACY.title)}</h3>`)
    + DEWIAR_PRIVACY.items.map(([t, x]) => `<p class="hint" style="margin:0 0 6px"><b style="color:var(--ink);font-weight:600">${e(t)}.</b> ${e(x)}</p>`).join("");
}

/* навигация */
/* «Душа»: дневник, душа, конституция и «Обо мне» — один пункт слева; каждая страница остаётся отдельной <section>,
   а сверху у неё полоска вкладок. Вкладку открывает скрытая кнопка nav с тем же data-p; _direct отличает такой
   переход от клика по пункту «Душа», который возвращает на последнюю открытую вкладку (soul_tab). */
const SOUL_TABS = [["diary", "Дневник"], ["soul", "Душа"], ["const", "Конституция"], ["me", "Обо мне"]];
const lsGet = (k, d) => { try { return LS.get(k, d); } catch (e) { return d; } };   // LS объявлен позже, в memory.js
function soulGo(p) { const b = $(`nav button[data-p="${p}"]`); if (!b) return; b._direct = true; b.click(); b._direct = false; }
SOUL_TABS.forEach(([p]) => {
  const sec = $("#p-" + p); if (!sec) return;
  const bar = document.createElement("div"); bar.className = "stabs";
  bar.innerHTML = SOUL_TABS.map(([q, t]) => `<button data-go="${q}">${t}</button>`).join("");
  $$("button", bar).forEach(x => x.onclick = () => soulGo(x.dataset.go));
  sec.insertBefore(bar, sec.firstChild);
});
$$("nav button[data-p]").forEach(b => b.onclick = () => {
  if (b.dataset.p === "diary" && !b._direct) {                 // «Душа» слева — открываем вкладку, на которой были
    const t = lsGet("soul_tab", "diary");
    if (t !== "diary" && SOUL_TABS.some(x => x[0] === t)) return soulGo(t);
  }
  $$("nav button").forEach(x => x.classList.toggle("cur", x === b));
  $$("section").forEach(s => s.classList.toggle("cur", s.id === "p-" + b.dataset.p));
  if (SOUL_TABS.some(t => t[0] === b.dataset.p)) { $('nav button[data-p="diary"]').classList.add("cur"); $$(".stabs button").forEach(x => x.classList.toggle("on", x.dataset.go === b.dataset.p)); try { LS.set("soul_tab", b.dataset.p); } catch (e) { } }
  if (b.dataset.p === "soul") loadSoul();
  if (b.dataset.p === "const") loadConst();
  if (b.dataset.p === "ref") loadRefs();             // «Рефералы» — с облака, настройки не нужны
  if (b.dataset.p === "notes") loadNotesPage();      // «Заметки» — файлы папки ассистента
  if (b.dataset.p === "pay" && window.loadPayPage) loadPayPage();      // «Платежи» (studio/pay.js)
  if (b.dataset.p === "vault" && window.loadVaultPage) loadVaultPage();  // «Сейф паролей» (studio/vault.js)
  if (!S) return;                                    // данные подгрузятся после связи с ядром
  if (b.dataset.p === "sound") loadDevices();
  if (b.dataset.p === "about") core.call("status").then(renderStatus).catch(() => {});
  if (b.dataset.p === "skills") { loadPlugins(); core.call("skills").then(renderSkills).catch(() => {}); core.call("mcp_status").then(renderMcp).catch(() => {});
    if (S.skills && S.skills.catalog_url) { $("#cat-url").value = S.skills.catalog_url; openCatalog(); } }
  if (b.dataset.p === "me") loadMe();
  if (b.dataset.p === "tricks") loadTricks();       // «Что я умею» — data/tricks.json через ядро
  clearInterval(mTimer);
  if (b.dataset.p === "memory") { loadNotes(); loadMemory(!!mNet); setTimeout(mPanes, 0); mTimer = setInterval(() => { if (!document.hidden) loadMemory(true); }, 30000); }
  if (b.dataset.p === "voice") core.call("voices_warm").catch(() => {});
  if (b.dataset.p === "brain") core.call("cloud_status", { refresh: true }).then(cloudFill).catch(() => {});
  if (b.dataset.p === "dw") { dwLoad(); dwQr(); }
  if (b.dataset.p === "hands") { core.call("tools").then(renderTools).catch(() => {}); }
  if (b.dataset.p === "reminders") loadReminders();
  if (b.dataset.p === "servers") svLoad();
  if (b.dataset.p === "calendar") loadCalendar();
  if (b.dataset.p === "folder") loadFolder();
  if (b.dataset.p === "diary") loadDiary();
  if (b.dataset.p === "safety") { loadSafety(); loadVault(); loadBackups(); }
  if (b.dataset.p === "tasks") loadTasks();
  if (b.dataset.p === "prompter") loadMeetings();
  if (b.dataset.p === "about") core.call("update_status").then(renderUpdate).catch(() => {});
  if (b.dataset.p === "voice") loadVosk();
  if (b.dataset.p === "look") loadLook();
  if (b.dataset.p === "close") loadClose(true);
  if (b.dataset.p === "chat") loadChat();
  if (b.dataset.p === "home" && window.loadHome) loadHome();
});
