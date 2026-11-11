/* Студия Dewiar: «Обо мне» (память и «Знания»), почта, Telegram, серверы (SSH), MAX, Obsidian, аккаунт dewiar.pro,
   отчёты о сбоях и кнопка «Проверить обновления».
   Каждый блок — функция *Fill(данные) плюс обработчики кнопок; команды ядра: memory_get/set, knowledge_*, mail_*, tg_*,
   server_*, max_*, obsidian_*, account_*, report*, update_check; события telegram, max, obsidian, account,
   report_offer, update — ядро присылает в них то же, что отвечает на команды; жалобы — complaint_send, complaints,
   bug_shot/bug_state и события bug_shot, complaints.
   Файлы студии подключаются по порядку из ui/settings.html и делят одну глобальную область: при загрузке здесь только
   вешаются обработчики; SVG/ICONS (выше), escH (folder.js), RI (brain.js), renderUpdate (tasks.js) нужны при отрисовке.
   openExt и obLast отсюда использует memory.js. */
/* ---------- обо мне: память и «Знания» ---------- */
function fmtSize(b) { return b > 1e6 ? (b / 1e6).toFixed(1).replace(".", ",") + " МБ" : Math.max(1, Math.round(b / 1e3)) + " КБ"; }
function renderKn(files) {
  const box = $("#kn-list"); box.innerHTML = "";
  if (!files.length) { box.innerHTML = '<p class="hint">Пока пусто. Добавьте файлы — и ассистент сможет по ним отвечать.</p>'; return; }
  files.forEach(f => {
    const row = document.createElement("div"); row.className = "kn-row";
    row.innerHTML = `<b></b><span class="${f.error ? "bad" : ""}"></span><span>${fmtSize(f.size)}</span><button class="btn small" title="Убрать из «Знаний»">Убрать</button>`;
    $("b", row).textContent = f.path; $("span", row).textContent = f.error ? f.error : `${f.chunks} ${f.chunks % 10 === 1 && f.chunks % 100 !== 11 ? "отрывок" : "отрывков"}`;
    $("button", row).onclick = () => core.call("knowledge_remove", { path: f.path }).then(renderKn).catch(e => toast(e.message));
    box.appendChild(row);
  });
}
/* свои папки для поиска (core/knowledge.py: индексируются в фоне; пока идёт — «читаю: 120 из 400») */
let knFT = 0;
function renderKnFolders(list) {
  const box = $("#kn-folders"); box.innerHTML = ""; clearTimeout(knFT);
  if (!list.length) { box.innerHTML = '<p class="hint">Своих папок пока нет.</p>'; return; }
  list.forEach(f => {
    const row = document.createElement("div"); row.className = "kn-row";
    row.innerHTML = `<b></b><span></span><span></span><button class="btn small" title="Больше не искать в этой папке (файлы остаются)">Убрать</button>`;
    $("b", row).textContent = f.path; row.title = f.path;
    const st = $$("span", row);
    st[0].className = f.error || !f.exists ? "bad" : "";
    st[0].textContent = !f.exists ? "папки нет" : f.error ? f.error : f.busy ? `читаю: ${f.indexed || 0} из ${f.files || "…"}` : f.files == null ? "ждёт очереди" : `файлов: ${f.files}${f.skipped ? " (дальше — не берём)" : ""}`;
    st[1].textContent = f.chunks ? `${f.chunks} отрывков` : "";
    $("button", row).onclick = () => core.call("knowledge_folder_remove", { path: f.path }).then(renderKnFolders).catch(e => toast(e.message));
    box.appendChild(row);
  });
  if (list.some(f => f.busy || f.files == null)) knFT = setTimeout(() => core.call("knowledge_folders").then(renderKnFolders).catch(() => {}), 2500);
}
$("#kn-fadd").onclick = async () => {
  const a = window.api && window.api(); let path = "";
  if (a && a.pick_folder) path = await a.pick_folder();
  else path = await askText("Полный путь к папке с документами:") || "";
  if (!path) return;
  try { renderKnFolders(await core.call("knowledge_folder_add", { path })); $("#kn-fres").className = "res ok"; $("#kn-fres").textContent = "Добавлено — читаю файлы в фоне"; }
  catch (e) { $("#kn-fres").className = "res bad"; $("#kn-fres").textContent = e.message; }
};
/* Следы экзамена способностей (16.10: «Бюджет Миши на мебель… менялось 13 раз» в «Обо мне»): похожее на его фразы —
   списком с галочками, убирает человек. «Оставить» — больше не показываем этот же набор (подпись в настройках ui.exam_clean). */
const XT_KIND = { memory: "Память", note: "Заметки", money: "Расходы", done: "Сделано" };
let xtItems = [];
async function meExam() {
  const box = $("#me-exam"); box.hidden = true;
  try { xtItems = await core.call("exam_traces"); } catch (e) { return; }
  const sig = xtItems.map(x => x.kind + ":" + x.id).sort().join("|");
  if (!xtItems.length || ((S && S.ui) || {}).exam_clean === sig.length + ":" + sig.slice(0, 200)) return;
  $("#me-exam-list").innerHTML = Object.keys(XT_KIND).filter(k => xtItems.some(x => x.kind === k)).map(k => `<h4>${XT_KIND[k]}</h4>` +
    xtItems.map((x, i) => x.kind !== k ? "" : `<label><input type="checkbox" checked data-i="${i}"><span>${escH(x.text)}</span></label>`).join("")).join("");
  box.hidden = false;
  $("#me-exam-keep").onclick = () => { box.hidden = true; save({ ui: { exam_clean: sig.length + ":" + sig.slice(0, 200) } }, true); };
  $("#me-exam-go").onclick = async () => {
    const items = $$("#me-exam-list input:checked").map(c => xtItems[+c.dataset.i]);
    try { const r = await core.call("exam_traces_remove", { items }); toast(r.removed ? `Убрано записей: ${r.removed}` : "Ничего не убрано", 3000); }
    catch (e) { toast(e.message, 4000); return; }
    core.call("memory_get").then(t => { $("#me-mem").value = t.trim(); }).catch(() => {});
    meExam();
  };
}
function loadMe() {
  core.call("memory_get").then(t => { $("#me-mem").value = t.trim(); }).catch(() => {});
  meExam();
  core.call("knowledge_list").then(r => renderKn(r.files)).catch(e => ($("#kn-res").textContent = e.message));
  core.call("knowledge_folders").then(renderKnFolders).catch(() => {});
  const a = S && lastAccount; $("#kn-site").textContent = a && a.logged_in ? `Знания с сайта тоже подключены: аккаунт ${a.user && a.user.login || a.login} (раздел «Подключение»).` : "";
}
let lastAccount = null;
$("#me-save").onclick = () => core.call("memory_set", { text: $("#me-mem").value }).then(() => { $("#me-res").className = "res ok"; $("#me-res").textContent = "Сохранено"; setTimeout(() => ($("#me-res").textContent = ""), 2500); }).catch(e => toast(e.message));
$("#kn-open").onclick = () => core.call("knowledge_open").catch(e => toast(e.message));
$("#kn-file").onchange = async (ev) => {
  const files = [...ev.target.files]; ev.target.value = ""; const r = $("#kn-res"); let n = 0;
  for (const f of files) {
    // файл идёт в ядро одним сообщением base64 (+⅓ к размеру), а сообщение — не больше 16 МБ (core/bridge.py)
    if (f.size > 11e6) { toast(`«${f.name}» больше 11 МБ — положите его в папку сами: «Открыть папку»`); continue; }
    r.className = "res"; r.textContent = `добавляю ${f.name}…`;
    const b64 = await new Promise((ok, bad) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = bad; fr.readAsDataURL(f); });
    try { const res = await core.call("knowledge_add", { name: f.name, b64 }); renderKn(res.files); n++; }
    catch (e) { toast(`${f.name}: ${e.message}`); }
  }
  r.className = n ? "res ok" : "res"; r.textContent = n ? `Добавлено файлов: ${n}` : "";
};
$("#kn-go").onclick = async () => {
  const q = $("#kn-q").value.trim(); if (!q) return; const box = $("#kn-found"); box.innerHTML = "";
  try {
    const hits = await core.call("knowledge_search", { query: q });
    if (!hits.length) { box.innerHTML = '<p class="hint" style="margin-top:8px">Ничего не нашлось.</p>'; return; }
    hits.forEach(h => { const d = document.createElement("div"); d.className = "kn-hit"; d.innerHTML = "<b></b><div></div>"; $("b", d).textContent = h.path; $("div", d).textContent = h.text.slice(0, 420) + (h.text.length > 420 ? "…" : ""); box.appendChild(d); });
  } catch (e) { toast(e.message); }
};
$("#kn-q").onkeydown = (e) => { if (e.key === "Enter") $("#kn-go").click(); };

