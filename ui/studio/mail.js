/* Почтовый центр: IMAP accounts, cached triage, editable rules and version-confirmed replies. */
let mlAccounts = [], mlSelected = "", mlCurrentMessage = null, mlDraft = null, mlRulesData = {};
let mlView = "all", mlAccountFilter = "", mlSelectedMessageId = "", mlCurrentThread = [], mlCurrentThreadId = "", mlScopeItems = [];
let mlBadgeFilters = { important: true, unread: false, unanswered: true };
let mlAttentionShown = false;
document.querySelector('nav button[data-p="mail"]').addEventListener("click", () => {
  if (core.connected) mlLoad().catch(e => { $("#ml-res").textContent = e.message; });
});

const ML_PASSWORD_STATE = {
  needs_access: "Нет доступа к паролю в Связке ключей — введите пароль или разрешите доступ",
  rejected: "Почта не приняла пароль — введите новый пароль приложения",
  locked: "Связка ключей заблокирована — разблокируйте её",
};
function mlStatus(a) {
  if (a.needs_password) return ML_PASSWORD_STATE[a.credential_state] || "Нужен пароль";
  const checked = a.last_checked ? " · проверено " + new Date(a.last_checked * 1000).toLocaleString() : "";
  return (a.enabled ? "Подключён" : a.configured ? "Требуется проверка" : "Нужно подключить") + checked + (a.last_error ? " · " + a.last_error : "");
}
/* Как в Apple Mail: ящик без рабочего пароля сразу показывает поле ввода, а не окно Связки ключей. */
function mlAskPassword(a) {
  const settings = document.querySelector(".ml-settings"); if (settings) settings.open = true;
  const r = $("#ml-res"); r.className = "res bad"; r.textContent = a.address + ": " + (ML_PASSWORD_STATE[a.credential_state] || "нужен пароль");
  $("#ml-pass").focus();
}
function mlRenderAccounts() {
  const box = $("#ml-accounts"); box.replaceChildren();
  mlAccounts.forEach(a => {
    const b = document.createElement("button"); b.type = "button";
    b.className = "ml-account" + (a.id === mlAccountFilter ? " selected" : ""); b.setAttribute("aria-pressed", String(a.id === mlAccountFilter));
    const name = document.createElement("b"), addr = document.createElement("span"), status = document.createElement("small");
    name.textContent = a.label; addr.textContent = a.address; status.textContent = mlStatus(a);
    status.className = a.needs_password ? "ml-error" : a.enabled ? "ml-ok" : a.last_error ? "ml-error" : "";
    b.append(name, addr, status); b.onclick = () => mlChooseAccount(a.id); box.appendChild(b);
  });
  const active = mlAccounts.filter(a => a.enabled).length;
  $("#ml-connection-summary").textContent = `${active} из ${mlAccounts.length} подключено`;
}
async function mlPublishScope(active = !!document.querySelector("#p-mail.cur")) {
  if (!core.connected) return;
  const selected = mlCurrentMessage ? { id: mlCurrentMessage.id, thread_id: mlCurrentThreadId,
    from: mlCurrentMessage.from || mlCurrentMessage.sender, subject: mlCurrentMessage.subject,
    date: mlCurrentMessage.date, account: mlCurrentMessage.account } : null;
  return core.call("mail_scope_update", { active, view: mlView, account_id: mlAccountFilter, selected,
    messages: active ? mlScopeItems.slice(0, 20).map(item => ({ id: item.id, thread_id: item.thread_id,
      from: item.from, subject: item.subject, date: item.date, account: item.account, thread_count: item.thread_count })) : [] });
}
function mlRenderImportanceRules(rules) {
  const box = $("#ml-importance-rules"); box.replaceChildren();
  if (!rules.length) { box.textContent = "Правил по отправителям и темам пока нет."; box.className = "ml-importance-rules hint"; return; }
  box.className = "ml-importance-rules";
  rules.forEach(rule => {
    const row = document.createElement("div"); row.className = "ml-importance-rule";
    const label = document.createElement("span");
    label.textContent = rule.sender_address ? "Письма от " + rule.sender_address : rule.recipient_address ?
      "Письма на адрес " + rule.recipient_address :
      "Тема " + (rule.subject_match === "exact" ? "совпадает с" : "содержит") + " «" + rule.subject + "»";
    if (rule.name) label.textContent += " · " + rule.name;
    const enabledLabel = document.createElement("label"), enabled = document.createElement("input");
    enabled.type = "checkbox"; enabled.checked = rule.enabled !== false; enabled.setAttribute("aria-label", "Правило включено");
    enabledLabel.append(enabled, document.createTextNode("Включено"));
    enabled.onchange = () => mlChangeImportanceRule(rule.id, { enabled: enabled.checked });
    const priority = document.createElement("select"); priority.setAttribute("aria-label", "Назначенная важность");
    [["important", "Важное"], ["normal", "Обычное"]].forEach(([value, title]) => {
      const option = document.createElement("option"); option.value = value; option.textContent = title; priority.appendChild(option);
    });
    priority.value = rule.importance || "important";
    priority.onchange = () => mlChangeImportanceRule(rule.id, { importance: priority.value });
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "btn"; remove.textContent = "Удалить";
    remove.onclick = () => mlRemoveImportanceRule(rule.id);
    row.append(label, enabledLabel, priority, remove); box.appendChild(row);
  });
}
async function mlSaveImportanceRules(nextRules) {
  const state = $("#ml-ir-state");
  try {
    const rules = JSON.parse($("#ml-rules").value); rules.importance_rules = nextRules;
    const result = await core.call("mail_rules_save", { rules });
    mlRulesData = result.rules; $("#ml-rules").value = JSON.stringify(mlRulesData, null, 2);
    mlRenderImportanceRules(mlRulesData.importance_rules || []); state.className = "res ok";
    state.textContent = "Правило сохранено и применено к заголовкам писем."; await mlList();
  } catch (e) { state.className = "res bad"; state.textContent = e.message; }
}
function mlChangeImportanceRule(id, patch) {
  mlSaveImportanceRules((mlRulesData.importance_rules || []).map(rule => rule.id === id ? { ...rule, ...patch } : rule));
}
function mlRemoveImportanceRule(id) {
  mlSaveImportanceRules((mlRulesData.importance_rules || []).filter(rule => rule.id !== id));
}
function mlSetView(view) {
  mlClearMessage();
  mlView = view; mlAccountFilter = "";
  const names = { all: "Все входящие", unread: "Непрочитанные", important: "Важные", important_unread: "Важные непрочитанные", unanswered: "Без ответа", badge_custom: "На значке" };
  document.querySelectorAll("[data-ml-view]").forEach(b => {
    const selected = b.dataset.mlView === view;
    b.classList.toggle("selected", selected); b.setAttribute("aria-pressed", String(selected));
  });
  $("#ml-view-title").textContent = names[view] || "Все входящие";
  $("#ml-view-subtitle").textContent = "Выберите письмо, чтобы прочитать его";
  mlRenderAccounts(); mlList();
}
function mlChooseAccount(id) {
  mlClearMessage();
  mlAccountFilter = id; mlView = "account";
  const a = mlAccounts.find(x => x.id === id);
  document.querySelectorAll("[data-ml-view]").forEach(b => { b.classList.remove("selected"); b.setAttribute("aria-pressed", "false"); });
  $("#ml-view-title").textContent = a ? a.label : "Входящие";
  $("#ml-view-subtitle").textContent = a ? a.address : "";
  mlRenderAccounts(); mlSelect(id); mlList();
}
function mlClearMessage() {
  mlCurrentMessage = null; mlDraft = null; mlSelectedMessageId = "";
  mlCurrentThread = []; mlCurrentThreadId = ""; $("#ml-thread-messages").replaceChildren(); $("#ml-thread-messages").hidden = true;
  $("#ml-detail").hidden = true; $("#ml-draft-preview").hidden = true;
}
async function mlSelect(id) {
  mlSelected = id; const a = mlAccounts.find(x => x.id === id); if (!a) return;
  const unlock = $("#ml-unlock"); if (unlock) unlock.title = `Разрешить Dewiar читать сохранённый пароль ящика ${a.address} из Связки ключей`;
  $("#ml-selected-address").textContent = a.address;
  $("#ml-name").value = a.name || ""; $("#ml-login").value = a.login || "";
  $("#ml-smtp").value = a.smtp_host || ""; $("#ml-sport").value = a.smtp_port || "";
  $("#ml-sec").value = a.smtp_security || "ssl"; $("#ml-imap").value = a.imap_host || ""; $("#ml-iport").value = a.imap_port || "";
  $("#ml-pass").value = ""; $("#ml-pass").placeholder = a.needs_password ? "введите пароль приложения заново" :
    a.has_password ? "пароль сохранён; введите новый только для замены" : "пароль приложения";
  $("#ml-on").style.display = a.configured ? "" : "none";
  $("#ml-state").textContent = a.enabled ? "Подключён: " + a.address : a.configured ? "Настройки сохранены" : "";
  mlRenderAccounts();
  const g = await core.call("mail_guess", { address: a.address });
  $("#ml-smtp").value ||= g.smtp_host; $("#ml-sport").value ||= g.smtp_port;
  $("#ml-sec").value ||= g.smtp_security; $("#ml-imap").value ||= g.imap_host; $("#ml-iport").value ||= g.imap_port;
  const help = $("#ml-help"); help.replaceChildren(); help.append(document.createTextNode("Используйте пароль приложения. "));
  if (g.help) { const link = document.createElement("a"); link.href = g.help; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.textContent = "Настройки безопасности почты"; help.append(link); }
}
async function mlLoad(selectAccountId = "") {
  mlAccounts = await core.call("mail_accounts");
  if (selectAccountId && mlAccounts.some(a => a.id === selectAccountId)) mlSelected = selectAccountId;
  else if (!mlSelected || !mlAccounts.some(a => a.id === mlSelected)) mlSelected = (mlAccounts.find(a => a.enabled) || mlAccounts[0] || {}).id || "";
  const unlock = $("#ml-unlock"); if (unlock) unlock.disabled = !mlSelected;
  // Открыли по значку «!» — сразу ящик, который ждёт пароль (один раз за открытие страницы).
  const waiting = !mlAttentionShown && new URLSearchParams(location.search).has("attention") && mlAccounts.find(a => a.needs_password);
  if (waiting) { mlAttentionShown = true; mlSelected = waiting.id; }
  mlRenderAccounts(); if (mlSelected) await mlSelect(mlSelected);
  if (waiting) mlAskPassword(waiting);
  const r = await core.call("mail_rules_get"); mlRulesData = r.rules; $("#ml-rules").value = JSON.stringify(r.rules, null, 2);
  mlRenderImportanceRules(r.rules.importance_rules || []);
  mlBadgeFilters = { important: true, unread: false, unanswered: true,
    ...((typeof S !== "undefined" && S && S.ui && S.ui.mail_badge_filters) || {}) };
  document.querySelectorAll("[data-mail-badge-filter]").forEach(input => {
    input.checked = !!mlBadgeFilters[input.dataset.mailBadgeFilter];
    input.onchange = mlSaveBadgeFilters;
  });
  $("#ml-rules-state").textContent = r.error || ""; await mlList();
  const params = new URLSearchParams(location.search);
  if (params.has("badge")) mlSetView("badge_custom");
  else if (params.has("important") && params.has("unread")) mlSetView("important_unread");
  else if (params.has("important")) mlSetView("important"); else if (params.has("unread")) mlSetView("unread");
}
const mlAddAccountToggle = $("#ml-add-account-toggle"), mlAddAccountForm = $("#ml-add-account-form");
mlAddAccountToggle.onclick = () => {
  const opening = mlAddAccountForm.hidden;
  mlAddAccountForm.hidden = !opening;
  mlAddAccountToggle.setAttribute("aria-expanded", String(opening));
  if (opening) $("#ml-new-address").focus();
};
$("#ml-add-account-cancel").onclick = () => {
  mlAddAccountForm.reset(); mlAddAccountForm.hidden = true; mlAddAccountToggle.setAttribute("aria-expanded", "false");
};
mlAddAccountForm.onsubmit = async event => {
  event.preventDefault();
  const submit = mlAddAccountForm.querySelector('[type="submit"]'); submit.disabled = true;
  try {
    const result = await core.call("mail_account_add", { address: $("#ml-new-address").value.trim(), label: $("#ml-new-label").value.trim() });
    mlAddAccountForm.reset(); mlAddAccountForm.hidden = true; mlAddAccountToggle.setAttribute("aria-expanded", "false");
    await mlLoad(result.account.id);
    const settings = document.querySelector(".ml-settings"); if (settings) settings.open = true;
    const status = $("#ml-res"); status.className = "res";
    status.textContent = "Ящик добавлен. Введите пароль приложения и нажмите «Сохранить и проверить».";
    $("#ml-pass").focus();
  } catch (error) { toast(error.message); }
  finally { submit.disabled = false; }
};
async function mlSaveBadgeFilters() {
  const next = Object.fromEntries([...document.querySelectorAll("[data-mail-badge-filter]")]
    .map(input => [input.dataset.mailBadgeFilter, input.checked]));
  const previous = mlBadgeFilters;
  mlBadgeFilters = next;
  try {
    S = await core.call("settings_save", { patch: { ui: { mail_badge_filters: next } } });
    toast("Условия значка сохранены.");
    if (mlView === "badge_custom") mlList();
  } catch (e) {
    mlBadgeFilters = previous;
    document.querySelectorAll("[data-mail-badge-filter]").forEach(input => { input.checked = !!previous[input.dataset.mailBadgeFilter]; });
    toast(e.message);
  }
}
async function mlList() {
  try {
    const important = mlView === "important" || mlView === "important_unread" || mlView === "unanswered" || (mlView === "badge_custom" && mlBadgeFilters.important);
    const unanswered = mlView === "unanswered" || (mlView === "badge_custom" && mlBadgeFilters.unanswered);
    const noBadgeFilters = mlView === "badge_custom" && !Object.values(mlBadgeFilters).some(Boolean);
    const items = noBadgeFilters ? [] : await core.call("mail_list", { limit: 150, query: $("#ml-query").value.trim(),
      account_id: mlAccountFilter, important, unanswered, incoming_only: mlView === "badge_custom",
      unread: mlView === "unread" || mlView === "important_unread" || (mlView === "badge_custom" && mlBadgeFilters.unread) });
    mlScopeItems = items;
    const box = $("#ml-list"); box.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("div"); empty.className = "ml-empty";
      const configured = mlAccounts.some(a => a.enabled);
      empty.innerHTML = noBadgeFilters
        ? "<b>Условия не выбраны</b><span>Отметьте, какие письма показывать на значке.</span>"
        : `<b>${configured ? "Писем пока нет" : "Подключите почтовый ящик"}</b><span>${configured ? "Измените поиск или выберите другое представление." : "Откройте «Подключение и управление ящиками», чтобы начать получать письма."}</span>`;
      box.appendChild(empty); mlPublishScope().catch(() => {}); return;
    }
    items.forEach(m => {
      const b = document.createElement("button"); b.type = "button";
      const threadUnread = Number(m.thread_unread_count) || (m.unread ? 1 : 0);
      b.className = "ml-message" + (m.thread_important || m.importance === "important" ? " important" : "") + (threadUnread ? " unread" : "") + (m.id === mlSelectedMessageId ? " selected" : "");
      b.setAttribute("role", "option"); b.setAttribute("aria-selected", String(m.id === mlSelectedMessageId));
      const top = document.createElement("span"), sender = document.createElement("b"), date = document.createElement("time");
      const subject = document.createElement("span"), meta = document.createElement("small");
      top.className = "ml-message-top"; sender.className = "ml-message-sender"; date.className = "ml-message-date";
      sender.textContent = m.from || m.sender || "(неизвестный отправитель)";
      date.textContent = m.date || ""; date.dateTime = m.date || "";
      subject.className = "ml-message-subject"; subject.textContent = m.subject || "(без темы)";
      const markers = [];
      if (Number(m.thread_count) > 1) markers.push(`${m.thread_count} писем в переписке`);
      if (threadUnread) markers.push(threadUnread === 1 ? "1 непрочитанное" : `${threadUnread} непрочитанных`);
      if (m.thread_important || m.importance === "important") markers.push("Важное");
      if (m.thread_answered || m.answered) markers.push("Отвечено");
      if (m.folder === "SENT") markers.push("Отправлено");
      if (m.auth_warning) markers.push("Проверьте подлинность");
      if ((m.received_on || []).length > 1) markers.push("На нескольких адресах");
      meta.className = "ml-message-meta"; meta.textContent = markers.join(" · ") || m.account;
      top.append(sender, date); b.append(top, subject, meta); b.onclick = () => mlOpen(m.id, m.thread_id); box.appendChild(b);
    });
    mlPublishScope().catch(() => {});
  } catch (e) { $("#ml-list").textContent = e.message; }
}
async function mlOpen(id, threadId = "") {
  try {
    mlCurrentThreadId = threadId;
    const m = await core.call("mail_open", { message_id: id }); mlCurrentMessage = m; mlDraft = null; mlSelectedMessageId = id;
    mlCurrentThread = threadId ? await core.call("mail_thread", { thread_id: threadId }) : [m];
    await mlList();
    const threadBox = $("#ml-thread-messages"); threadBox.replaceChildren(); threadBox.hidden = mlCurrentThread.length < 2;
    if (mlCurrentThread.length > 1) {
      const heading = document.createElement("b"); heading.className = "ml-thread-heading";
      heading.textContent = `Переписка · ${mlCurrentThread.length} писем`;
      threadBox.appendChild(heading);
      mlCurrentThread.forEach(item => {
        const entry = document.createElement("button"); entry.type = "button";
        entry.className = "ml-thread-item" + (item.id === id ? " selected" : "");
        const from = document.createElement("b"), subject = document.createElement("span"), date = document.createElement("time");
        from.textContent = item.from || item.sender || "(неизвестный отправитель)";
        subject.textContent = item.subject || "(без темы)"; date.textContent = item.date || "";
        entry.append(from, subject, date); entry.onclick = () => mlOpen(item.id, threadId); threadBox.appendChild(entry);
      });
    }
    $("#ml-detail").hidden = false;
    $("#ml-reply-prepare").disabled = m.folder === "SENT";
    $("#ml-message-subject").textContent = m.subject || "(без темы)";
    $("#ml-message-head").textContent = "От: " + m.from + "\nКому: " + m.to + "\nКопия: " + (m.cc || "—") +
      "\nПолучено: " + m.account + (m.folder === "SENT" ? " · Отправленные" : "") + "\nТема: " + m.subject + "\nДата: " + m.date + "\nВложения: " + ((m.files || []).join(", ") || "нет");
    if (/(?:spf|dkim|dmarc)\s*=\s*fail\b/i.test(m.authentication_results || "")) {
      $("#ml-message-head").textContent += "\nВНИМАНИЕ: SPF/DKIM/DMARC fail по результату почтового сервера.";
    }
    $("#ml-message-body").textContent = m.text || "(пустое письмо)"; $("#ml-reply-body").value = "";
    $("#ml-draft-preview").hidden = true; $("#ml-detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (e) { toast(e.message); }
}
$("#ml-close-message").onclick = () => { mlClearMessage(); mlList(); };
$("#ml-copy-message").onclick = async ev => {
  if (!mlCurrentMessage) return;
  const m = mlCurrentMessage;
  const value = `От: ${m.from || "—"}\nКому: ${m.to || "—"}\nКопия: ${m.cc || "—"}\nТема: ${m.subject || "(без темы)"}\nДата: ${m.date || "—"}\n\n${m.text || ""}`;
  ev.target.disabled = true;
  try {
    let copied = false;
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(value); copied = true; } catch (_) { /* webview may deny clipboard API */ }
    }
    if (!copied) {
      const input = document.createElement("textarea"); input.value = value; input.style.position = "fixed"; input.style.opacity = "0";
      document.body.appendChild(input); input.select();
      try { copied = document.execCommand("copy"); } finally { input.remove(); }
    }
    if (!copied) throw new Error("Не удалось скопировать письмо в буфер обмена.");
    toast("Письмо скопировано в буфер обмена.");
  } catch (e) { toast(e.message || "Не удалось скопировать письмо."); }
  finally { ev.target.disabled = false; }
};
$("#ml-delete-message").onclick = async ev => {
  if (!mlCurrentMessage) return;
  const m = mlCurrentMessage; ev.target.disabled = true;
  try {
    if (!await ask(`Переместить письмо «${m.subject || "(без темы)"}» из ${m.account} в Корзину?`,
      { title: "Удалить письмо", ok: "В Корзину", danger: true })) return;
    await core.call("mail_delete", { message_id: m.id });
    mlClearMessage(); await mlList(); toast("Письмо перемещено в Корзину.");
  } catch (e) { toast(e.message); }
  finally { ev.target.disabled = false; }
};
$("#ml-forward-message").onclick = async ev => {
  if (!mlCurrentMessage) return; ev.target.disabled = true;
  try { await mlShowDraft(await core.call("mail_forward_prepare", { message_id: mlCurrentMessage.id })); }
  catch (e) { toast(e.message); } finally { ev.target.disabled = false; }
};
async function mlShowDraft(d) {
  mlDraft = d; $("#ml-draft-preview").hidden = false;
  const forward = d.kind === "forward";
  $("#ml-forward-fields").hidden = !forward;
  if (forward) {
    $("#ml-draft-to").value = (d.to || []).join(", "); $("#ml-draft-cc").value = (d.cc || []).join(", ");
    $("#ml-draft-subject").value = d.subject || "";
    $("#ml-forward-attachments").textContent = d.forwarded_original ? "Исходное письмо и его вложения будут приложены файлом .eml." : "";
  }
  $("#ml-draft-meta").textContent = "Версия " + d.revision + " · От: " + d.from + " · Кому: " + d.to.join(", ") +
    " · Копия: " + (d.cc.join(", ") || "—") + " · Тема: " + d.subject;
  $("#ml-draft-original").textContent = "Исходное письмо: " + d.original.from + " · " + d.original.subject + "\n\n" + (d.original.text || "");
  $("#ml-draft-body").value = d.body;
  $("#ml-draft-state").textContent = "Проверьте адреса, тему и весь текст. Каждая правка создаёт новую версию.";
}
core.on("mail_reply_draft", async event => {
  if (!document.querySelector("#p-mail.cur")) return;
  try {
    if (event.message_id && mlCurrentMessage?.id !== event.message_id) await mlOpen(event.message_id, event.thread_id || "");
    const draft = await core.call("mail_draft_get", { draft_id: event.draft_id });
    $("#ml-detail").hidden = false;
    await mlShowDraft(draft);
    $("#ml-draft-preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (error) { toast("Черновик ответа создан, но не открылся в почтовом окне: " + error.message); }
});
function mlDraftPatch() {
  const patch = { body: $("#ml-draft-body").value };
  if (mlDraft?.kind === "forward") {
    patch.to = $("#ml-draft-to").value; patch.cc = $("#ml-draft-cc").value; patch.subject = $("#ml-draft-subject").value;
  }
  return patch;
}

$("#ml-save").onclick = async ev => {
  if (!mlSelected) return; const r = $("#ml-res"); ev.target.disabled = true; r.className = "res"; r.textContent = "Сохраняю секрет и проверяю IMAP/SMTP…";
  try {
    const a = mlAccounts.find(x => x.id === mlSelected);
    const result = await core.call("mail_account_save", { account_id: mlSelected, patch: {
      address: a.address, password: $("#ml-pass").value, name: $("#ml-name").value.trim(), login: $("#ml-login").value.trim(),
      smtp_host: $("#ml-smtp").value.trim(), smtp_port: +$("#ml-sport").value || 465, smtp_security: $("#ml-sec").value,
      imap_host: $("#ml-imap").value.trim(), imap_port: +$("#ml-iport").value || 993 }});
    $("#ml-pass").value = ""; await mlLoad();
    const ok = result.test.smtp === "ok" && result.test.imap === "ok";
    if (result.test.auth_rejected) {   // новый пароль не принят — сохранённый не изменён, поле снова ждёт ввода
      r.className = "res bad"; r.textContent = "Почта не приняла этот пароль, он не сохранён. Проверьте пароль приложения и введите его ещё раз.";
      $("#ml-pass").focus(); return;
    }
    r.className = ok ? "res ok" : "res bad";
    r.textContent = ok ? "IMAP и SMTP подключены. Запускаю синхронизацию заголовков." :
      [result.test.smtp !== "ok" ? "SMTP: " + result.test.smtp : "", result.test.imap !== "ok" ? "IMAP: " + result.test.imap : ""].filter(Boolean).join(" · ");
    if (ok) { await core.call("mail_sync"); await mlLoad(); }
  } catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; }
};
$("#ml-unlock") && ($("#ml-unlock").onclick = async ev => {
  if (!mlSelected) return; const r = $("#ml-res"); ev.target.disabled = true; r.className = "res";
  r.textContent = "macOS спросит пароль входа в систему. Нажмите «Разрешать всегда», чтобы Dewiar больше не спрашивал.";
  try {
    const x = await core.call("mail_account_unlock", { account_id: mlSelected });
    const ok = x.test.smtp === "ok" && x.test.imap === "ok"; await mlLoad();
    r.className = ok ? "res ok" : "res bad";
    r.textContent = ok ? "Доступ получен, IMAP и SMTP подключены." : [x.test.smtp !== "ok" ? "SMTP: " + x.test.smtp : "", x.test.imap !== "ok" ? "IMAP: " + x.test.imap : ""].filter(Boolean).join(" · ");
    if (ok) { await core.call("mail_sync"); await mlLoad(); }
  } catch (e) { r.className = "res bad"; r.textContent = e.message + " Можно просто ввести пароль приложения заново."; $("#ml-pass").focus(); }
  finally { ev.target.disabled = false; }
});
$("#ml-test").onclick = async ev => { ev.target.disabled = true; const r = $("#ml-res"); r.textContent = "Отправляю тестовое письмо в выбранный ящик…";
  try { const x = await core.call("mail_account_send_test", { account_id: mlSelected }); r.className = "res ok"; r.textContent = "Тест передан на " + x.to.join(", ") + "."; }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; } };
