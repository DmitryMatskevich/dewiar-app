/* Студия: «Чат» — вся переписка с персонажами (просьба 14.10: «на компьютере не понятно, где наша с Лирой переписка; на
   телефоне это есть»). Лента — из ядра (chat_list, core/chatlog.py): что человек сказал голосом или написал и что ответили,
   по дням, старые сверху; «Показать раньше» подгружает предыдущие. Новые реплики приходят событием chat; пока модель
   отвечает — живой пузырь из событий answer. Внизу — поле: написать текстом (то же, что сказать, — команда say).
   Пузыри — те же, что в «Близких» (.cl-m), чтобы переписка везде выглядела одинаково. */
const CH = { items: [], more: false, loaded: false, live: null, query: "", request: 0, archived: false, thread: null, threads: [] };
function chatWho(pid) { return (personas || []).find(p => p.id === pid) || null; }
function chatDay(ts) {
  const d = new Date(ts * 1000), now = new Date(), y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Сегодня";
  if (d.toDateString() === y.toDateString()) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}
const chatSize = (n) => n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + " КБ" : (n / 1048576).toFixed(1).replace(".", ",") + " МБ";
function chatCopyText(text) {
  const fallback = () => new Promise((resolve, reject) => {
    const area = document.createElement("textarea"); area.value = text; area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0"; document.body.appendChild(area);
    area.focus(); area.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (e) { copied = false; }
    area.remove(); copied ? resolve(true) : reject(new Error("Буфер обмена недоступен"));
  });
  return navigator.clipboard?.writeText ? navigator.clipboard.writeText(text).then(() => true).catch(fallback) : fallback();
}
function chatBubble(x, lastWho) {
  const out = x.role === "user", p = out ? null : chatWho(x.who);
  const hm = new Date(x.ts * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  // имя персонажа — над его первым ответом за день и когда отвечает другой персонаж (не над каждым ответом)
  const head = !out && lastWho !== (x.who || "")
    ? `<div class="ch-who"><i style="background:${escH((p && p.color) || "#8A9BFF")}"></i>${escH((p && p.name) || "Ассистент")}</div>` : "";
  const body = !x.text ? "" : out ? `<div class="tx">${escH(x.text)}</div>` : `<div class="tx md">${md(x.text)}</div>`;
  // вложения: картинки — миниатюрами, файлы — плашками; нажать — открыть из «Входящих» папки ассистента
  // картинки к рассказу (show_images) — миниатюры со ссылкой на источник, лентой; свои вложения — из «Входящих»
  const web = (x.att || []).filter(a => a.kind === "image" && a.url && !a.path);
  const gal = web.length && window.DewiarRich ? DewiarRich.gallery(web.map(a => ({ src: a.thumb, page: a.url, full: a.full, title: a.name, author: a.author, license: a.license, site: a.site, source: a.source }))) : "";
  const att = (x.att || []).filter(a => !web.includes(a)).map(a => a.kind === "image" && a.thumb
    ? `<button class="ch-img" data-path="${escH(a.path || "")}" title="${escH(a.name)} — открыть"><img src="${escH(a.thumb)}" alt="${escH(a.name)}"></button>`
    : `<button class="cl-f" data-path="${escH(a.path || "")}" data-key="${escH(a.key || "")}" title="${escH(a.label || a.name)}"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3.5h8.5L19 8v12.5H6z"/><path d="M14 3.5V8h5"/></svg><span>${escH(a.name)}</span><small>${a.size ? chatSize(a.size) : ""}</small></button>`).join("");
  const copy = x.text ? `<button class="ch-copy" type="button" data-copy-id="${escH(x.id)}" title="Скопировать сообщение" aria-label="Скопировать сообщение"><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg></button>` : "";
  return `${head}<div class="cl-m${out ? " out" : ""}${gal ? " wide" : ""}" data-id="${escH(x.id)}">${x.via ? `<small class="ch-via">${x.via === "dev" ? "ответ разработчика" : "из мессенджера"}</small>` : ""}${att ? `<div class="ch-att">${att}</div>` : ""}${body}${gal}<time>${hm}</time>${copy}</div>`;
}
function chatRender(keep) {
  const box = $("#ch-msgs"); if (!box) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60, oldH = box.scrollHeight, oldTop = box.scrollTop;
  let html = CH.more ? `<button class="btn small ch-older" id="ch-older">Показать раньше</button>` : "", day = "", lastWho = null;
  CH.items.forEach((x) => {
    const d = chatDay(x.ts);
    if (d !== day) { html += `<div class="ch-day"><span>${escH(d)}</span></div>`; day = d; lastWho = null; }
    html += chatBubble(x, lastWho);
    if (x.role === "assistant") lastWho = x.who || "";
  });
  if (CH.live && lastWho === active) html += `<div class="cl-m ch-live${CH.live.media ? " wide" : ""}">${CH.live.text ? `<div class="tx md">${md(CH.live.text)}</div>` : `<div class="ch-dots"><i></i><i></i><i></i></div>`}${CH.live.media && window.DewiarRich ? DewiarRich.gallery(CH.live.media) : ""}</div>`;
  else if (CH.live) {
    const p = chatWho(active);
    html += `<div class="ch-who"><i style="background:${escH((p && p.color) || "#8A9BFF")}"></i>${escH((p && p.name) || "Ассистент")}</div>`
      + `<div class="cl-m ch-live${CH.live.media ? " wide" : ""}">${CH.live.text ? `<div class="tx md">${md(CH.live.text)}</div>` : `<div class="ch-dots"><i></i><i></i><i></i></div>`}${CH.live.media && window.DewiarRich ? DewiarRich.gallery(CH.live.media) : ""}</div>`;
  }
  $("#ch-empty").style.display = CH.items.length || CH.live ? "none" : "";
  $("#ch-empty b").textContent = CH.query ? "Ничего не найдено" : CH.archived ? "Архив пуст" : "Здесь будет ваша переписка";
  $("#ch-empty span").textContent = CH.query ? "Попробуйте другое слово или сбросьте поиск." : CH.archived ? "Сохранённые вопросы и ответы появятся здесь." : "Скажите «Лира, …» или напишите ниже — разговор появится в ленте.";
  $("#ch-box").classList.toggle("archived", CH.archived);
  $("#ch-clear").disabled = !CH.threads.some(t => t.id === CH.thread && t.count);
  $("#ch-archive").hidden = CH.archived;
  $("#ch-archive").disabled = $("#ch-clear").disabled;
  $("#ch-restore").hidden = !CH.archived;
  $("#ch-restore").disabled = !CH.thread;
  box.innerHTML = html;
  if (keep === "older") box.scrollTop = box.scrollHeight - oldH + oldTop;   // подгрузили раньше — лента не прыгает
  else if (keep !== "stay" || atBottom) box.scrollTop = box.scrollHeight;
  const o = $("#ch-older"); if (o) o.onclick = chatOlder;
  if (window.DewiarRich) DewiarRich.diagrams(box);      // ```mermaid в ответах — схемами
  $$("#ch-msgs [data-path]").forEach(b => b.onclick = () => {
    if (b.dataset.key) core.call("knowledge_open_file", { key: b.dataset.key }).catch(e => { $("#ch-res").textContent = e.message; });   // источник ответа
    else if (b.dataset.path) core.call("ws_open", { path: b.dataset.path }).catch(e => { $("#ch-res").textContent = e.message; }); });
  $$("#ch-msgs .ch-copy").forEach(b => b.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (b.dataset.copying) return;
    const item = CH.items.find(x => String(x.id) === String(b.dataset.copyId));
    if (!item || !item.text) return;
    b.dataset.copying = "1";
    try {
      await chatCopyText(item.text);
      b.classList.add("copied"); b.title = "Скопировано"; b.setAttribute("aria-label", "Скопировано");
      toast("Сообщение скопировано");
      setTimeout(() => { if (!document.body.contains(b)) return; b.classList.remove("copied"); b.title = "Скопировать сообщение"; b.setAttribute("aria-label", "Скопировать сообщение"); }, 1500);
    } catch (err) { toast("Не удалось скопировать сообщение", 4000, "error"); }
    finally { delete b.dataset.copying; }
  });
  const p = chatWho(active);
  $("#ch-text").placeholder = `Сообщение — ответит ${p ? p.name : "ассистент"}. Enter — отправить, Shift+Enter — новая строка`;
}
async function loadChat() {
  const request = ++CH.request;
  try {
    const threads = await core.call("chat_threads", {archived: CH.archived});
    if (request !== CH.request) return;
    CH.threads = threads.items || [];
    CH.thread = CH.archived ? (CH.threads.some(t => t.id === CH.thread) ? CH.thread : CH.threads[0]?.id || null) : threads.active;
    $("#ch-thread").innerHTML = CH.threads.map(t => `<option value="${escH(t.id)}">${escH(t.title)} (${t.count})</option>`).join('');
    $("#ch-thread").value = CH.thread || "";
    const r = CH.thread ? await core.call("chat_list", { limit: 80, q: CH.query, archived: CH.archived, chat_id: CH.thread }) : {items:[],more:false};
    if (request !== CH.request) return;
    CH.items = r.items || []; CH.more = !!r.more; CH.loaded = true;
    $("#ch-res").textContent = CH.query ? `Найдено сообщений: ${r.matched ?? CH.items.length}` : "";
  } catch (e) { if (request !== CH.request) return; $("#ch-res").textContent = e.message; }
  chatRender();
}
let chatSearchTimer;
$("#ch-search").oninput = () => {
  CH.query = $("#ch-search").value.trim(); CH.request++; CH.live = null;
  clearTimeout(chatSearchTimer); chatSearchTimer = setTimeout(loadChat, 250);
};
$("#ch-search-clear").onclick = () => {
  clearTimeout(chatSearchTimer); $("#ch-search").value = ""; CH.query = ""; loadChat(); $("#ch-search").focus();
};
$("#ch-scope").onchange = () => {
  clearTimeout(chatSearchTimer); CH.archived = $("#ch-scope").value === "archive"; CH.live = null; loadChat();
};
$("#ch-thread").onchange = async () => {
  try {
    if (!CH.archived) await core.call("chat_select", {chat_id: $("#ch-thread").value});
    CH.thread = $("#ch-thread").value; CH.live = null; await loadChat();
  } catch(e) { $("#ch-thread").value = CH.thread || ""; toast(e.message, 7000, "error"); }
};
$("#ch-new").onclick = async () => {
  const button = $("#ch-new"); button.disabled = true;
  try {
    await core.call("chat_select", {});
    CH.archived = false; CH.query = ""; CH.live = null;
    $("#ch-scope").value = "active"; $("#ch-search").value = "";
    await loadChat(); $("#ch-text").focus();
  } catch(e) { toast(e.message, 7000, "error"); }
  finally { button.disabled = false; }
};
async function chatManage(button) {
  const action = button.dataset.chatAction, dialog_id = CH.thread;
  if (!dialog_id) return;
  const archived = CH.archived;
  if (action === "delete" && !await ask("Удалить весь выбранный чат со всеми вопросами и ответами без возможности восстановления? Сохранённые файлы и отдельная память ассистента останутся.")) return;
  button.disabled = true;
  try {
    await core.call("chat_manage", {dialog_id, action, archived});
    await loadChat();
    toast(action === "delete" ? "Чат удалён" : action === "archive" ? "Чат перенесён в архив" : "Чат восстановлен в список бесед", 3500);
  } catch(e) { toast(e.message, 7000, "error"); }
  finally { button.disabled = false; }
}
core.on("chat_changed", () => { if (CH.loaded) loadChat(); });
$("#ch-archive").onclick = () => chatManage($("#ch-archive"));
$("#ch-restore").onclick = () => chatManage($("#ch-restore"));
$("#ch-clear").onclick = () => chatManage($("#ch-clear"));
async function chatOlder() {
  if (!CH.items.length) return;
  const request = ++CH.request;
  const r = await core.call("chat_list", { before: CH.items[0].id, limit: 80, q: CH.query, archived: CH.archived, chat_id: CH.thread }).catch(() => null);
  if (!r || request !== CH.request) return;
  CH.items = (r.items || []).concat(CH.items); CH.more = !!r.more;
  chatRender("older");
}
core.on("chat", e => {
  const x = e.item; if (!x || !CH.loaded) return;
  if (CH.archived) return;
  if (x.role === "assistant") CH.live = null;
  loadChat();
});
// пока модель отвечает — живой пузырь (текст по мере ответа); готовый ответ придёт событием chat
core.on("answer", e => {
  if (!CH.loaded || e.done || CH.query || CH.archived) return;
  CH.live = { text: e.text || "" };
  if ($("#p-chat").classList.contains("cur")) chatRender("stay");
});
// картинки к рассказу появляются, пока персонаж ещё говорит, — сразу в живом пузыре
core.on("media", e => {
  if (!CH.loaded || e.source !== "pics" || CH.query || CH.archived) return;
  CH.live = Object.assign(CH.live || { text: "" }, { media: (CH.live && CH.live.media || []).concat(e.items || []).slice(0, 12) });
  if ($("#p-chat").classList.contains("cur")) chatRender("stay");
});
core.on("state", e => {
  if (!CH.loaded || CH.query || CH.archived) return;
  if (e.state === "thinking" && !CH.live) { CH.live = { text: "" }; if ($("#p-chat").classList.contains("cur")) chatRender("stay"); }
  if ((e.state === "idle" || e.state === "listening") && CH.live && !CH.live.text && !CH.live.media) { CH.live = null; if ($("#p-chat").classList.contains("cur")) chatRender("stay"); }
});
/* вложения к сообщению: скрепка, перетащить в ленту, Ctrl+V; картинки уменьшаются до 1600 px (как в виджете),
   файлы — до 11 МБ; ядро кладёт всё во «Входящие» папки ассистента и показывает модели (текст документов, картинки) */