/* ---------- Telegram ---------- */
let tgTimer = null;
let tgWas = null, tgMode = "";   // tgWas: был ли привязан владелец в прошлый раз — чтобы поздравить ровно в момент привязки
function tgFill(t) {
  if (!t) return;
  if (tgWas === false && t.paired) { $("#tg-res").className = "res ok"; $("#tg-res").textContent = "Готово! Напишите боту что-нибудь — ответит ассистент."; }
  tgWas = !!t.paired; tgMode = t.mode || "";
  $("#tg-setup").style.display = t.connected ? "none" : "";
  $("#tg-pair").style.display = t.connected && !t.paired ? "" : "none";
  $("#tg-on").style.display = t.paired ? "" : "none";
  if (t.bot) { $("#tg-link").textContent = "@" + t.bot; $("#tg-link").href = t.link || ("https://t.me/" + t.bot + (t.code ? "?start=" + t.code : ""));
              $("#tg-open").href = $("#tg-link").href;
              $("#tg-web").href = "https://web.telegram.org/k/#?tgaddr=" + encodeURIComponent("tg://resolve?domain=" + t.bot + (t.code ? "&start=" + t.code : ""));
              tgQr(t.connected && !t.paired ? $("#tg-link").href : ""); }
  tgSharedVis();
  $("#tg-code").textContent = "/start " + (t.code || "");
  $("#tg-state").textContent = t.paired ? `Подключён${t.mode === "dewiar" ? " через бота Dewiar" : ""}: @${t.bot} · владелец ${t.owner || "вы"}` : "";
  $("#tg-notify").checked = t.notify !== false; $("#tg-voice").checked = !!t.voice_home; $("#tg-vreply").checked = t.voice_reply !== false;
  if (t.api) $("#tg-api").value = t.api;
  $("#tg-off-row").style.display = t.can_offline ? "" : "none";
  $("#tg-offline").checked = t.offline !== false;
  { const os_ = t.offline_state || {}, base = "Бот Dewiar сам отвечает с сервера: вопросы, заметки, список покупок, напоминания. Включится компьютер — всё окажется и на нём.";
    $("#tg-off-why").textContent = !os_.synced ? base : !os_.ok ? "Чтобы отвечать без компьютера, нужны модели Dewiar: войдите в аккаунт dewiar.pro (раздел «Dewiar.pro») или подключите ключ Dewiar."
      : base + (os_.tick ? "" : " Напоминания без компьютера заработают, когда на сервере включат расписание (tg.php?tick раз в минуту)."); }
  $("#tg-px-on").textContent = t.proxy ? "· включён" : "";
  if (document.activeElement !== $("#tg-proxy")) $("#tg-proxy").value = t.proxy || "";
  const r = $("#tg-res");
  if (t.problem) { r.className = "res bad"; r.textContent = "Telegram: " + t.problem; }
  // ждём, пока владелец нажмёт «Запустить» у бота: спрашиваем ядро раз в 2 с, пока не привяжется
  clearInterval(tgTimer); tgTimer = null;
  if (t.connected && !t.paired) tgTimer = setInterval(() => core.call("tg_get").then(x => { if (x.paired) tgFill(x); }).catch(() => {}), 2000);
}
function tgSharedVis() {
  $("#tg-shared").style.display = "";                 // аккаунт не нужен — бот Dewiar по ключу этого компьютера
  $("#tg-shared-info").style.display = "none";
  $("#tg-own-sum").textContent = "Дополнительно: свой бот (создаётся у @BotFather)";
}
$("#tg-shared-go").onclick = async (ev) => {
  const r = $("#tg-res"); ev.target.disabled = true; r.className = "res"; r.textContent = "подключаю…";
  try { const t = await core.call("tg_connect_shared"); r.textContent = ""; tgFill(t); }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#tg-connect").onclick = async (ev) => {
  const r = $("#tg-res"); ev.target.disabled = true; r.className = "res"; r.textContent = "проверяю токен…";
  try { const t = await core.call("tg_connect", { token: $("#tg-token").value.trim(), api: $("#tg-api").value.trim() });
        $("#tg-token").value = ""; r.textContent = ""; tgFill(t); }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#tg-token").onkeydown = (e) => { if (e.key === "Enter") $("#tg-connect").click(); };
$("#tg-proxy-save").onclick = async (ev) => {
  const r = $("#tg-res"), v = $("#tg-proxy").value.trim(); ev.target.disabled = true; r.className = "res"; r.textContent = v ? "проверяю прокси…" : "отключаю прокси…";
  // ядро отдаёт прокси со скрытым паролем («•••») — такую строку сохранять нельзя
  try { if (v.includes("•••")) throw new Error("впишите прокси заново, вместе с паролем");
        const t = await core.call("tg_proxy", { proxy: v }); tgFill(t); r.className = "res ok"; r.textContent = v ? "Прокси работает — Telegram ходит через него." : "Прокси выключен."; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#tg-proxy").onkeydown = (e) => { if (e.key === "Enter") $("#tg-proxy-save").click(); };
$("#tg-copy").onclick = () => { navigator.clipboard && navigator.clipboard.writeText($("#tg-open").href).then(() => toast("Ссылка скопирована")).catch(() => {}); };
function tgQr(url) {
  const box = $("#tg-qr");
  if (!url || typeof qrcode !== "function") { box.innerHTML = ""; box.style.display = "none"; return; }
  if (box.dataset.u === url) return;
  box.innerHTML = qrPretty(url, "M"); box.dataset.u = url; box.style.display = "";
}
$("#tg-notify").onchange = (e) => core.call("tg_set", { patch: { notify: e.target.checked } }).then(tgFill).catch(x => toast(x.message));
$("#tg-vreply").onchange = (e) => core.call("tg_set", { patch: { voice_reply: e.target.checked } }).then(tgFill).catch(x => toast(x.message));
$("#tg-offline").onchange = (e) => core.call("tg_set", { patch: { offline: e.target.checked } }).then(tgFill).catch(x => toast(x.message));
$("#tg-voice").onchange = (e) => core.call("tg_set", { patch: { voice_home: e.target.checked } }).then(tgFill).catch(x => toast(x.message));
$("#tg-test").onclick = async (ev) => { const r = $("#tg-res"); ev.target.disabled = true; r.className = "res"; r.textContent = "отправляю…";
  try { await core.call("tg_test"); r.className = "res ok"; r.textContent = "Сообщение отправлено — посмотрите в Telegram."; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; } };
$("#tg-recode").onclick = () => core.call("tg_new_code").then(t => { tgFill(t); $("#tg-res").className = "res"; $("#tg-res").textContent = "Прежний владелец отвязан — новый владелец открывает ссылку и жмёт «Запустить»."; }).catch(e => toast(e.message));
$("#tg-off").onclick = () => { const own = tgMode === "own";
  core.call("tg_forget").then(t => { tgWas = null; tgFill(t); $("#tg-res").className = "res";
    $("#tg-res").textContent = own ? "Telegram отключён. Бота можно удалить в @BotFather (/deletebot)." : "Telegram отключён — бот вам больше не пишет."; }).catch(e => toast(e.message)); };
// виджет дважды не загрузился — ui/app.py запускается без окна (ui.widget_fail2 ≥ 2); сброс счётчика включает окно снова
$("#wf-on").onclick = () => core.call("settings_save", { patch: { ui: { widget_fail2: 0 } } })
  .then(() => toast("Готово — перезапустите Dewiar (значок в трее → «Выход», затем снова из меню «Пуск»)")).catch(e => toast(e.message));
core.on("telegram", e => tgFill(e));
/* ---------- серверы ----------
   Вход по ключу SSH: пароль нужен один раз — ядро ставит ключ на сервер и пароль не хранит. ask — когда спрашивать
   разрешение на команды: на каждую или только на меняющие что-то. */
let svData = null;
function svFill(d) {
  if (!d) return; svData = d;
  const box = $("#sv-list"); box.innerHTML = "";
  const no = $("#sv-nossh");
  if (!d.ssh) { no.style.display = ""; no.innerHTML = '<b>Нет программы ssh.</b> <span class="hint">' + (navigator.userAgent.includes("Windows") ?
      "Windows: Параметры → Приложения → Дополнительные компоненты → «Клиент OpenSSH» → Установить." : "Linux: sudo apt install openssh-client") + "</span>"; }
  else no.style.display = "none";
  if (!d.servers.length) box.innerHTML = '<div class="rempty">Серверов пока нет — добавьте ниже. Понадобятся адрес, пользователь и (один раз) пароль.</div>';
  d.servers.forEach(x => {
    const el = document.createElement("div"); el.className = "ritem sv-item";
    el.innerHTML = `<div class="ico">${SVG(ICONS.servers)}</div><div class="rbody"><div class="rtext"></div><div class="rmeta"></div></div>
      <select title="Когда спрашивать"><option value="all">спрашивать каждую команду</option><option value="changes">спрашивать только изменения</option></select>
      <div class="ract"><button data-c="chk" title="Проверить вход">${SVG('<path d="M20 6 9 17l-5-5"/>')}</button><button data-c="del" title="Удалить">${SVG(RI.del)}</button></div>`;
    $(".rtext", el).textContent = x.name;
    const m = $(".rmeta", el);
    const add = (t, cls) => { const sp = document.createElement(cls === "code" ? "code" : "span"); sp.textContent = t; m.appendChild(sp); };
    add(`${x.user}@${x.host}${x.port !== 22 ? ":" + x.port : ""}`); if (x.system) add(x.system); if (x.fingerprint) add(x.fingerprint.slice(0, 22) + "…", "code");
    if ((x.aliases || []).length) add("ещё: " + x.aliases.join(", "));
    const sel = $("select", el); sel.value = x.ask || "all";
    sel.onchange = () => core.call("server_update", { id: x.id, patch: { ask: sel.value } }).then(svFill)
      .then(() => toast(sel.value === "changes" ? "Команды «только посмотреть» — без вопроса" : "Каждая команда — с вопросом")).catch(e => toast(e.message));
    $("[data-c=chk]", el).onclick = async (ev) => { const btn = ev.currentTarget; btn.disabled = true; toast("Проверяю вход…");   // после await currentTarget уже null
      try { const r = await core.call("server_check", { id: x.id }); svFill(r); toast(`«${x.name}»: вход есть · ${r.info.system || ""} · ${r.info.uptime || ""}`); }
      catch (e) { toast(e.message); } finally { btn.disabled = false; } };
    $("[data-c=del]", el).onclick = async () => { if (await ask(`Удалить сервер «${x.name}» из Dewiar? Ключ на самом сервере останется — его можно убрать из ~/.ssh/authorized_keys.`))
      core.call("server_remove", { id: x.id }).then(svFill).catch(e => toast(e.message)); };
    box.appendChild(el);
  });
  $("#sv-key").textContent = d.key || "ключ появится после первого сервера (или нажмите «Создать ключ»)";
  $("#sv-key-make").style.display = d.key ? "none" : "";
}
function svLoad() { core.call("server_list").then(svFill).catch(e => { $("#sv-list").textContent = e.message; }); }
$("#sv-add").onclick = async (ev) => {
  const r = $("#ssh-res"); const host = $("#ssh-host").value.trim(), user = $("#sv-user").value.trim();
  if (!host || !user) { r.className = "res bad"; r.textContent = "Нужны адрес и пользователь."; return; }
  ev.target.disabled = true; r.className = "res"; r.textContent = $("#sv-pass").value ? "ставлю ключ и проверяю вход… (до минуты)" : "проверяю вход по ключу…";
  try {
    const d = await core.call("server_add", { name: $("#sv-name").value.trim(), host, user, port: +$("#ssh-port").value || 22, password: $("#sv-pass").value,
      keyfile: $("#sv-keyfile").value.trim(), aliases: $("#sv-alias").value.split(",").map(x => x.trim()).filter(Boolean) });
    $("#sv-pass").value = ""; ["#sv-name", "#ssh-host", "#sv-user", "#sv-alias", "#sv-keyfile"].forEach(i => $(i).value = ""); $("#ssh-port").value = 22;
    svFill(d); const x = d.servers[d.servers.length - 1]; r.className = "res ok";
    r.textContent = `Готово: «${x.name}» — ${x.system || "вход есть"}. Скажите: «проверь место на сервере ${x.name}».`;
  } catch (e) { r.className = "res bad"; r.textContent = e.message; $("#sv-pass").value = ""; } finally { ev.target.disabled = false; }
};
$("#sv-key-copy").onclick = () => { if (svData && svData.key) navigator.clipboard.writeText(svData.key).then(() => toast("Ключ скопирован")); else toast("Сначала создайте ключ"); };
$("#sv-cmd-copy").onclick = () => {
  if (!(svData && svData.key)) return toast("Сначала создайте ключ");
  const host = $("#ssh-host").value.trim() || "АДРЕС", user = $("#sv-user").value.trim() || "ПОЛЬЗОВАТЕЛЬ", port = +$("#ssh-port").value || 22;
  // для тех, кто не хочет давать пароль Dewiar: та же установка ключа, но руками в терминале
  const cmd = `ssh ${port !== 22 ? "-p " + port + " " : ""}${user}@${host} "mkdir -p ~/.ssh && echo '${svData.key}' >> ~/.ssh/authorized_keys"`;
  navigator.clipboard.writeText(cmd).then(() => toast("Команда скопирована — вставьте её в терминал (PowerShell) и введите пароль сервера"));
};
$("#sv-key-make").onclick = () => core.call("server_key").then(d => { svFill(d); toast("Ключ создан"); }).catch(e => toast(e.message));

/* ---------- MAX ---------- (устроен как Telegram: свой бот или общий бот Dewiar, привязка владельца по коду /start) */
let mxTimer = null, mxWas = null;
function mxFill(t) {
  if (!t) return;
  if (mxWas === false && t.paired) { $("#mx-res").className = "res ok"; $("#mx-res").textContent = "Готово! Напишите боту в MAX что-нибудь — ответит ассистент."; }
  mxWas = !!t.paired;
  $("#mx-setup").style.display = t.connected ? "none" : "";
  $("#mx-pair").style.display = t.connected && !t.paired ? "" : "none";
  $("#mx-on").style.display = t.paired ? "" : "none";
  const link = t.link || (t.bot ? `https://max.ru/${t.bot}` + (t.code ? `?start=${t.code}` : "") : "");
  $("#mx-link").textContent = t.bot || "бот"; $("#mx-link").href = link; $("#mx-open").href = link;
  const box = $("#mx-qr");
  if (t.connected && !t.paired && link && typeof qrcode === "function") {
    if (box.dataset.u !== link) { box.innerHTML = qrPretty(link, "M"); box.dataset.u = link; }
    box.style.display = "";
  } else { box.innerHTML = ""; box.style.display = "none"; }
  $("#mx-code").textContent = "/start " + (t.code || "");
  $("#mx-state").textContent = t.paired ? `Подключён${t.mode === "dewiar" ? " через бота Dewiar" : ""}: ${t.bot_name || t.bot} · владелец ${t.owner || "вы"}` : "";
  $("#mx-notify").checked = t.notify !== false; $("#mx-voice").checked = !!t.voice_home; $("#mx-vreply").checked = t.voice_reply !== false;
  if (t.problem) { $("#mx-res").className = "res bad"; $("#mx-res").textContent = "MAX: " + t.problem; }
  clearInterval(mxTimer); mxTimer = null;
  if (t.connected && !t.paired) mxTimer = setInterval(() => core.call("max_get").then(x => { if (x.paired) mxFill(x); }).catch(() => {}), 2000);
}
async function mxDo(ev, cmd, args, wait) {
  const r = $("#mx-res"); if (ev) ev.target.disabled = true; r.className = "res"; r.textContent = wait || "";
  try { const t = await core.call(cmd, args || {}); r.textContent = ""; mxFill(t); return t; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { if (ev) ev.target.disabled = false; }
}
$("#mx-shared-go").onclick = (ev) => mxDo(ev, "max_connect_shared", {}, "подключаю…");
$("#mx-connect").onclick = (ev) => mxDo(ev, "max_connect", { token: $("#mx-token").value.trim() }, "проверяю токен…").then(t => { if (t) $("#mx-token").value = ""; });
$("#mx-token").onkeydown = (e) => { if (e.key === "Enter") $("#mx-connect").click(); };
$("#mx-copy").onclick = () => { navigator.clipboard && navigator.clipboard.writeText($("#mx-open").href).then(() => toast("Ссылка скопирована")).catch(() => {}); };
$("#mx-notify").onchange = (e) => core.call("max_set", { patch: { notify: e.target.checked } }).then(mxFill).catch(x => toast(x.message));
$("#mx-vreply").onchange = (e) => core.call("max_set", { patch: { voice_reply: e.target.checked } }).then(mxFill).catch(x => toast(x.message));
$("#mx-voice").onchange = (e) => core.call("max_set", { patch: { voice_home: e.target.checked } }).then(mxFill).catch(x => toast(x.message));
$("#mx-test").onclick = async (ev) => { const t = await mxDo(ev, "max_test", {}, "отправляю…"); if (t) { $("#mx-res").className = "res ok"; $("#mx-res").textContent = "Сообщение отправлено — посмотрите в MAX."; } };
$("#mx-recode").onclick = async () => { const t = await mxDo(null, "max_new_code"); if (t) $("#mx-res").textContent = "Прежний владелец отвязан — новый владелец открывает ссылку и жмёт «Начать»."; };
$("#mx-off").onclick = async () => { const t = await mxDo(null, "max_forget"); if (t) { mxWas = null; $("#mx-res").textContent = "MAX отключён — бот вам больше не пишет."; } };
core.on("max", e => mxFill(e));


/* ---------- Obsidian ---------- */
/* Внешняя ссылка (в т. ч. obsidian://) — через окно: внутри pywebview window.open не откроет другую программу */
function openExt(url) { const a = api(); if (a && a.open_url) a.open_url(url); else window.open(url, "_blank"); }
let obLast = null;
function obFill(o) {
  if (!o) return; obLast = o;
  $("#ob-setup").style.display = o.connected ? "none" : "";
  $("#ob-on").style.display = o.connected ? "" : "none";
  const sel = $("#ob-vault"), cur = sel.value;
  const opts = (o.vaults || []).map(v => [v.path, `${v.name} — ${v.path}`]);
  if (cur && !opts.some(x => x[0] === cur)) opts.unshift([cur, cur]);
  sel.innerHTML = opts.length ? opts.map(([p, t]) => `<option value="${escH(p)}">${escH(t)}</option>`).join("")
                              : `<option value="">Obsidian не найден — выберите папку хранилища</option>`;
  if (cur) sel.value = cur;
  const st = o.stats || {}, moved = (st.to_vault || 0) + (st.from_vault || 0);
  $("#ob-state").textContent = o.connected ? `Хранилище «${o.name}» · папка ${o.folder} · ${o.synced} файлов` +
    (o.last ? ` · сверено в ${new Date(o.last * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}` : "") : "";
  $("#ob-search").checked = o.search !== false;
  $("#ob-all").checked = !!o.sync_all;
  const r = $("#ob-res");
  if (o.missing) { r.className = "res bad"; r.textContent = `Папка хранилища не найдена: ${o.vault}. Подключите заново.`;
                   $("#ob-setup").style.display = ""; }
  else if (o.error) { r.className = "res bad"; r.textContent = o.error; }
  else if (st.conflicts) { r.className = "res"; r.textContent = `Заметку правили и в Obsidian, и здесь — сохранены обе версии (${st.conflicts}).`; }
}
$("#ob-pick").onclick = async () => { const a = api(); if (!a) return toast("Выбор папки доступен в окне студии");
  const p = await a.pick_folder(); if (!p) return;
  const sel = $("#ob-vault"); if (![...sel.options].some(o => o.value === p)) sel.insertAdjacentHTML("afterbegin", `<option value="${escH(p)}">${escH(p)}</option>`);
  sel.value = p; };
$("#ob-go").onclick = async (ev) => {
  const r = $("#ob-res"), p = $("#ob-vault").value; if (!p) return $("#ob-pick").click();
  ev.target.disabled = true; r.className = "res"; r.textContent = "подключаю и переношу заметки…";
  try { const o = await core.call("obsidian_connect", { path: p }); obFill(o); r.className = "res ok";
        r.textContent = `Готово: заметки ассистента — в папке «${o.folder}» хранилища «${o.name}».`; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#ob-search").onchange = (e) => core.call("obsidian_set", { patch: { search: e.target.checked } }).then(obFill).catch(x => toast(x.message));
$("#ob-all").onchange = (e) => core.call("obsidian_set", { patch: { sync_all: e.target.checked } }).then(o => { obFill(o); toast(e.target.checked ? "Хранилище синхронизируется — первая отправка может занять пару минут" : "Синхронизация всего хранилища выключена"); core.call("sync_now").catch(() => {}); }).catch(x => toast(x.message));
$("#ob-sync").onclick = async (ev) => { const r = $("#ob-res"); ev.target.disabled = true;
  try { const o = await core.call("obsidian_sync"); obFill(o); const s = o.stats || {};
        r.className = "res ok"; r.textContent = `Сверено: в Obsidian — ${s.to_vault || 0}, оттуда — ${s.from_vault || 0}.`; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; } };
$("#ob-open").onclick = () => obLast && obLast.open && openExt(obLast.open);
$("#ob-off").onclick = () => core.call("obsidian_forget").then(o => { obFill(o); $("#ob-res").className = "res";
  $("#ob-res").textContent = "Obsidian отключён. Папка Dewiar в хранилище осталась — это ваши заметки."; }).catch(e => toast(e.message));
core.on("obsidian", e => obFill(e));

/* ---------- аккаунт сайта ----------
   Распознавание и голос «с сервера Dewiar» доступны после входа (a.services). Включает и выключает их ядро
   (account_stt/account_tts); локальная правка S — только чтобы кнопка сменилась сразу, точные настройки придут
   событием "settings". */
function renderSrvStt() {
  const a = lastAccount, on = S && S.stt && S.stt.engine === "remote" && S.stt.remote && S.stt.remote.from_account;
  $("#srv-stt").style.display = a && a.logged_in && a.services && a.services.stt ? "" : "none";
  $("#srv-stt-go").textContent = on ? "Выключить" : "Включить";
  $("#srv-stt-go").className = on ? "btn" : "btn primary";
  $("#srv-stt-res").className = on ? "res ok" : "res"; $("#srv-stt-res").textContent = on ? "Включено: распознаёт сервер" : "";
}
$("#srv-stt-go").onclick = async (ev) => {
  const on = !(S.stt.engine === "remote" && S.stt.remote && S.stt.remote.from_account);
  ev.target.disabled = true;
  try { await core.call("account_stt", { on }); S.stt.engine = on ? "remote" : "gigaam"; S.stt.remote = Object.assign({}, S.stt.remote, { from_account: on }); renderSrvStt(); toast(on ? "Распознавание — на сервере" : "Распознавание — на этом компьютере"); }
  catch (e) { toast(e.message); } finally { ev.target.disabled = false; }
};
/* «Слух» → «Сервер Dewiar»: распознавание через облако (ключ Dewiar или вход аккаунтом — device.cloud_stt) или сайт по
   аккаунту (services.stt). Адрес и ключ ядро подставляет само (cloud.set_stt / account_stt) — в поля «своего сервера»
   они не попадают на глаза. -> "cloud" | "account" | "" */
const dwSttOn = () => !!(S && S.stt && S.stt.engine === "remote" && S.stt.remote && (S.stt.remote.cloud || S.stt.remote.from_account));
function srvSttHow() {
  const a = lastAccount, d = typeof DW !== "undefined" && DW.dev;
  if (d && d.cloud_stt) return "cloud";
  if (a && a.logged_in && a.services && a.services.stt) return "account";
  return "";
}
function renderSrvSttOpt() { const o = $("#srv-stt-opt"); if (o) o.style.display = srvSttHow() || dwSttOn() ? "" : "none"; }
async function dwStt(on, then) {
  const how = on ? srvSttHow() : (S.stt.remote || {}).cloud ? "cloud" : "account";
  if (on && !how) { toast("Сервер Dewiar сейчас недоступен — войдите аккаунтом dewiar.pro или подключите ключ"); refreshStatic(); return; }
  try {
    const r = await core.call(how === "cloud" ? "cloud_stt" : "account_stt", { on });
    if (r && r.engine) S.stt = Object.assign({}, S.stt, r);
    else { S.stt.engine = on ? "remote" : "gigaam"; S.stt.remote = Object.assign({}, S.stt.remote, { from_account: on, cloud: false }); }
    if (!on && then) {              // свой сервер — с чистыми полями: адрес и ключ Dewiar туда не переносим
      const patch = { stt: { engine: then } };
      if (then === "remote") patch.stt.remote = { base_url: "", api_key: "", model: "", cloud: false, from_account: false };
      await save(patch, true);
    }
    toast(on ? "Речь распознаёт сервер Dewiar" : "Речь распознаёт " + (then === "remote" ? "ваш сервер — впишите его адрес" : "этот компьютер"));
  } catch (e) { toast(e.message, 4000); }
  refreshStatic();
}
$('nav button[data-p="ears"]') && $('nav button[data-p="ears"]').addEventListener("click", () => {
  core.call("device", { fresh: true }).then(x => { if (typeof DW !== "undefined") DW.dev = x; renderSrvSttOpt(); }).catch(() => {});
});
/* «Голос Dewiar» — как на телефоне: синтез сайта по аккаунту (services.tts) или облако Dewiar (ключ или вход аккаунтом,
   облако умеет озвучку — device.cloud_tts). -> "account" | "cloud" | "" */
function srvTtsHow() {
  const a = lastAccount, d = typeof DW !== "undefined" && DW.dev;
  if (a && a.logged_in && a.services && a.services.tts) return "account";
  if (d && d.cloud_tts) return "cloud";
  if (typeof DW !== "undefined" && !DW.dev && !srvTtsHow.asked) { srvTtsHow.asked = true; core.call("device").then(x => { DW.dev = x; renderSrvTts(); }).catch(() => {}); }
  return "";
}
/* «Голос Dewiar» — первый вариант в списке «Голос» (radio data-tts="dewiar"), а не отдельный блок где-то ещё:
   виден, когда облако или аккаунт умеют озвучку. Открыли раздел «Голос» — спрашиваем облако заново (fresh): озвучку
   могли только что включить на сервере, перезапуск программы не нужен. */
const dwVoiceOn = () => !!(S && S.tts && S.tts.engine === "remote" && S.tts.remote && S.tts.remote.from_account);
function renderSrvTts() {
  const on = dwVoiceOn();
  $("#srv-tts").style.display = srvTtsHow() || on ? "" : "none";
  $("#srv-tts-res").className = on ? "res ok" : "res"; $("#srv-tts-res").textContent = on ? "Включено: говорит сервер Dewiar" : "";
  if (typeof S !== "undefined" && S && S.tts && typeof refreshStatic === "function") refreshStatic();
}
/* on — включить; выключить — сразу на выбранный голос компьютера (then) */
async function dwVoice(on, then) {
  const how = on ? srvTtsHow() : (S.tts.remote || {}).cloud ? "cloud" : "account";
  if (on && !how) { toast("Голос Dewiar сейчас недоступен — нет связи с облаком"); renderSrvTts(); return; }
  try {
    const t = await core.call(how === "cloud" ? "cloud_tts" : "account_tts", { on });
    if (t && t.engine) S.tts = Object.assign({}, S.tts, t); else { S.tts.engine = on ? "remote" : "vosk"; S.tts.remote = Object.assign({}, S.tts.remote, { from_account: on }); }
    if (!on && then && then !== S.tts.engine) { S.tts.engine = then; await save({ tts: { engine: then } }); }
    toast(on ? "Говорит «Голос Dewiar» — как на телефоне" : "Голос — этого компьютера");
  } catch (e) { toast(e.message); }
  renderSrvTts();
}
$('nav button[data-p="voice"]') && $('nav button[data-p="voice"]').addEventListener("click", () => {
  core.call("device", { fresh: true }).then(x => { if (typeof DW !== "undefined") DW.dev = x; renderSrvTts(); }).catch(() => {});
});
function renderAccount(a) {
  if (!a) return;
  lastAccount = a;
  setTimeout(renderSrvStt, 0); setTimeout(renderSrvTts, 0); setTimeout(renderSrvSttOpt, 0); setTimeout(tgSharedVis, 0);
  $("#acc-out").style.display = a.logged_in ? "none" : ""; $("#acc-in").style.display = a.logged_in ? "" : "none";
  if (!$("#acc-server").value && a.server) $("#acc-server").placeholder = a.server;
  if (!a.logged_in && a.login && !$("#acc-login").value) $("#acc-login").value = a.login;
  const host = (a.server || "").replace(/^https?:\/\//, "");
  $("#acc-who").innerHTML = a.logged_in ? `Вы вошли как <b>${escH(a.user && a.user.login || a.login)}</b> · ${escH(host)}` : "";
  $("#acc-res").textContent = a.problem || "";
}
$("#acc-go").onclick = async () => {
  const login = $("#acc-login").value.trim(), password = $("#acc-pass").value;
  const code = /^\d{6}$/.test(login.replace(/[\s-]/g, "")) ? login.replace(/[\s-]/g, "") : "";   // в поле логина — 6 цифр: это код   // код с другого устройства (№167)
  if (code) { $("#acc-res").textContent = "Это код с другого устройства — подключаю по нему…";
    return core.call("cloud_connect", { key: code }).then(() => { $("#acc-pass").value = ""; $("#acc-res").textContent = "Подключено по коду."; })
      .catch(e => { $("#acc-res").textContent = e.message + " (код живёт 10 минут — возьмите новый на том устройстве)"; }); }
  if (!login || !password) return ($("#acc-res").textContent = "Введите логин и пароль");
  $("#acc-go").disabled = true; $("#acc-res").textContent = "Вхожу…";
  try { const a = await core.call("account_login", { server: $("#acc-server").value.trim(), login, password }); $("#acc-pass").value = ""; renderAccount(a); toast("Вход выполнен"); }
  catch (e) { $("#acc-res").textContent = e.message; } finally { $("#acc-go").disabled = false; }
};
$("#acc-pass").onkeydown = (e) => { if (e.key === "Enter") $("#acc-go").click(); };
$("#acc-out-btn").onclick = () => core.call("account_logout").then(renderAccount).then(() => ($("#acc-answer").textContent = "")).catch(e => toast(e.message));
$("#acc-ask").onclick = async () => {
  const q = $("#acc-q").value.trim(); if (!q) return;
  $("#acc-ask").disabled = true; $("#acc-answer").textContent = "Ищу в Знаниях…";
  try { const r = await core.call("account_ask", { question: q }); $("#acc-answer").textContent = r.answer + (r.sources.length ? "\n\nИсточники: " + r.sources.join("; ") : ""); }
  catch (e) { $("#acc-answer").textContent = e.message; } finally { $("#acc-ask").disabled = false; }
};
$("#acc-q").onkeydown = (e) => { if (e.key === "Enter") $("#acc-ask").click(); };
core.on("account", e => renderAccount(e.account));

/* ---------- «Сообщить о проблеме» (core/health.py): complaint_send — слова, снимок экрана (bug_shot снимает экран; из виджета —
   до открытия студии, событие bug_shot или bug_state при открытии), подпись для «Помощников проекта»; complaints — мои
   жалобы с ответами разработчика и помощники проекта; report — только сохранить отчёт файлом; report_offer — ядро
   заметило сбой и предлагает отправлять такие отчёты самому ---------- */
let bugPreview = "";
function bugShow(preview) {
  bugPreview = preview || "";
  const img = $("#bug-img"); img.hidden = !bugPreview; if (bugPreview) img.src = bugPreview;
  $("#bug-empty").hidden = !!bugPreview; $("#bug-use").checked = !!bugPreview; $("#bug-use").disabled = !bugPreview;
  $("#bug-shot").classList.remove("off");
}
function bugFocus(preview) {                                       // из виджета: на «О программе», к форме, снимок — сразу виден
  const b = $('nav button[data-p="about"]'); if (b && !b.classList.contains("on")) b.click();
  if (preview) bugShow(preview);
  setTimeout(() => { $("#bug").scrollIntoView({ behavior: "smooth", block: "start" }); $("#bug").classList.add("flash"); $("#rep-note").focus(); }, 120);
  setTimeout(() => $("#bug").classList.remove("flash"), 1800);
}
const BUG_ST = { new: "получена сервером", email:"отправлена по почте", queued:"ожидает отправки", seen: "прочитана", work: "в работе", fixed: "исправлено", no: "не ошибка" };
function reportResult(x) {
  return x.delivery === 'queued' ? 'Отчёт сохранён локально и ожидает отправки. '+(x.reason || '')
    : x.delivery === 'email' ? 'Отчёт отправлен на domatskevich@gmail.com, тема «Ошибка обработки персональный агент».'
    : `Отчёт №${x.id} получен сервером обработки.`;
}
async function loadReportDelivery() {
  try {
    const data=await core.call('report_delivery_status');
    $('#report-delivery').textContent=(data.server_configured ? 'Сервер задан. ' : 'Сервер не задан — используется почта. ')+(data.mail_configured ? 'Почта отправителя подключена.' : 'Для отправки подключите почту отправителя.');
    $('#report-pending').innerHTML=(data.pending || []).map(item=>`<div class="ritem"><div class="rtxt"><b>Отчёт ожидает отправки</b><span>${escH(item.reason || '')}</span></div><button class="btn small" data-report-retry="${escH(item.queue_id)}">Повторить отправку</button></div>`).join('');
    $$('#report-pending [data-report-retry]').forEach(button=>button.onclick=async()=>{
      button.disabled=true;
      try {
        const result=await core.call('report_delivery_retry',{queue_id:button.dataset.reportRetry});
        const r=$('#rep-res');r.className='res'+(result.delivery === 'queued' ? '' : ' ok');r.textContent=reportResult(result);
        await loadReportDelivery(); core.call('complaints').then(bugRender).catch(()=>{});
      } catch(error){toast(error.message);button.disabled=false;}
    });
  } catch(_) { $('#report-delivery').textContent='Не удалось проверить доставку отчётов.'; }
}
$('#report-mail-setup').onclick=()=>document.querySelector('nav button[data-p="mail"]').click();
document.querySelector('nav button[data-p="about"]').addEventListener('click',loadReportDelivery);
core.on('hello',()=>{if(document.querySelector('#p-about.cur'))loadReportDelivery();});
function bugRender(v) {
  if (!v) return;
  if (v.who !== undefined && !$("#bug-who").value) $("#bug-who").value = v.who || "";
  const mine = (v.mine || []).slice(0, 8);
  $("#bug-mine").innerHTML = mine.length ? '<div class="bugh">Мои жалобы</div>' + mine.map(c =>
    `<div class="bugc"><span class="n" title="${escH(String(c.id))}">№${escH(String(c.id).length>18 ? String(c.id).slice(0,12)+'…' : String(c.id))}</span><span class="t">${escH(c.note || "")}</span><span class="st"><span class="chip ${escH(c.status)}">${escH(BUG_ST[c.status] || c.status)}`
    + `${c.status === "fixed" && c.fixed ? " в " + escH(c.fixed) : ""}</span>${new Date(c.ts * 1000).toLocaleDateString("ru")}${c.shot ? " · со снимком" : ""}`
    + `${c.reward ? ` · спасибо ${c.reward} тал.` : ""}</span>${c.reply ? `<span class="reply">${escH(c.reply)}</span>` : ""}</div>`).join("") : "";
  const th = v.thanks || [];
  $("#bug-thanks").innerHTML = th.length ? '<div class="bugh">Помощники проекта — их жалобы сделали Dewiar лучше</div><div class="thx">'
    + th.map(p => `<span title="подтверждено: ${p.n}, исправлено: ${p.fixed}">${escH(p.name)}<b>${p.n}</b></span>`).join("") + "</div>" : "";
}
$("#bug-img").onclick = (e) => e.target.classList.toggle("zoom");
$("#bug-use").onchange = (e) => $("#bug-shot").classList.toggle("off", !e.target.checked);
$("#bug-snap").onclick = guardBtn(async () => {
  const r = $("#rep-res"); r.className = "res"; r.textContent = "снимаю экран…";
  try { const x = await core.call("bug_shot"); if (x.ok) { bugShow(x.preview); r.textContent = ""; } else { r.className = "res bad"; r.textContent = "Не снялся: " + (x.error || ""); } }
  catch (e) { r.className = "res bad"; r.textContent = e.message; }
});
/* своя картинка к жалобе: Ctrl+V в форме (снимок из буфера), «Картинка…» или перетащить на место снимка (№139) */
async function bugPick(file) {
  if (!file || !/^image\//.test(file.type)) return false;
  const r = $("#rep-res"); r.className = "res"; r.textContent = "прикладываю…";
  const data = await new Promise((ok, no) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = no; fr.readAsDataURL(file); });
  try { const x = await core.call("bug_shot", { image: data }); if (x.ok) { bugShow(x.preview); r.textContent = ""; } else { r.className = "res bad"; r.textContent = x.error || "не приложилось"; } }
  catch (e) { r.className = "res bad"; r.textContent = e.message; }
  return true;
}
$("#bug").addEventListener("paste", (e) => { const f = [...(e.clipboardData || {}).files || []].find(x => /^image\//.test(x.type)); if (f) { e.preventDefault(); bugPick(f); } });
$("#bug-file").onchange = (e) => { bugPick(e.target.files[0]); e.target.value = ""; };
$("#bug-shot").addEventListener("dragover", (e) => { e.preventDefault(); $("#bug-shot").classList.add("drop"); });
$("#bug-shot").addEventListener("dragleave", () => $("#bug-shot").classList.remove("drop"));
$("#bug-shot").addEventListener("drop", (e) => { e.preventDefault(); $("#bug-shot").classList.remove("drop"); bugPick((e.dataTransfer.files || [])[0]); });
$("#bug-send").onclick = guardBtn(async () => {
  const r = $("#rep-res"), note = $("#rep-note").value.trim();
  if (note.length < 3) { r.className = "res bad"; r.textContent = "Напишите хотя бы пару слов — что пошло не так"; $("#rep-note").focus(); return; }
  r.className = "res"; r.textContent = "собираю и отправляю…";
  try {
    const x = await core.call("complaint_send", { note, who: $("#bug-who").value.trim(), shot: !!bugPreview && $("#bug-use").checked });
    r.className = x.delivery === 'queued' ? 'res' : 'res ok'; r.textContent = reportResult(x);
    $("#rep-note").value = ""; bugShow("");
    core.call("complaints").then(bugRender).catch(() => {});
    loadReportDelivery();
  } catch (e) { r.className = "res bad"; r.textContent = e.message; }
});
function guardBtn(fn) { return async (ev) => { const b = ev.currentTarget; if (b.disabled) return; b.disabled = true; try { await fn(ev); } finally { b.disabled = false; } }; }
core.on("bug_shot", e => bugFocus(e.preview));
core.on("complaints", bugRender);
$('nav button[data-p="about"]') && $('nav button[data-p="about"]').addEventListener("click", () => core.call("complaints").then(bugRender).catch(() => {}));
core.on("hello", () => core.call("bug_state").then(x => { if (x && x.preview) bugFocus(x.preview); }).catch(() => {}));
core.on("report_offer", e => { $("#rep-offer-t").textContent = e.summary ? `(${e.summary.slice(0, 160)})` : ""; $("#rep-offer").hidden = false; });
const repAuto = (on) => core.call("report_auto", { on }).then(() => { $("#rep-offer").hidden = true; const cb = document.querySelector('[data-k="report.auto"]'); if (cb) cb.checked = on; toast(on ? "При сбое отчёт уйдёт сам" : "Хорошо, не отправляю"); });
$("#rep-yes").onclick = () => repAuto(true);
$("#rep-no").onclick = () => repAuto(false);
$("#rep-go").onclick = async (ev) => {
  ev.target.disabled = true; const r = $("#rep-res"); r.className = "res"; r.textContent = "собираю…";
  try { const path = await core.call("report"); r.className = "res ok"; r.textContent = "Сохранено: " + path; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#up-check").onclick = () => { $("#up-state").textContent = "Проверяю…"; core.call("update_check").then(renderUpdate).catch(e => toast(e.message)); };
core.on("update", e => renderUpdate(e.update));

/* ---------- встречи (core/prompter.py): окно, итоги и локальные аудио/видео ---------- */
$("#pr-open").onclick = () => { const a = api(); if (a && a.open_prompter) a.open_prompter(); else core.call("prompter_open").catch(e => toast(e.message)); };
const PR_ICONS = {
  summary: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M9 3.5h6v2.5H9zM8.5 11l1.5 1.5 3-3M8.5 16h7"/>',
  play: '<circle cx="12" cy="12" r="8.5"/><path d="M10.2 8.6v6.8l5.6-3.4z" fill="currentColor"/>',
  audio: '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  video: '<rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="M10 9.2v5.6l4.8-2.8z" fill="currentColor"/>',
  frames: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.7"/><path d="m4 17.5 5-4.5 3.5 3 3-2.5 4.5 4"/>',
  text: '<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z"/><path d="M14 3.5V8h4M9.5 12h6M9.5 15h6M9.5 18h4"/>',
  folder: '<path d="M3.5 7a1.5 1.5 0 0 1 1.5-1.5h4l2 2h8a1.5 1.5 0 0 1 1.5 1.5v8.5a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
};
const prSvg = (k) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${PR_ICONS[k]}</svg>`;
let prMeetings = [];
/* Итог и текст записи — во встроенном редакторе (раздел «Папка»), а не во внешней программе */
function prEdit(path) {
  const nav = $('nav button[data-p="folder"]');
  if (nav) nav.click();
  setTimeout(() => (typeof openFile === "function" ? openFile(path) : core.call("ws_open", { path }).catch(e => toast(e.message))), 250);
}
/* Полоса над файлом встречи во встроенном редакторе: итог ↔ текст записи ↔ прослушать ↔ кадры ↔ папка.
   В режиме правки ссылки в тексте не нажимаются — переходы вынесены сюда. path — открытый файл. */
async function prMeetingBar(path) {
  const m1 = /^Встречи\/([^/]+)\.md$/.exec(path), m2 = /^Встречи\/Расшифровки\/([^/]+)\.txt$/.exec(path);
  const stem = (m1 || m2 || [])[1];
  if (!stem) return null;
  let list = prMeetings;
  if (!list.some(x => x.name === stem)) { try { list = prMeetings = await core.call("prompter_meetings"); } catch (e) { return null; } }
  const m = list.find(x => x.name === stem);
  if (!m) return null;
  const bar = document.createElement("div");
  bar.className = "pr-fbar"; bar.setAttribute("role", "toolbar"); bar.setAttribute("aria-label", "Файлы встречи");
  const b = (label, title, on, active) => `<button class="btn small${active ? " on" : ""}" title="${escH(title)}" ${on ? "" : "disabled"}>${label}</button>`;
  bar.innerHTML = `<span>Встреча:</span>`
    + b("Итог", "Итог встречи", true, !!m1)
    + b("Текст записи", m.transcript ? "Текст записи со всеми участниками" : "Текста записи нет", !!m.transcript, !!m2)
    + b("▶ Прослушать", m.audio || Object.keys(m.tracks || {}).length ? "Звук, кадры и текст синхронно" : "Аудиозаписи нет: " + (m.audio_error || "звук не записывался"), !!(m.audio || Object.keys(m.tracks || {}).length))
    + (m.frames_count ? b(`Кадры (${m.frames_count})`, "Кадры экрана", true) : "")
    + b("Папка", "Открыть папку встречи в Finder", true);
  const [sum, txt, play, ...rest] = bar.querySelectorAll("button");
  const nm = m.name.replace(/^(\d{4})-(\d\d)-(\d\d) (\d\d)(\d\d) /, "$3.$2.$1 $4:$5 — ");
  sum.onclick = () => m1 || openFile(m.path);
  txt.onclick = () => m2 || openFile(m.transcript);
  play.onclick = () => prPlayer(m, nm, play, "frames");
  if (m.frames_count) rest[0].onclick = () => prGallery(m.frames, nm, rest[0]);
  rest[rest.length - 1].onclick = () => core.call("ws_open", { path: m.folder || "Встречи" }).catch(e => toast(e.message));
  return bar;
}
function loadMeetings() {
  Promise.all([core.call("prompter_meetings"), core.call("prompter_interrupted")]).then(([l, interrupted]) => {
    prMeetings = l || [];
    const recovery = $("#pr-interrupted");
    recovery.innerHTML = (interrupted || []).map((m, i) => `<div class="pr-row"><div class="pr-it pr-static"><b>${escH(m.title || m.name)}</b><span>Прерванная запись · ${Math.floor((m.duration_s || 0) / 60)} мин · WAV ${m.audio_mb} МБ</span></div>
      <button class="btn primary pr-recover" data-recover="${i}">Восстановить расшифровку и итог</button></div>`).join("");
    const nm = (m) => m.name.replace(/^(\d{4})-(\d\d)-(\d\d) (\d\d)(\d\d) /, "$3.$2.$1 $4:$5 — ");
    const parts = (m) => [m.audio && "аудио", Object.keys(m.tracks || {}).length > 1 && "две дорожки", m.video && "видео", m.frames_count && `кадров: ${m.frames_count}`]
      .filter(Boolean).join(" · ") || "только итог";
    // Перед корзиной: звук (всегда — без записи кнопка неактивна и объясняет почему), видео и кадры (если есть),
    // расшифровка и папка встречи.
    const btn = (cls, k, title, attrs, extra = "") => `<button class="pr-ic ${cls}" ${attrs} title="${escH(title)}" aria-label="${escH(title)}">${prSvg(k)}${extra}</button>`;
    $("#pr-list").innerHTML = prMeetings.length ? prMeetings.map((m, i) => `<div class="pr-row"><div class="pr-it pr-static"><b>${escH(nm(m))}</b><span>${escH(parts(m))}</span></div>
        ${btn("pr-pill", "summary", "Итог встречи — открыть во встроенном редакторе", `data-edit="${escH(m.path)}"`, "<em>Итог</em>")}
        ${m.transcript ? btn("pr-pill", "text", "Текст записи встречи со всеми участниками — открыть во встроенном редакторе", `data-edit="${escH(m.transcript)}"`, "<em>Текст</em>")
                       : btn("pr-pill", "text", "Текста записи нет: встреча не расшифровывалась", "disabled aria-disabled=\"true\"", "<em>Текст</em>")}
        ${m.audio || Object.keys(m.tracks || {}).length ? btn("pr-main", "play", "Прослушать встречу: дорожки, кадры и текст синхронно", `data-player="${i}"`)
                  : btn("", "play", "Аудиозаписи нет: " + (m.audio_error || "звук не записывался"), "disabled aria-disabled=\"true\"")}
        ${m.video ? btn("", "video", "Просмотреть видео встречи", `data-player="${i}" data-mode="video"`) : ""}
        ${m.frames_count ? btn("", "frames", `Просмотреть кадры экрана (${m.frames_count})`, `data-frames="${i}"`, `<i class="pr-badge">${m.frames_count > 99 ? "99+" : m.frames_count}</i>`) : ""}
        ${btn("", "folder", "Открыть папку встречи", `data-media="${escH(m.folder || "Встречи")}"`)}
        <button class="pr-del" data-p="${escH(m.path)}" data-n="${escH(nm(m))}" title="Удалить итог встречи" aria-label="Удалить итог встречи ${escH(nm(m))}"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 7h15M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/></svg></button></div>`).join("")
      : `<p class="hint">Пока встреч не было. Итог каждой появится здесь.</p>`;
    $$("#pr-list .pr-ic[data-media]").forEach(b => b.onclick = () => core.call("ws_open", { path: b.dataset.media }).catch(e => toast(e.message)));
    $$("#pr-list .pr-ic[data-edit]").forEach(b => b.onclick = () => prEdit(b.dataset.edit));
    $$("#pr-list .pr-ic[data-frames]").forEach(b => b.onclick = () => { const m = prMeetings[+b.dataset.frames]; if (m) prGallery(m.frames, nm(m), b); });
    $$("#pr-list .pr-ic[data-player]").forEach(b => b.onclick = () => { const m = prMeetings[+b.dataset.player]; if (m) prPlayer(m, nm(m), b, b.dataset.mode || "frames"); });
    // удалить итог — в корзину, как всё удаление в Dewiar (отзыв 17.10: «как удалить прошлую встречу?»)
    $$("#pr-list .pr-del").forEach(b => b.onclick = async () => {
      if (!await ask(`Удалить итог встречи «${b.dataset.n}»? Файл уйдёт в корзину — вернуть можно оттуда.`)) return;
      core.call("prompter_meeting_remove", { path: b.dataset.p }).then(() => { toast("Итог встречи — в корзине"); loadMeetings(); }).catch(e => toast(e.message, 4000));
    });
    $$("#pr-interrupted .pr-recover").forEach(b => b.onclick = async () => {
      const meeting = interrupted[+b.dataset.recover]; if (!meeting) return;
      if (!await ask(`Восстановить запись «${meeting.title}» за ${Math.floor((meeting.duration_s || 0) / 60)} мин? WAV останется на месте; будут созданы расшифровка и итог.`)) return;
      b.disabled = true; b.textContent = "Восстанавливаю…";
      core.call("prompter_recover", { id: meeting.id }).then(() => { toast("Восстановление записи запущено"); loadMeetings(); })
        .catch(e => { b.disabled = false; b.textContent = "Восстановить расшифровку и итог"; toast(e.message, 4000); });
    });
  }).catch(() => {});
}
/* Просмотр кадров прошлой встречи: стрелки ← →, Esc — закрыть, щелчок по кадру — открыть оригинал. */
function prGallery(frames, title, opener) {
  if (!frames || !frames.length) return;
  const cache = new Map();
  let i = 0;
  const box = document.createElement("div");
  box.className = "pr-gal"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Кадры встречи " + title);
  box.innerHTML = `<div class="pr-gal-top"><b>${escH(title)}</b><span class="pr-gal-n"></span>
      <button class="pr-gal-x" title="Закрыть (Esc)" aria-label="Закрыть просмотр">✕</button></div>
    <div class="pr-gal-body"><button class="pr-gal-nav" data-d="-1" title="Предыдущий кадр (←)" aria-label="Предыдущий кадр">‹</button>
      <figure><img alt=""><figcaption></figcaption></figure>
      <button class="pr-gal-nav" data-d="1" title="Следующий кадр (→)" aria-label="Следующий кадр">›</button></div>`;
  document.body.appendChild(box);
  const img = box.querySelector("img"), cap = box.querySelector("figcaption");
  const stamp = (t) => t == null ? "" : `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
  const load = (k) => cache.has(k) ? cache.get(k) : (cache.set(k, core.call("prompter_meeting_frame", { path: frames[k] })), cache.get(k));
  async function show(k) {
    i = (k + frames.length) % frames.length;
    box.querySelector(".pr-gal-n").textContent = `${i + 1} из ${frames.length}`;
    img.classList.add("busy"); cap.textContent = "Загружаю…";
    try {
      const want = i, r = await load(want);
      if (want !== i) return;                                            // уже листнули дальше
      img.src = r.url; img.alt = r.name; img.title = "Открыть оригинал";
      cap.textContent = [stamp(r.t) && "на " + stamp(r.t), r.name].filter(Boolean).join(" · ");
    } catch (e) { cap.textContent = e.message; img.removeAttribute("src"); }
    img.classList.remove("busy");
    if (frames.length > 1) load((i + 1) % frames.length).catch(() => {});      // следующий — заранее
  }
  const close = () => { document.removeEventListener("keydown", onKey, true); box.remove(); if (opener) opener.focus(); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); show(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); show(i - 1); }
  };
  document.addEventListener("keydown", onKey, true);
  box.querySelector(".pr-gal-x").onclick = close;
  box.onclick = (e) => { if (e.target === box) close(); };
  box.querySelectorAll(".pr-gal-nav").forEach(b => { b.onclick = () => show(i + +b.dataset.d); b.hidden = frames.length < 2; });
  img.onclick = () => core.call("ws_open", { path: frames[i] }).catch(e => toast(e.message));
  box.querySelector(".pr-gal-x").focus();
  show(0);
}
/* Проигрыватель встречи: дорожка (общая / Вы / Собеседник), кадры и расшифровка по одной шкале — часам записи.
   Кадр показывается тот, что был на экране в эту секунду (даже без собранного видео); реплика подсвечивается,
   щелчок по реплике или отметке кадра — перемотка. Пробел — пауза, ← → — на 5 секунд, Esc — закрыть. */
async function prPlayer(meeting, title, opener, mode) {
  let d;
  try { d = await core.call("prompter_meeting_player", { path: meeting.path }); }
  catch (e) { toast(e.message, 4000); return; }
  if (!d.tracks.length && !d.video) { toast("У этой встречи нет звука"); return; }
  const fmt = (t) => { t = Math.max(0, Math.floor(t || 0)); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m).padStart(2, "0")) + ":" + String(s).padStart(2, "0"); };
  const who = { me: "Вы", them: "Собеседник" };
  const box = document.createElement("div");
  box.className = "pr-gal pr-pl"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
  box.setAttribute("aria-label", "Проигрыватель встречи " + title);
  box.innerHTML = `<div class="pr-gal-top"><b>${escH(title)}</b>
      <div class="pr-seg" role="group" aria-label="Дорожка">${d.tracks.map(t => `<button data-track="${t.key}" title="${t.key === "mix" ? "Обе стороны вместе" : "Только " + escH(t.label.toLowerCase())}">${escH(t.label)}</button>`).join("")}</div>
      ${d.video ? `<div class="pr-seg" role="group" aria-label="Изображение"><button data-mode="frames" title="Кадры экрана по времени записи">Кадры</button><button data-mode="video" title="Собранное видео встречи">Видео</button></div>` : ""}
      <button class="pr-gal-x" title="Закрыть (Esc)" aria-label="Закрыть проигрыватель">✕</button></div>
    <div class="pr-pl-spk" aria-label="Участники"></div>
    <div class="pr-pl-body">
      <figure class="pr-pl-stage"><img alt=""><video playsinline hidden></video><figcaption></figcaption></figure>
      <ol class="pr-pl-lines" aria-label="Расшифровка">${d.lines.map((l, k) => `<li data-k="${k}" data-t="${l.t}" class="w-${escH(l.who)}${l.speaker ? " s-" + escH(l.speaker) : ""}" tabindex="0" title="${(d.speakers || []).length > 1 ? "Правая кнопка — кто сказал" : ""}"><time>${fmt(l.t)}</time><b>${escH(l.label || who[l.who] || l.who)}</b> ${escH(l.text)}</li>`).join("") || '<li class="pr-pl-empty">Расшифровки нет</li>'}</ol>
    </div>
    <div class="pr-pl-bar"><div class="pr-pl-marks" title="Кадры экрана: щелчок — перейти">${d.frames.map((f, k) => `<i data-k="${k}" style="left:${d.duration ? (100 * f.t / d.duration).toFixed(3) : 0}%" title="Кадр ${k + 1} — ${fmt(f.t)}"></i>`).join("")}</div>
      <audio controls preload="metadata"></audio></div>`;
  document.body.appendChild(box);
  prSpeakers(box, meeting, title, opener, mode, d);
  const audio = box.querySelector("audio"), video = box.querySelector("video"), img = box.querySelector("img");
  const cap = box.querySelector("figcaption"), list = box.querySelector(".pr-pl-lines");
  const items = [...list.querySelectorAll("li[data-t]")];
  let media = audio, curFrame = -2, curLine = -2, track = (d.tracks.find(t => t.key === "mix") || d.tracks[0] || {}).key;
  const lastAt = (arr, t) => { let lo = 0, hi = arr.length - 1, r = -1; while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= t) { r = mid; lo = mid + 1; } else hi = mid - 1; } return r; };
  const frameT = d.frames.map(f => f.t), lineT = d.lines.map(l => l.t);
  function sync() {
    const t = media.currentTime + 0.05;
    if (media === audio) {
      const k = lastAt(frameT, t);
      if (k !== curFrame) {
        curFrame = k;
        if (k >= 0) { img.src = d.frames[k].url; img.hidden = false; }
        else img.hidden = true;
        box.querySelectorAll(".pr-pl-marks i").forEach((m, n) => m.classList.toggle("on", n === k));
      }
      cap.textContent = d.frames.length ? (curFrame >= 0 ? `Кадр ${curFrame + 1} из ${d.frames.length} · на экране с ${fmt(d.frames[curFrame].t)}` : "До первого кадра")
        : "Кадров экрана нет — слушайте и читайте расшифровку";
    } else cap.textContent = "Видео встречи";
    const j = lastAt(lineT, t);
    if (j !== curLine) {
      curLine = j;
      items.forEach((li, n) => li.classList.toggle("on", n === j));
      if (j >= 0 && !list.matches(":hover")) items[j].scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }
  function seek(t) { media.currentTime = Math.max(0, t); sync(); }
  function setTrack(key) {
    const tr = d.tracks.find(t => t.key === key); if (!tr) return;
    const at = audio.currentTime || 0, playing = !audio.paused;
    track = key; audio.src = tr.url;
    audio.addEventListener("loadedmetadata", () => { audio.currentTime = Math.min(at, audio.duration || at); if (playing) audio.play().catch(() => {}); }, { once: true });
    box.querySelectorAll("[data-track]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.track === key)));
  }
  function setMode(m) {
    const at = media.currentTime || 0, playing = !media.paused;
    media.pause();
    mode = m === "video" && d.video ? "video" : "frames";
    media = mode === "video" ? video : audio;
    video.hidden = mode !== "video"; img.hidden = mode === "video" || curFrame < 0;
    box.querySelector(".pr-pl-bar").classList.toggle("video", mode === "video");
    box.querySelectorAll("[data-track]").forEach(b => b.disabled = mode === "video");
    if (mode === "video" && !video.src) { video.src = d.video; video.controls = true; }
    const go = () => { media.currentTime = at; if (playing) media.play().catch(() => {}); sync(); };
    media.readyState >= 1 ? go() : media.addEventListener("loadedmetadata", go, { once: true });
    box.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
    curFrame = -2;
  }
  [audio, video].forEach(el => { el.addEventListener("timeupdate", () => el === media && sync()); el.addEventListener("seeked", () => el === media && sync()); });
  items.forEach(li => { li.onclick = () => { seek(+li.dataset.t); media.play().catch(() => {}); };
    li.onkeydown = (e) => { if (e.key === "Enter") li.click(); }; });
  box.querySelectorAll(".pr-pl-marks i").forEach(m => m.onclick = () => seek(d.frames[+m.dataset.k].t));
  box.querySelectorAll("[data-track]").forEach(b => b.onclick = () => setTrack(b.dataset.track));
  box.querySelectorAll("[data-mode]").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  img.onclick = () => curFrame >= 0 && core.call("ws_open", { path: meeting.frames.find(p => p.endsWith("/" + d.frames[curFrame].name)) || meeting.folder }).catch(e => toast(e.message));
  const close = () => { audio.pause(); video.pause(); audio.removeAttribute("src"); video.removeAttribute("src");
    document.removeEventListener("keydown", onKey, true); box.remove(); if (opener) opener.focus(); };
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.target.closest && e.target.closest("button, li, audio, video")) return;
    else if (e.key === " ") { e.preventDefault(); media.paused ? media.play().catch(() => {}) : media.pause(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); seek(media.currentTime + 5); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); seek(media.currentTime - 5); }
  };
  document.addEventListener("keydown", onKey, true);
  box.querySelector(".pr-gal-x").onclick = close;
  if (d.tracks.length) setTrack(track); else mode = "video";
  setMode(mode);
  sync();
  box.querySelector(".pr-gal-x").focus();
}

/* Участники по голосу (core/diarization): разбить / пересчитать, переименовать, объединить, переназначить реплику,
   аудио каждого участника. Правки хранятся отдельно (.speakers.json) и переживают пересчёт. */
async function prSpeakers(box, meeting, title, opener, mode, d) {
  const panel = box.querySelector(".pr-pl-spk"); if (!panel) return;
  const reopen = () => { box.querySelector(".pr-gal-x").click(); prPlayer(meeting, title, opener, mode); };
  let v;
  try { v = await core.call("prompter_meeting_speakers", { path: meeting.path }); }
  catch (e) { panel.textContent = ""; return; }
  const sp = (v.speakers || []).map(s => ({ id: s.id, label: s.display || s.label || s.id, sec: s.speech_sec }));
  const busy = v.job && v.job.busy;
  const stage = { diarize: "голоса", transcribe: "расшифровка", align: "сборка" };
  const opt = (sel) => [0, 1, 2, 3, 4, 5, 6, 8, 10].map(n => `<option value="${n}"${n === sel ? " selected" : ""}>${n ? n : "авто"}</option>`).join("");
  panel.innerHTML = `<div class="pr-spk-row">
      ${sp.length ? sp.map((s, k) => `<span class="pr-spk c${k % 6}" data-id="${escH(s.id)}"><b tabindex="0" title="Переименовать">${escH(s.label)}</b>${s.sec ? `<small>${Math.round(s.sec / 60) || "<1"} мин</small>` : ""}${s.id !== "ME" && sp.length > 2 ? `<select title="Объединить с другим участником (один и тот же человек)"><option value="">≡</option>${sp.filter(x => x.id !== s.id).map(x => `<option value="${escH(x.id)}">→ ${escH(x.label)}</option>`).join("")}</select>` : ""}</span>`).join("")
        : `<span class="pr-spk-none">${v.can_diarize ? "Разбиение по участникам не выполнялось" : "Нет аудиозаписи — разбить по участникам нельзя"}</span>`}
      ${v.can_diarize ? `<span class="pr-spk-act"><select class="pr-spk-n" title="Собеседников без вас">${opt(0)}</select>
        <button class="pr-spk-run" ${busy ? "disabled" : ""} title="Разделить реплики по голосам; правки участников сохраняются">${busy ? `Идёт: ${stage[v.job.stage] || ""} ${Math.round(100 * (v.job.progress || 0))}%` : (sp.length ? "Пересчитать участников" : "Разбить по участникам")}</button>
        ${sp.length ? `<button class="pr-spk-audio" title="WAV каждого участника (по времени встречи)">Аудио участников</button>` : ""}</span>` : ""}
    </div>${v.job && v.job.error ? `<div class="pr-spk-err">${escH(v.job.error)}</div>` : ""}${(v.not_transferred || []).length ? `<div class="pr-spk-err">Не перенесено после пересчёта: ${(v.not_transferred || []).filter(x => x.kind === "name").map(x => "«" + escH(x.value) + "»").join(", ") || "часть правок"} — назначьте заново</div>` : ""}`;
  const txtNote = (t) => { if (t && t.alt) toast("Текст расшифровки правили вручную — он не изменён. Новая разметка — рядом, в «.участники.txt»", 6000);
    else if (t && t.manual) toast("Ваши правки текста сохранены — изменены только подписи", 4000); };
  const edit = async (args) => {
    try { const r = await core.call("prompter_meeting_speakers_edit", Object.assign({ path: meeting.path }, args)); txtNote(r && r.txt); reopen(); }
    catch (e) { toast(e.message, 4000); }
  };
  panel.querySelectorAll(".pr-spk b").forEach(b => {
    const go = () => {
      const id = b.closest(".pr-spk").dataset.id, inp = document.createElement("input");
      inp.value = b.textContent; inp.className = "pr-spk-in"; inp.setAttribute("aria-label", "Имя участника");
      b.replaceWith(inp); inp.focus(); inp.select();
      let done = false;
      const save = (ok) => { if (done) return; done = true; if (ok && inp.value.trim() !== b.textContent) edit({ action: "rename", id, name: inp.value }); else inp.replaceWith(b); };
      inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") save(true); else if (e.key === "Escape") save(false); };
      inp.onblur = () => save(true);
    };
    b.onclick = go; b.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } };
  });
  panel.querySelectorAll(".pr-spk select").forEach(s => s.onchange = () => s.value && edit({ action: "merge", id: s.closest(".pr-spk").dataset.id, to: s.value }));
  const run = panel.querySelector(".pr-spk-run");
  if (run) run.onclick = async () => {
    run.disabled = true;
    try { await core.call("prompter_meeting_speakers_diarize", { path: meeting.path, expected_speakers: Number(panel.querySelector(".pr-spk-n").value || 0) || null }); }
    catch (e) { toast(e.message, 4000); run.disabled = false; return; }
    poll();
  };
  const audioBtn = panel.querySelector(".pr-spk-audio");
  if (audioBtn) audioBtn.onclick = async () => {
    audioBtn.disabled = true;
    try { const r = await core.call("prompter_meeting_speakers_audio", { path: meeting.path }); toast(`Аудио участников: ${r.files.length} файл(ов) — дорожки в проигрывателе`); reopen(); }
    catch (e) { toast(e.message, 4000); audioBtn.disabled = false; }
  };
  function poll() {
    setTimeout(async () => {
      if (!document.body.contains(box)) return;
      let s; try { s = await core.call("prompter_meeting_speakers", { path: meeting.path }); } catch (_) { return; }
      const j = s.job || {};
      if (j.busy) { const b = panel.querySelector(".pr-spk-run"); if (b) b.textContent = `Идёт: ${stage[j.stage] || ""} ${Math.round(100 * (j.progress || 0))}%`; poll(); }
      else if (j.error) { toast(j.error, 5000); prSpeakers(box, meeting, title, opener, mode, d); }
      else { txtNote(j.result && j.result.txt); reopen(); }
    }, 1500);
  }
  if (busy) poll();
  // переназначить реплику: правая кнопка мыши по строке расшифровки
  if (sp.length > 1) box.querySelectorAll(".pr-pl-lines li[data-t]").forEach(li => li.oncontextmenu = (e) => {
    e.preventDefault();
    box.querySelectorAll(".pr-spk-menu").forEach(m => m.remove());
    const m = document.createElement("div"); m.className = "pr-spk-menu"; m.setAttribute("role", "menu");
    m.innerHTML = `<small>Сказал(а):</small>` + sp.map(s => `<button role="menuitem" data-id="${escH(s.id)}">${escH(s.label)}</button>`).join("");
    m.style.left = e.clientX + "px"; m.style.top = e.clientY + "px"; box.appendChild(m);
    m.querySelectorAll("button").forEach(b => b.onclick = () => { m.remove(); edit({ action: "reassign", t: +li.dataset.t, end: +li.dataset.t, source: d.lines[+li.dataset.k]?.source_speaker, to: b.dataset.id }); });
    setTimeout(() => document.addEventListener("click", () => m.remove(), { once: true }), 0);
  });
}
// Итог и видео создаются в фоне после остановки. Обновляем список без
// повторного открытия раздела, чтобы кнопка ▶ появилась сразу.
core.on("prompter", e => {
  if ((e.kind === "summary" && !e.busy) || (e.kind === "video" && !e.busy)) loadMeetings();
});

/* «О программе» → «Для отладки: что Лира сейчас учитывает» (24.10): части подсказки модели — размер, начало, где поправить */
async function ctxvLoad() {
  const box = $("#ctxv-list"); if (!box) return;
  box.innerHTML = '<div class="hint">Собираю…</div>';
  let r; try { r = await core.call("ctx_view"); } catch (e) { box.textContent = e.message; return; }
  const esc = (t) => String(t || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  box.innerHTML = r.parts.map(p => `<details class="ctxp${p.chars ? "" : " empty"}"><summary><b>${esc(p.title)}</b>`
    + `<span class="hint">${p.chars ? p.chars.toLocaleString("ru-RU") + " знаков" : "пусто"}${p.note ? " · " + esc(p.note) : ""}</span>`
    + `<button class="btn small ghost" data-go="${esc(p.go)}">Открыть</button></summary>`
    + (p.chars ? `<pre>${esc(p.text)}</pre>` : "") + `</details>`).join("")
    + `<p class="hint" style="margin-top:8px">Плюс последние ${r.turns} реплик разговора дословно. Всего подсказка — ${r.full_chars.toLocaleString("ru-RU")} знаков (с правилами и инструментами).</p>`;
  $$("#ctxv-list [data-go]").forEach(b => b.onclick = (e) => { e.preventDefault(); const n = $(`nav button[data-p="${b.dataset.go}"]`); if (n) n.click(); });
  $("#ctxv-pre").textContent = r.full || "";
}
if ($("#ctxv")) {
  $("#ctxv").addEventListener("toggle", () => { if ($("#ctxv").open) ctxvLoad(); });
  $("#ctxv-re").onclick = () => ctxvLoad();
  $("#ctxv-full").onclick = () => { const p = $("#ctxv-pre"); p.hidden = !p.hidden; $("#ctxv-full").textContent = p.hidden ? "Показать подсказку целиком" : "Скрыть подсказку"; };
}