$("#ml-off").onclick = async () => { if (!mlSelected || !await ask("Отключить ящик и удалить пароль из Связки ключей?")) return;
  try { await core.call("mail_account_forget", { account_id: mlSelected }); await mlLoad(); $("#ml-res").textContent = "Ящик отключён."; } catch (e) { toast(e.message); } };
$("#ml-refresh").onclick = async ev => { ev.target.disabled = true; $("#ml-sync-state").textContent = "Проверяю подключённые ящики…";
  try { const r = await core.call("mail_sync"); await mlLoad(); const ok = r.accounts.filter(x => x.ok).length, bad = r.accounts.filter(x => !x.ok);
    $("#ml-sync-state").textContent = "Проверено ящиков: " + ok + "." + (bad.length ? " Ошибки: " + bad.map(x => x.account_id + ": " + x.error).join("; ") : " Ошибок нет.");
  } catch (e) { $("#ml-sync-state").textContent = e.message; } finally { ev.target.disabled = false; } };
document.querySelectorAll("[data-ml-view]").forEach(b => b.onclick = () => mlSetView(b.dataset.mlView));
$("#ml-query").oninput = () => mlList();
$("#ml-reply-prepare").onclick = async ev => { if (!mlCurrentMessage) return; ev.target.disabled = true;
  try { await mlShowDraft(await core.call("mail_reply_prepare", { message_id: mlCurrentMessage.id, body: $("#ml-reply-body").value })); }
  catch (e) { toast(e.message); } finally { ev.target.disabled = false; } };