let chAtts = [];                                       // {kind: "image"|"file", name, b64, size, url?}
const CH_MAX = 11e6;
function chatShrink(file) {
  return new Promise((ok, bad) => {
    const img = new Image(); img.onerror = () => bad(new Error("это не картинка"));
    img.onload = () => { const k = Math.min(1, 1600 / Math.max(img.width, img.height)), c = document.createElement("canvas");
      c.width = Math.round(img.width * k); c.height = Math.round(img.height * k); c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src); ok(c.toDataURL("image/jpeg", 0.88)); };
    img.src = URL.createObjectURL(file);
  });
}
const chatB64 = (file) => new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = bad; r.readAsDataURL(file); });
async function chatAddFiles(list) {
  for (const f of list) {
    try {
      if (/^image\//.test(f.type) && !/svg/.test(f.type)) {
        const url = await chatShrink(f);
        chAtts.push({ kind: "image", name: (f.name || "картинка").replace(/\.\w+$/, "") + ".jpg", b64: url, url, size: Math.round(url.length * 0.75) });
      } else if (f.size > CH_MAX) $("#ch-res").textContent = `«${f.name}» больше 11 МБ — положите его в папку ассистента`;
      else chAtts.push({ kind: "file", name: f.name, b64: await chatB64(f), size: f.size });
    } catch (e) { $("#ch-res").textContent = `«${f.name}»: ${e.message}`; }
  }
  chAtts = chAtts.slice(0, 10);
  chatAttsRender();
}
function chatAttsRender() {
  $("#ch-atts").innerHTML = chAtts.map((a, i) => `<span class="ch-chip">${a.kind === "image" ? `<img src="${escH(a.url)}" alt="">` : `<i>${escH((a.name.split(".").pop() || "").slice(0, 4).toUpperCase())}</i>`}<b>${escH(a.name)}</b><button data-i="${i}" title="Убрать" aria-label="Убрать">✕</button></span>`).join("");
  $$("#ch-atts button").forEach(b => b.onclick = () => { chAtts.splice(+b.dataset.i, 1); chatAttsRender(); });
}
function chatSend() {
  const t = $("#ch-text"), q = t.value.trim(); if (!q && !chAtts.length) return;
  const files = chAtts.map(a => ({ name: a.name, b64: a.b64 }));
  const atts = chAtts;
  t.value = ""; t.style.height = ""; chAtts = []; chatAttsRender(); $("#ch-res").textContent = "";
  if (!core.connected) $("#ch-res").textContent = "Нет связи с ядром — сообщение не потеряется: отправлю, как только ядро вернётся";
  core.sendLater("say", { text: q, files }).then(() => { if (/отправлю, как только/.test($("#ch-res").textContent)) $("#ch-res").textContent = ""; })
    .catch(e => {   // не дошло — текст обратно в поле, набирать заново не нужно (№200)
      if (!t.value.trim()) { t.value = q; t.dispatchEvent(new Event("input")); }
      if (!chAtts.length) { chAtts = atts; chatAttsRender(); }
      $("#ch-res").textContent = e.message + " — текст вернул в поле, отправьте ещё раз";
    });
}
$("#ch-file").onchange = (e) => { chatAddFiles([...e.target.files]); e.target.value = ""; };
$("#ch-text").addEventListener("paste", (e) => {
  const files = [...(e.clipboardData && e.clipboardData.items || [])].filter(x => x.kind === "file").map(x => x.getAsFile()).filter(Boolean);
  if (files.length) { e.preventDefault(); chatAddFiles(files); return; }
  // WebKitGTK часто не отдаёт странице картинку из буфера — спрашиваем окно (Api.clipboard_image, как в виджете)
  if (e.clipboardData && !e.clipboardData.getData("text")) { e.preventDefault(); chatClipImage(); }
});
async function chatClipImage() {
  const a = window.api && window.api(); let url = "";
  try { url = a && a.clipboard_image ? await a.clipboard_image() : ""; } catch (e) {}
  if (!url) { $("#ch-res").textContent = "В буфере обмена нет картинки — скопируйте снимок и вставьте ещё раз."; return; }
  chAtts.push({ kind: "image", name: "Снимок " + new Date().toLocaleTimeString("ru-RU").replace(/:/g, "-") + (/^data:image\/png/.test(url) ? ".png" : ".jpg"), b64: url, url,
                size: Math.round(url.length * 0.75) });
  chAtts = chAtts.slice(0, 10); chatAttsRender();
}
{ const box = $("#ch-box");
  box.addEventListener("dragover", (e) => { if ([...(e.dataTransfer.types || [])].includes("Files")) { e.preventDefault(); box.classList.add("over"); } });
  box.addEventListener("dragleave", (e) => { if (!box.contains(e.relatedTarget)) box.classList.remove("over"); });
  box.addEventListener("drop", (e) => { e.preventDefault(); box.classList.remove("over"); chatAddFiles([...(e.dataTransfer.files || [])]); }); }
$("#ch-go").onclick = chatSend;
$("#ch-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); chatSend(); } });
$("#ch-text").addEventListener("input", (e) => { const t = e.target; t.style.height = ""; t.style.height = Math.min(160, t.scrollHeight) + "px"; });
core.on("hello", () => { if ($("#p-chat").classList.contains("cur")) loadChat(); });