$("#ml-draft-update").onclick = async ev => { if (!mlDraft) return; ev.target.disabled = true;
  try { await mlShowDraft(await core.call("mail_reply_revise", { draft_id: mlDraft.id, revision: mlDraft.revision, patch: mlDraftPatch() })); }
  catch (e) { toast(e.message); } finally { ev.target.disabled = false; } };
$("#ml-draft-send").onclick = async ev => {
  if (!mlDraft) return; const d = mlDraft; ev.target.disabled = true;
  try {
    const updated = await core.call("mail_reply_revise", { draft_id: d.id, revision: d.revision, patch: mlDraftPatch() });
    await mlShowDraft(updated);
    const question = "Отправить эту точную версию?\n\nОт: " + updated.from + "\nКому: " + updated.to.join(", ") +
      "\nКопия: " + (updated.cc.join(", ") || "—") + "\nТема: " + updated.subject + "\nВерсия: " + updated.revision + "\n\n" + updated.body;
    if (!await ask(question, {title: "Отправка письма", ok: "Отправить"})) return;
    const sent = await core.call("mail_reply_send", { draft_id: updated.id, revision: updated.revision, confirmed: true });
    $("#ml-draft-state").textContent = "Отправлено с " + sent.from + ". Состояние копии: " + sent.sent_copy + "."; await mlList();
  } catch (e) { $("#ml-draft-state").textContent = e.message; }
  finally { ev.target.disabled = false; }
};
$("#ml-rules-save").onclick = async ev => { const r = $("#ml-rules-state"); ev.target.disabled = true;
  try { const result = await core.call("mail_rules_save", { rules: JSON.parse($("#ml-rules").value) });
    mlRulesData = result.rules; $("#ml-rules").value = JSON.stringify(result.rules, null, 2); mlRenderImportanceRules(result.rules.importance_rules || []); r.className = "res ok";
    r.textContent = "Правила проверены, сохранены и применены к индексу."; await mlLoad(); }
  catch (e) { r.className = "res bad"; r.textContent = e.message; } finally { ev.target.disabled = false; } };

$("#ml-ir-add").onclick = async ev => {
  const sender = $("#ml-ir-sender").value.trim(), recipient = $("#ml-ir-recipient").value.trim();
  const subject = $("#ml-ir-subject").value.trim(), state = $("#ml-ir-state");
  if ([sender, recipient, subject].filter(Boolean).length !== 1) { state.className = "res bad"; state.textContent = "Заполните только одно поле: отправитель, адресат или тема."; return; }
  ev.target.disabled = true;
  try {
    const rules = JSON.parse($("#ml-rules").value), current = rules.importance_rules || [];
    const existing = current.find(rule => sender ? rule.sender_address?.toLowerCase() === sender.toLowerCase() :
      recipient ? rule.recipient_address?.toLowerCase() === recipient.toLowerCase() :
      !rule.sender_address && !rule.recipient_address && rule.subject?.trim().toLocaleLowerCase() === subject.toLocaleLowerCase() &&
      rule.subject_match === $("#ml-ir-match").value);
    const rule = { id: existing?.id || "priority-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7),
      name: $("#ml-ir-name").value.trim(), sender_address: sender, recipient_address: recipient, subject,
      subject_match: subject ? $("#ml-ir-match").value : "contains", importance: $("#ml-ir-importance").value, enabled: true };
    const next = existing ? current.map(item => item.id === existing.id ? rule : item) : [...current, rule];
    await mlSaveImportanceRules(next);
    if ($("#ml-ir-state").classList.contains("ok")) { $("#ml-ir-sender").value = ""; $("#ml-ir-recipient").value = ""; $("#ml-ir-subject").value = ""; $("#ml-ir-name").value = ""; }
  } catch (e) { state.className = "res bad"; state.textContent = e.message; }
  finally { ev.target.disabled = false; }
};

// A bridge reconnect must not activate Mail while the user is in another section.
function mlLoadIfVisible() {
  if (core.connected && document.querySelector('#p-mail.cur')) {
    mlLoad().catch(e => { $("#ml-res").textContent = e.message; });
  }
}
core.on("hello", mlLoadIfVisible);
mlLoadIfVisible();
window.addEventListener("dewiar:studio-page", event => mlPublishScope(event.detail?.page === "mail").catch(() => {}));
setInterval(() => { if (document.querySelector("#p-mail.cur") && !document.hidden) mlPublishScope().catch(() => {}); }, 10000);
window.addEventListener("focus", () => mlPublishScope().catch(() => {}));
