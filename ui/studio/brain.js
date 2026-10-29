/* Студия Dewiar: «Модели» (профили моделей), простые поля настроек data-k, «Послушать» голос, звук и состояние,
   «Руки» (инструменты), «Напоминания» и готовые дела по расписанию.
   Поля с data-k="a.b.c" сохраняются сами (bindFields) и заполняются из S (refreshStatic — её зовёт save() после
   каждого сохранения). Команды ядра: llm_test, llm_models, discover, voice_test, vosk_voices, devices, tools,
   reminders, reminder_add/parse/update/cancel, job_add, job_run, alarm_test; события state (ход «Послушать»), reminders.
   Файлы студии подключаются по порядку из ui/settings.html и делят одну глобальную область; при загрузке здесь только
   вешаются обработчики. S_presets, S_fx (dewiar.js), wizardCheck (connect.js) нужны позже; RI отсюда берёт links.js. */
/* ---------- модели ----------
   llm.profiles в patch ядро заменяет целиком (settings._merge: удалённый профиль не должен вернуться), поэтому
   любая правка профиля шлёт весь словарь S.llm.profiles. */
let openProf = null, profSig = "";
let activeModelRole = "communication";
const ROLE_MAX_TOKENS_DEFAULT = {communication: 1024, planning: 16384, coding: 8192};
const MODEL_ROLE_DEFS = {
  communication: {
    label: "Общение",
    description: "Обычные ответы, вопросы и задачи, которым не назначено планирование или программирование."
  },
  planning: {
    label: "Планирование",
    description: "Построение планов, обсуждение вариантов, совет моделей и оценка готовности решения."
  },
  coding: {
    label: "Программирование",
    description: "Разработка программ, работа с проектом, тесты, исправления и ревью кода."
  }
};
const profileModelLists = new Map();
let modelRoutingStatus = null, modelRoutingRequest = 0, modelRoutingLastRefresh = 0;
function profileRoles(p) {
  return Array.isArray(p?.roles) ? p.roles : ["communication"];
}
function profileInRole(p, role = activeModelRole) {
  return profileRoles(p).includes(role);
}
function roleMaxTokens(role = activeModelRole) {
  const stored = (typeof S !== "undefined" && S?.llm?.role_max_tokens || {})[role];
  const n = Number(stored ?? ROLE_MAX_TOKENS_DEFAULT[role] ?? ROLE_MAX_TOKENS_DEFAULT.communication);
  return Number.isFinite(n) ? Math.max(128, Math.min(32768, Math.round(n))) : ROLE_MAX_TOKENS_DEFAULT.communication;
}
function roleEntries() {
  return Object.entries(S?.llm?.profiles || {}).filter(([, p]) => profileInRole(p));
}
function renderModelRoleTabs() {
  const def = MODEL_ROLE_DEFS[activeModelRole] || MODEL_ROLE_DEFS.communication;
  $$('[data-model-role]').forEach(button => {
    const selected = button.dataset.modelRole === activeModelRole;
    button.classList.toggle("on", selected);
    button.setAttribute("aria-selected", selected ? "true" : "false");
    button.tabIndex = selected ? 0 : -1;
  });
  const title = $("#model-role-title"), description = $("#model-role-description");
  if (title) title.textContent = def.label;
  if (description) description.textContent = def.description;
  const limit = $("#model-role-max-tokens");
  if (limit && document.activeElement !== limit) limit.value = roleMaxTokens();
}
function setupModelRoleTabs() {
  $$('[data-model-role]').forEach(button => {
    button.onclick = () => {
      activeModelRole = button.dataset.modelRole in MODEL_ROLE_DEFS ? button.dataset.modelRole : "communication";
      openProf = null;
      modelRoutingStatus = null;
      renderModelRoleTabs();
      renderProfiles();
      loadModelRoutingStatus();
    };
    button.onkeydown = event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = $$('[data-model-role]');
      const index = tabs.indexOf(button);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); tabs[next].focus(); tabs[next].click();
    };
  });
  const limit = $("#model-role-max-tokens");
  if (limit) limit.onchange = async () => {
    if (typeof S === "undefined" || !S?.llm) return;
    if (!limit.reportValidity()) return;
    const value = Math.max(128, Math.min(32768, Math.round(Number(limit.value))));
    limit.value = value;
    limit.disabled = true;
    try {
      const role_max_tokens = {...(S.llm.role_max_tokens || {}), [activeModelRole]: value};
      S = await core.call("settings_save", {patch: {llm: {role_max_tokens}}});
      renderModelRoleTabs();
      toast(`Лимит «${MODEL_ROLE_DEFS[activeModelRole].label}»: ${value} токенов`);
    } catch (e) {
      limit.value = roleMaxTokens();
      toast(e.message, 7000, "error");
    } finally { limit.disabled = false; }
  };
  renderModelRoleTabs();
}
setupModelRoleTabs();
window.modelRoleForNewProfile = () => activeModelRole;
$("#model-role-add-button").onclick = async () => {
  const select = $("#model-role-add-select"), id = select?.value;
  if (!id || !S?.llm?.profiles?.[id]) return;
  const current = S.llm.profiles[id], roles = [...new Set([...profileRoles(current), activeModelRole])];
  select.disabled = true;
  try {
    const profiles = {...S.llm.profiles, [id]: {...current, roles}};
    S = await core.call("settings_save", {patch: {llm: {profiles}}});
    openProf = id; renderProfiles(); loadModelRoutingStatus();
    toast(`Модель добавлена в «${MODEL_ROLE_DEFS[activeModelRole].label}»`);
  } catch (e) { toast(e.message, 7000, "error"); }
  finally { select.disabled = false; }
};
function modelQuotaText(q, kind) {
  const parts = [];
  if (q.budget_remaining_usd != null) parts.push(`Бюджет API: $${Number(q.budget_remaining_usd).toFixed(2)}`);
  if (q.key_remaining_usd != null) parts.push(`Лимит средств ключа: $${Number(q.key_remaining_usd).toFixed(2)}`);
  for (const bucket of q.buckets || []) {
    for (const w of bucket.windows || []) {
      const label = w.minutes ? (w.minutes >= 1440 ? `${w.minutes / 1440} дней` : `${w.minutes / 60} ч`) : ({five_hour:"5 ч",seven_day:"7 дней"}[w.name] || w.name);
      const left = w.remaining_percent == null ? "остаток не сообщён" : `осталось ${Number(w.remaining_percent).toFixed(1).replace(/\.0$/, "")}%`;
      const reset = w.resets_at ? ` · сброс ${new Date(w.resets_at * 1000).toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})}` : "";
      parts.push(`${bucket.name || bucket.id} / ${label}: ${left}${reset}`);
    }
    if (bucket.credit_balance != null) parts.push(`Дополнительные кредиты: ${bucket.credit_balance}`);
    if (bucket.unlimited) parts.push("Дополнительные кредиты: без ограничения по данным сервиса");
  }
  for (const [name, value] of Object.entries(q.remaining || {})) parts.push(`Остаток ${name === "tokens" ? "токенов" : "запросов"} в окне API: ${value}`);
  if (!parts.length) return kind === "claude_subscription" ? "Лимиты Claude Code: нет данных; появятся после ответа, если CLI их передаст." : "Баланс и лимиты: нет данных от сервиса.";
  const prefix = kind.endsWith("_subscription") ? "Общие лимиты подписки · " : "";
  return prefix + parts.join(" · ") + (q.checked_at ? ` · данные на ${new Date(q.checked_at * 1000).toLocaleTimeString("ru-RU")}` : "");
}
function applyModelRoutingStatus() {
  if (!S) return;
  const data = modelRoutingStatus;
  renderModelRoleTabs();
  for (const el of $$("#profiles .prof")) {
    const p = S.llm.profiles[el.dataset.id]; if (!p) continue;
    const row = data?.profiles?.find(x => x.id === el.dataset.id);
    $('[data-quota]', el).textContent = modelQuotaText(row?.quota || {}, p.kind || "");
    const summary = $('[data-routing-summary]', el);
    summary.textContent = `Приоритет ${p.priority ?? 9} · ${p.schedule_enabled ? `${p.schedule_start || "00:00"}–${p.schedule_end || "24:00"}` : "круглосуточно"}${row?.reason ? " · " + row.reason : ""}`;
  }
  if (data) {
    const pid = S.llm.auto_select ? data.next : S.llm.active;
    const next = S.llm.profiles[pid];
    const role = MODEL_ROLE_DEFS[activeModelRole]?.label || activeModelRole;
    $("#model-routing-status").textContent = `${role} · ${S.llm.auto_select ? "автовыбор" : "ручной выбор"} · ${next ? `следующий запрос: ${next.name || next.model}` : "нет доступных моделей"} · ${data.timezone || "местное время"}${data.selected?.model ? ` · последний ответ: ${data.selected.model}` : ""}`;
  }
  $("#model-routing-rows").innerHTML = roleEntries().sort((a,b) => (a[1].priority ?? 9) - (b[1].priority ?? 9)).map(([id,p]) => {
    const row = data?.profiles?.find(x => x.id === id);
    return `<tr><td><b>${escH(p.name || p.model || id)}</b><br><small>${escH(p.model || "")}</small></td><td>${p.priority ?? 9}</td><td>${escH(p.schedule_enabled ? `${p.schedule_start || "00:00"}–${p.schedule_end || "24:00"}` : "24 часа")}</td><td>${escH(row?.reason || (S.llm.auto_select && data?.next === id ? "Следующая для ответа" : "Доступна"))}</td><td>${escH(modelQuotaText(row?.quota || {},p.kind || ""))}</td><td><button class="btn small" data-profile-settings="${escH(id)}">Настроить</button></td></tr>`;
  }).join("");
  $$("[data-profile-settings]").forEach(b => b.onclick = () => {
    openProf = b.dataset.profileSettings; renderProfiles();
    [...$("#profiles").children].find(el => el.dataset.id === openProf)?.scrollIntoView({block:"start"});
  });
}
async function loadModelRoutingStatus(refresh = false, retry = false) {
  if (!S) return;
  const button = $("#model-limits-refresh");
  if (refresh && button.disabled) return;
  const request = ++modelRoutingRequest;
  if (refresh) modelRoutingLastRefresh = Date.now();
  if (refresh) { button.disabled = true; $("#model-routing-status").textContent = "Обновляю доступные данные сервисов…"; }
  try {
    const data = await core.call("models_routing_status", {refresh, retry, role: activeModelRole});
    if (request !== modelRoutingRequest) return;
    modelRoutingStatus = data; applyModelRoutingStatus();
  } catch(e) { if (refresh) toast(e.message, 7000, "error"); }
  finally { if (refresh) button.disabled = false; }
}
window.loadModelRoutingStatus = loadModelRoutingStatus;
$("#model-limits-refresh").onclick = () => loadModelRoutingStatus(true, true);
document.querySelector('nav button[data-p="brain"]').addEventListener("click", () => loadModelRoutingStatus(true));
core.on("hello", () => setTimeout(() => loadModelRoutingStatus(!!document.querySelector('#p-brain.cur')), 0));
core.on("settings", () => setTimeout(() => loadModelRoutingStatus(), 0));
setInterval(() => { if (document.querySelector('#p-brain.cur')) loadModelRoutingStatus(Date.now() - modelRoutingLastRefresh >= 60000); }, 15000);
function profileModelKey(p) { return JSON.stringify([p.kind, p.base_url, p.api_key || ""]); }
function fillProfileModels(el, p, models) {
  const picker = el.querySelector('[data-model-picker]');
  picker.innerHTML = "";
  picker.add(new Option(models.length ? "Выберите модель" : "Список моделей пуст", ""));
  if (p.model && !models.includes(p.model)) picker.add(new Option(p.model + " (текущая, нет в списке)", p.model));
  for (const name of models) picker.add(new Option(name, name));
  picker.value = p.model || "";
  picker.disabled = !models.length && !p.model;
}
async function loadProfileModels(id, el) {
  const profile = S.llm.profiles[id], key = profileModelKey(profile);
  const button = el.querySelector('[data-a="models"]'), status = el.querySelector('[data-model-status]');
  button.disabled = true; status.textContent = "Загружаю список моделей…";
  try {
    const models = [...new Set(await core.call("llm_models", {profile}))];
    // Settings may change while the server is answering.
    if (!S.llm.profiles[id] || profileModelKey(S.llm.profiles[id]) !== key) return;
    profileModelLists.set(id, {key, models});
    if (!el.isConnected) return;
    fillProfileModels(el, S.llm.profiles[id], models);
    status.textContent = models.length ? `Моделей на сервере: ${models.length}` : "Сервер вернул пустой список. Модель можно указать вручную.";
  } catch (e) {
    if (el.isConnected) status.textContent = "Не удалось получить список: " + e.message + ". Можно повторить или указать модель вручную.";
  } finally { button.disabled = false; }
}
function uid() { return "p" + Math.random().toString(36).slice(2, 8); }
function renderProfiles() {
  const box = $("#profiles"); box.innerHTML = "";
  const profs = S.llm.profiles || {};
  profSig = JSON.stringify([S.llm.active, profs, S.llm.role_max_tokens, activeModelRole]);
  renderModelRoleTabs();
  const entries = roleEntries();
  const add = $("#model-role-add"), select = $("#model-role-add-select");
  const unassigned = Object.entries(profs).filter(([, p]) => !profileInRole(p));
  if (select) {
    select.innerHTML = '<option value="">Выберите модель…</option>' + unassigned.map(([id, p]) => `<option value="${escH(id)}">${escH(p.name || p.model || id)}</option>`).join("");
    if (add) add.hidden = !unassigned.length;
  }
  if (!Object.keys(profs).length) box.innerHTML = '<p class="hint" style="margin-bottom:10px">Пока ни одной модели — выберите ниже локальный сервер, подписку или API.</p>';
  else if (!entries.length) box.innerHTML = `<div class="model-role-empty"><b>В очереди «${escH(MODEL_ROLE_DEFS[activeModelRole].label)}» пока нет моделей.</b><span>Выберите уже подключённую модель выше и нажмите «Добавить в очередь», либо подключите новую — она будет добавлена в текущую вкладку.</span></div>`;
  entries.forEach(([id, p]) => {
    const el = document.createElement("div"); el.className = "prof" + (S.llm.active === id ? " on" : "") + (openProf === id ? " open" : ""); el.dataset.id = id;
    el.innerHTML = `<div class="head"><input type="radio" name="act" ${S.llm.active === id ? "checked" : ""} title="Активная вручную; предпочтение при равном приоритете в автовыборе"><div class="t"><b></b><small></small><p class="hint" data-routing-summary></p><p class="hint" data-quota role="status"></p></div><span class="res"></span><button class="btn small">Настроить</button></div>
    <div class="body"><div class="row" style="margin-top:12px">
      <label class="f">Название<input type="text" data-f="name"></label>
      <label class="f">Приоритет (1 — наивысший)<input type="number" data-f="priority" min="1" max="9" step="1"></label>
      <fieldset class="f model-roles" data-roles><legend>Назначение модели</legend><label><input type="checkbox" data-role="communication"> Общение</label><label><input type="checkbox" data-role="coding"> Программирование</label><label><input type="checkbox" data-role="planning"> Планирование</label><span class="hint">Кодер использует только модели с отметкой «Программирование».</span></fieldset>
      <label class="f"><span><input type="checkbox" data-f="routing_enabled"> Участвует в автовыборе</span></label>
      <label class="f"><span><input type="checkbox" data-f="schedule_enabled"> Ограничить часы работы</span></label>
      <label class="f">Работает с<input type="time" data-f="schedule_start"></label>
      <label class="f">Работает до<input type="text" inputmode="numeric" data-f="schedule_end" placeholder="24:00"><span class="hint">ЧЧ:ММ; можно 24:00. Ночной интервал, например 22:00–06:00, поддерживается.</span></label>
      <label class="f">Ожидание ответа, секунд<input type="number" data-f="timeout" min="5" max="1800" step="1"></label>
      <label class="f">Протокол<select data-f="kind"><option value="openai">OpenAI-совместимый</option><option value="ollama">Ollama</option><option value="anthropic">Anthropic</option><option value="codex_subscription">Подписка Codex</option><option value="claude_subscription">Подписка Claude Code</option></select></label>
      <label class="f">Адрес сервера<input type="url" data-f="base_url"><span class="hint">Заполняется само при подключении; менять обычно не нужно.</span></label>
      <label class="f">API-ключ<input type="password" data-f="api_key" placeholder="для локальных не нужен"></label>
      <label class="f">Модель<select data-model-picker aria-label="Выбор модели на сервере" disabled><option>Нажмите «Обновить список моделей»</option></select><span class="hint" data-model-status role="status"></span></label>
      <details><summary>Указать модель вручную</summary><label class="f">Идентификатор модели<input type="text" data-f="model" placeholder="пусто — первая с сервера"></label></details>
      <label class="f">Максимум токенов в ответе<input type="number" data-f="max_tokens" placeholder="600"></label>
      <label class="f">Температура<input type="number" step="0.1" min="0" max="2" data-f="temperature" placeholder="0.7"></label>
      <label class="f" style="justify-content:end"><span><input type="checkbox" data-f="disable_thinking"> отключать «размышления» (Qwen3 и подобные в vLLM)</span></label>
    </div><div class="actions"><button class="btn primary" data-a="test">Проверить</button><button class="btn" data-a="models">Обновить список моделей</button><span style="flex:1"></span><button class="btn danger" data-a="del">Удалить</button></div></div>`;
    if (["codex_subscription", "claude_subscription"].includes(p.kind)) {
      for (const key of ["base_url", "api_key", "temperature", "max_tokens", "disable_thinking"]) {
        el.querySelector(`[data-f="${key}"]`).closest("label").style.display = "none";
      }
      el.querySelector('[data-f="kind"]').disabled = true;
      const note = document.createElement("p"); note.className = "hint";
      note.textContent = "Подписка · внешний сервис. Вход и уровень рассуждений — в блоке «Модели по подписке». «Проверить» отправляет короткий запрос и расходует лимит плана.";
      el.querySelector(".body").prepend(note);
    }
    $("b", el).textContent = p.name || "Без названия";
    $("small", el).textContent = p.cloud ? `${S.cloud && S.cloud.account ? "по аккаунту dewiar.pro" : "по ключу …" + (p.api_key || "").slice(-4)} · ${p.title || p.model || ""}` : `${p.base_url || ""}${p.model ? " · " + p.model : ""}`;
    const defaults = {priority:9, routing_enabled:true, schedule_enabled:false, schedule_start:"00:00", schedule_end:"24:00", timeout:60};
    $('[data-routing-summary]', el).textContent = `Приоритет ${p.priority ?? 9} · ${p.schedule_enabled ? `${p.schedule_start || "00:00"}–${p.schedule_end || "24:00"}` : "круглосуточно"}${p.routing_enabled === false ? " · исключена из автовыбора" : ""}`;
    $$("[data-f]", el).forEach(inp => {
      const k = inp.dataset.f, value = p[k] ?? defaults[k]; if (inp.type === "checkbox") inp.checked = !!value; else inp.value = value ?? "";
      inp.onchange = async () => {
        if (!inp.reportValidity()) return;
        let v = inp.type === "checkbox" ? inp.checked : inp.value; if (inp.type === "number") v = v === "" ? null : +v;
        inp.disabled = true;
        try {
          const profiles = {...S.llm.profiles, [id]: {...S.llm.profiles[id], [k]:v}};
          S = await core.call("settings_save", {patch:{llm:{profiles}}});
          refreshStatic(); renderProfiles(); loadModelRoutingStatus();
        } catch(e) { toast(e.message, 7000, "error"); inp.value = p[k] ?? defaults[k] ?? ""; if (inp.type === "checkbox") inp.checked = !!(p[k] ?? defaults[k]); }
        finally { inp.disabled = false; }
      };
    });
    const selectedRoles = Array.isArray(p.roles) ? p.roles : ["communication"];
    $$('[data-role]', el).forEach(inp => {
      inp.checked = selectedRoles.includes(inp.dataset.role);
      inp.onchange = async () => {
        const roles = $$('[data-role]:checked', el).map(x => x.dataset.role);
        inp.disabled = true;
        try {
          const profiles = {...S.llm.profiles, [id]: {...S.llm.profiles[id], roles}};
          S = await core.call("settings_save", {patch:{llm:{profiles}}});
          refreshStatic(); renderProfiles(); loadModelRoutingStatus();
        } catch(e) { toast(e.message, 7000, "error"); inp.checked = selectedRoles.includes(inp.dataset.role); }
        finally { inp.disabled = false; }
      };
    });
    $(".head input", el).onclick = (ev) => { ev.stopPropagation(); save({ llm: { active: id } }).then(renderProfiles); };
    $(".head .btn", el).onclick = (ev) => { ev.stopPropagation(); openProf = openProf === id ? null : id; renderProfiles(); };
    const res = $(".head .res", el);
    $('[data-a="test"]', el).onclick = async (ev) => {
      ev.target.disabled = true; res.className = "res"; res.textContent = "проверяю…";
      try {
        const r = await core.call("llm_test", { profile: S.llm.profiles[id] });
        if (r.ok) toast(`Модель «${r.model}» работает${r.api === "ollama" ? " (Ollama)" : ""}\nОтвет за ${(r.total_ms / 1000).toFixed(1)} с: «${r.reply}»`, 9000, "success");
        else toast("Не удалось проверить модель: " + (r.error || "модель не ответила"), 12000, "error");
      } catch (e) {
        toast("Не удалось проверить модель: " + e.message, 12000, "error");
      } finally {
        res.textContent = ""; res.className = "res"; ev.target.disabled = false;
      }
    };
    $('[data-a="models"]', el).onclick = () => loadProfileModels(id, el);
    $('[data-model-picker]', el).onchange = async ev => {
      const model = ev.target.value;
      if (!model) return;
      ev.target.disabled = true;
      try {
        const profiles = {...S.llm.profiles, [id]: {...S.llm.profiles[id], model}};
        S = await core.call("settings_save", {patch: {llm: {profiles}}});
        refreshStatic(); renderProfiles(); toast("Модель: " + model);
      } catch(e) {
        ev.target.value = S.llm.profiles[id].model || "";
        $('[data-model-status]', el).textContent = "Не удалось сохранить модель: " + e.message;
      } finally { ev.target.disabled = false; }
    };
    $('[data-a="del"]', el).onclick = () => { delete S.llm.profiles[id]; const patch = { llm: { profiles: S.llm.profiles } }; if (S.llm.active === id) patch.llm.active = Object.keys(S.llm.profiles)[0] || ""; save(patch).then(renderProfiles); };
    box.appendChild(el);
    const cached = profileModelLists.get(id);
    if (cached && cached.key === profileModelKey(p)) {
      fillProfileModels(el, p, cached.models);
      $('[data-model-status]', el).textContent = `Моделей на сервере: ${cached.models.length}`;
    } else if (p.model) fillProfileModels(el, p, []);
    // Only local model servers are queried automatically; subscriptions require an explicit click.
    let local = false;
    try { local = ["127.0.0.1", "localhost", "[::1]"].includes(new URL(p.base_url).hostname); } catch (_) {}
    if (openProf === id && local && !["codex_subscription", "claude_subscription"].includes(p.kind)
        && (!cached || cached.key !== profileModelKey(p))) loadProfileModels(id, el);

  });
  applyModelRoutingStatus();
}
/* GitHub #18 (24.10): событие settings (ядро само сменило модель — engine_brain) не перерисовывало «Модели»: радиокнопка
   оставалась у прежней модели. Ничего не поменялось — не трогаем (список моделей и итог «Проверить» остаются); человек
   печатает в поле профиля — перерисовка сбила бы ввод: тогда только отметка. */
function profilesSync() {
  const box = $("#profiles"); if (!box || !S.llm || JSON.stringify([S.llm.active, S.llm.profiles || {}, activeModelRole]) === profSig) return;
  const a = document.activeElement, ids = Object.keys((S.llm && S.llm.profiles) || {});
  const typing = a && box.contains(a) && a.matches("input,select,textarea") && a.type !== "radio";
  const visibleIds = roleEntries().map(([id]) => id);
  if (typing && visibleIds.join() === $$(".prof", box).map(e => e.dataset.id).join()) {
    $$(".prof", box).forEach(e => { const on = e.dataset.id === S.llm.active; e.classList.toggle("on", on); $(".head input", e).checked = on; });
  } else renderProfiles();
}
function addProfile(preset, extra = {}) {
  if (!(S_presets[preset] || extra.base_url)) { toast("Студия ещё не на связи с ядром — попробуйте через секунду"); return Promise.resolve(); }
  const id = uid(); const p = Object.assign({ api_key: "", model: "" }, S_presets[preset], extra); delete p.models; delete p.preset;
  if (!Array.isArray(p.roles)) p.roles = [activeModelRole];
  S.llm.profiles[id] = p; openProf = id;
  const patch = { llm: { profiles: S.llm.profiles } }; if (!S.llm.active) patch.llm.active = id;
  return save(patch).then(renderProfiles);
}
$("#discover").onclick = async () => {
  const r = $("#discres"); r.className = "res"; r.textContent = "ищу… (если стоит LM Studio — включаю его сервер, до минуты)";
  try {
    const found = await core.call("discover");
    if (!found.length) { r.className = "res bad"; r.textContent = "Локальные серверы не отвечают. Если программа установлена, откройте её и включите сервер."; await loadLocalModelStatus(); return; }
    const known = Object.values(S.llm.profiles).map(p => p.base_url);
    let added = 0;
    for (const f of found) if (!known.includes(f.base_url)) { await addProfile(f.preset, { base_url: f.base_url, model: f.models[0] || "" }); added++; }
    const lm = found.find(f => f.started);
    r.className = "res ok"; r.textContent = `Найдено: ${found.map(f => f.name).join(", ")}${added ? ` — добавлено ${added}` : " — уже в списке"}.`
      + (lm ? ` Сервер LM Studio был выключен — включила${lm.models.length ? "" : "; загрузите в LM Studio модель"}.` : "");
    const loc = Object.entries(S.llm.profiles).find(([, p]) => p.base_url === found[0].base_url);
    if (loc) { if (S.llm.active !== loc[0]) { await save({ llm: { active: loc[0] } }); renderProfiles(); } await wizardCheck(loc[0]); }
  } catch (e) { r.className = "res bad"; r.textContent = e.message; }
  await loadLocalModelStatus();
};

/* ---------- простые поля ---------- */
/* Текстовые поля сохраняются через 600 мс после последней буквы, остальные — почти сразу; ползунок — без «Сохранено» */
function bindFields() {
  $$("[data-k]").forEach(inp => {
    const k = inp.dataset.k;
    inp.onchange = inp.oninput = () => {
      let v = inp.type === "checkbox" ? inp.checked : inp.value;
      if (inp.type === "number" || inp.type === "range") v = +v;
      if (inp.type === "range") $("#speedv").textContent = `×${v}`;
      clearTimeout(saveT[k]); saveT[k] = setTimeout(() => save(patchOf(k, v), inp.type === "range"), inp.type === "text" || inp.type === "url" || inp.type === "password" ? 600 : 50);
    };
  });
  $$("[data-eng]").forEach(o => $("input", o).onchange = () => {
    if (o.dataset.eng === "dewiar") return dwStt(true);             // «Сервер Dewiar» — облако или аккаунт (links.js)
    if (dwSttOn()) return dwStt(false, o.dataset.eng);              // уходим с сервера Dewiar — сразу на выбранное
    save({ stt: { engine: o.dataset.eng } });
  });
  $$("[data-tts]").forEach(o => $("input", o).onchange = () => {
    if (o.dataset.tts === "dewiar") return dwVoice(true);           // «Голос Dewiar» — облако или аккаунт (links.js)
    if (dwVoiceOn()) return dwVoice(false, o.dataset.tts);           // уходим с голоса Dewiar — сразу на выбранный
    save({ tts: { engine: o.dataset.tts } });
  });
  // extra — тоже заменяется целиком (settings._merge), так что лишние ключи из поля убираются
  $("#ttsextra").onchange = () => { try { save({ tts: { remote: { extra: JSON.parse($("#ttsextra").value || "{}") } } }); } catch (e) { toast("Это не JSON"); } };
}
function refreshStatic() {
  const vendorDisabled = !S?.privacy?.dewiar_services;
  $("#p-dw").classList.toggle("services-disabled", vendorDisabled);
  $("#dw-disabled").hidden = !vendorDisabled;
  if (vendorDisabled && $("#ann-dlg").open) $("#ann-dlg").close();
  window.subscriptionNetworkSync?.();
  if (window.studioSkin) studioSkin(get("ui.skin"));     // студия — в стиле виджета (studio/theme.js)
  featuresApply();
  if (window.DewiarZoom) { DewiarZoom.apply(get("ui")); if (window.zoomSync) zoomSync(); }   // масштаб и текст (look.js)
  const wf = $("#wf-box"); if (wf) wf.style.display = (+get("ui.widget_fail2") || 0) >= 2 ? "" : "none";
  $$("[data-k]").forEach(inp => {
    if (document.activeElement === inp) return;           // не перетираем поле, которое сейчас правят
    const v = get(inp.dataset.k); if (inp.type === "checkbox") inp.checked = !!v; else if (v !== undefined && v !== null) inp.value = v;
  });
  $("#speedv").textContent = `×${S.tts.speed}`;
  const pl = $("#pause-learn"), px = +get("audio.end_silence_extra") || 0;          // подстройка паузы: сколько уже добавлено
  if (pl) {
    const pxx = Math.min(600, px);                        // выше +600 ядро не ждёт (№226), даже если выучено раньше
    pl.textContent = "Если вы продолжаете говорить сразу после того, как ассистент решил, что фраза закончена, — он начнёт ждать дольше; со временем вернётся к обычной паузе."
      + (pxx > 0 && get("audio.end_silence_auto") !== false ? ` Сейчас жду ${(+get("audio.end_silence_ms") || 650) + pxx} мс (+${pxx} под вашу речь). ` : "");
    if (pxx > 0 && get("audio.end_silence_auto") !== false) {   // №226: «добавка сама не уменьшается» — вернуть сразу
      const r = document.createElement("a"); r.href = "#"; r.id = "pause-reset"; r.textContent = "Вернуть обычную";
      r.onclick = (e) => { e.preventDefault(); e.stopPropagation(); save({ audio: { end_silence_extra: 0 } }); };
      pl.appendChild(r);
    }
  }
  // «Сервер Dewiar» — отдельный вариант: адрес и ключ подставлены из аккаунта, поля «своего сервера» человеку не показываем
  // (отзыв 17.10: «зачем пользователю эти данные? Он должен их вносить руками?»)
  const dws = typeof dwSttOn === "function" && dwSttOn(), eng = dws ? "dewiar" : S.stt.engine;
  $$("[data-eng]").forEach(o => { const on = eng === o.dataset.eng; o.classList.toggle("on", on); $("input", o).checked = on; });
  $$('#p-ears .sub').forEach(s => s.style.display = eng === s.dataset.for ? "" : "none");
  if (typeof renderSrvSttOpt === "function") renderSrvSttOpt();
  const dw = typeof dwVoiceOn === "function" && dwVoiceOn();
  $$("[data-tts]").forEach(o => { const on = dw ? o.dataset.tts === "dewiar" : S.tts.engine === o.dataset.tts; o.classList.toggle("on", on); $("input", o).checked = on; });
  $$('#p-voice .sub').forEach(s => s.style.display = !dw && S.tts.engine === s.dataset.for ? "" : "none");
  if (document.activeElement !== $("#ttsextra")) $("#ttsextra").value = JSON.stringify(S.tts.remote.extra || {});
  renderFx();
}
/* «Послушать»: кнопка сразу показывает, что происходит — готовлю → звучит → снова можно нажать.
   Ход берём из событий state ядра (speaking → idle); не пришли за 45 с — кнопку всё равно отпускаем. */
let previewing = null;
function previewVoice(b, args) {
  if (previewing) previewDone();
  previewing = b; b.classList.add("pv-wait"); b.classList.remove("pv-play");
  let tag = b.querySelector(".pv"); if (!tag) { tag = document.createElement("i"); tag.className = "pv"; b.appendChild(tag); }
  tag.textContent = " · готовлю…";
  clearTimeout(b._pvT); b._pvT = setTimeout(() => { if (previewing === b) previewDone(); }, 45000);
  core.call("voice_test", args).catch(e => { previewDone(); toast(e.message); });
}
function previewDone() {
  const b = previewing; if (!b) return; previewing = null;
  b.classList.remove("pv-wait", "pv-play"); const tag = b.querySelector(".pv"); if (tag) tag.remove();
}
core.on("state", e => {
  if (!previewing) return;
  if (e.state === "speaking") { previewing.classList.remove("pv-wait"); previewing.classList.add("pv-play"); const tag = previewing.querySelector(".pv"); if (tag) tag.textContent = " · звучит"; }
  else if (e.state === "idle" && previewing.classList.contains("pv-play")) previewDone();
});
let vkSpeakers = [];
async function loadVosk() {
  try {
    const r = await core.call("vosk_voices"); vkSpeakers = r.speakers || [];
    const box = $("#vk-list");
    // модель голосов Vosk скачивается один раз; пока качается — спрашиваем снова каждые 5 с
    if (!r.ready) { box.textContent = r.error ? `Не загрузились: ${r.error}. Пока говорит Piper.` : "Голоса скачиваются (один раз) — прогресс виден под виджетом. Пока говорит Piper."; if (r.loading) setTimeout(loadVosk, 5000); return; }
    box.innerHTML = `<p class="hint" style="margin-bottom:8px">Голоса модели — нажмите, чтобы послушать. Персонажам они раздаются по полу автоматически, поменять — в карточке персонажа.</p><div class="chips">` +
      vkSpeakers.map(v => `<button class="chip" data-vk="${v.id}">▶ Голос ${v.id + 1} · ${v.gender === "male" ? "мужской" : "женский"}</button>`).join("") + `</div>`;
    $$("#vk-list [data-vk]").forEach(b => b.onclick = () => previewVoice(b, { text: "Здравствуйте! Я ваш голосовой помощник. Чем могу помочь?", voice: { vosk: +b.dataset.vk, prefer: "" } }));
  } catch (e) { $("#vk-list").textContent = e.message; }
}
function renderFx() {
  const cur = S.tts.fx || "persona";
  const items = [{ id: "persona", name: "Как у персонажа", description: "у каждого своя обработка" }].concat(S_fx);
  // ▶ у каждого варианта — видно, что его можно послушать (отзыв 11.10: «как проверить звучание, непонятно»)
  $("#fxgrid").innerHTML = items.map(f => `<button data-fx="${f.id}" class="${f.id === cur ? "on" : ""}" title="Выбрать и послушать"><b><i class="fxp"></i>${f.name}</b><span>${f.description}</span></button>`).join("");
  $$("#fxgrid button").forEach(b => b.onclick = () => {
    save({ tts: { fx: b.dataset.fx } });
    previewVoice(b, { text: "Слушаю вас. Чем могу помочь?", fx: b.dataset.fx === "persona" ? null : b.dataset.fx });
  });
}
async function loadDevices() {
  try {
    const ds = await core.call("devices");
    const fill = (sel, kind) => { sel.innerHTML = '<option value="">Системный по умолчанию</option>' + ds.filter(d => d.kind === kind).map(d => `<option value="${d.name}"></option>`).join("");
      $$("option", sel).forEach((o, i) => { if (i) o.textContent = ds.filter(d => d.kind === kind)[i - 1].description; }); sel.value = get(sel.dataset.k) || ""; };
    fill($("#devin"), "source"); fill($("#devout"), "sink");
  } catch (e) { toast(e.message); }
}
/* «Скачано на этом компьютере» (отзыв 19.10: непонятно, что скачано, а что будет качаться): models_status — строки по
   моделям, models_get — скачать заранее; ход — по status.downloads («m:<ключ>»), итог — событием models. */
let mdlData = null;
const mdlEsc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function mdlRender(d) {
  mdlData = d || mdlData; if (!mdlData) return;
  const dl = (lastStatus && lastStatus.downloads) || {};
  for (const g of ["ears", "voice"]) {
    const box = $("#mdl-" + g); if (!box) continue;
    box.innerHTML = mdlData.items.filter(x => x.group === g).map(x => {
      const f = dl["m:" + x.key], pct = f != null && f < 1 ? Math.round(f * 100) : x.pct;
      const st = pct != null ? `<span class="bar"><i style="width:${pct}%"></i></span><span>скачивается${pct ? " " + pct + "%" : "…"}</span>`
        : x.installed ? `<span class="ok">✓ скачана</span><span>${x.mb} МБ</span>${x.used ? `<span class="use">· используется</span>` : ""}`
        : `<span>не скачана · ~${x.mb} МБ</span><button class="btn small" data-mget="${mdlEsc(x.key)}">Скачать</button>`;
      return `<div class="mdl-row"><b>${mdlEsc(x.title)}</b><small>${mdlEsc(x.about)}</small><div class="st">${st}</div></div>`;
    }).join("");
  }
  $$("[data-mget]").forEach(b => b.onclick = () => { b.disabled = true; b.textContent = "Начинаю…";
    core.call("models_get", { key: b.dataset.mget }).then(() => setTimeout(mdlLoad, 400)).catch(e => { toast(e.message); b.disabled = false; b.textContent = "Скачать"; }); });
}
function mdlLoad() { core.call("models_status").then(mdlRender).catch(() => {}); }
core.on("models", d => mdlRender(d));
["ears", "voice"].forEach(p => { const b = $(`nav button[data-p="${p}"]`); if (b) b.addEventListener("click", mdlLoad); });
let lastStatus = {};
function renderStatus(st) {
  lastStatus = st || lastStatus; st = lastStatus;
  if (typeof studioTight === "function" && st) studioTight(!!st.tight);   // мало памяти — облики студии почти неподвижны
  const rows = [["Связь с ядром", core.connected ? "есть" : "нет"], ["Эхоподавление", st.headphones ? "не нужно — наушники" : st.aec ? "работает" : "выключено — полудуплекс"], ["Микрофон", st.input || "—"],
    ["Распознавание", `${st.stt || "—"}${st.stt_ready === false ? " (загружается, пока Vosk)" : ""}`], ["Голос", st.tts || "—"], ["Модель", st.llm || "не выбрана"], ["Сейчас", [st.note, ...Object.values(st.loading || {}).map(x => "готовлю: " + x.t.toLowerCase()
      + (x.f != null && x.f < 1 ? ` ${Math.round(x.f * 100)}%` : "…"))].filter(Boolean).join(" · ") || "всё спокойно"]];
  $("#status").innerHTML = rows.map(([a, b]) => `<dt>${a}</dt><dd></dd>`).join("");
  $$("#status dd").forEach((d, i) => d.textContent = rows[i][1]);
  $("#aecst").textContent = st.headphones ? "Не нужно: включён режим наушников." : st.aec ? "Работает: ассистент не слышит сам себя, и его можно перебить голосом." : "Сейчас не работает — пока ассистент говорит, микрофон приглушается. В наушниках включите режим ниже.";
  $("#sttstat").textContent = st.stt ? `Сейчас: ${st.stt}${st.stt_ready === false ? " — загружается, пока работает Vosk" : ""}` : "";
  if (mdlData && Object.keys(st.downloads || {}).some(k => k.startsWith("m:"))) mdlRender();   // ход скачивания — сразу в строке
}

function renderTools(list) {
  const box = $("#toollist"); box.innerHTML = "";
  list.forEach(t => {
    const el = document.createElement("div"); el.className = "tool";
    el.innerHTML = `<div><b></b>${t.risk === "confirm" ? '<span class="risk">опасное</span>' : ""}<div class="d"></div></div>
      <label class="tg" title="Включено"><input type="checkbox" ${t.enabled ? "checked" : ""}><i></i></label>
      <label class="ask"><input type="checkbox" ${t.confirm ? "checked" : ""}> спрашивать разрешение</label>`;
    $("b", el).textContent = t.title; $(".d", el).textContent = t.description;
    const [on, ask] = $$("input", el);
    on.onchange = () => save({ tools: { enabled: { [t.name]: on.checked } } }).then(() => core.call("tools").then(renderTools));
    ask.onchange = () => save({ tools: { confirm: { [t.name]: ask.checked } } });
    box.appendChild(el);
  });
}
/* ---------- напоминания ---------- */
const RI = { pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
  play: '<path d="M7 5v14l11-7z"/>', edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="m13.5 6.5 4 4"/>',
  del: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>', ok: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>' };
let remItems = [];
function remGroup(x) {
  if (x.repeat) return "Повторяющиеся";
  const d = new Date(x.at), t = new Date(); const day = (a) => new Date(a.getFullYear(), a.getMonth(), a.getDate()).getTime();
  const diff = Math.round((day(d) - day(t)) / 864e5);
  return diff <= 0 ? "Сегодня" : diff === 1 ? "Завтра" : "Позже";
}
function renderReminders(items) {
  remItems = items || []; renderPresets(); const box = $("#r-list"); box.innerHTML = "";
  if (!remItems.length) { box.innerHTML = '<div class="rempty">Напоминаний пока нет.<br>Скажите «Агент, напомни через 20 минут выключить духовку» — или напишите выше.</div>'; return; }
  const groups = {}; remItems.forEach(x => (groups[remGroup(x)] = groups[remGroup(x)] || []).push(x));
  ["Сегодня", "Завтра", "Позже", "Повторяющиеся"].forEach(g => {
    if (!groups[g]) return;
    const h = document.createElement("div"); h.className = "rgroup"; h.textContent = g; box.appendChild(h);
    groups[g].forEach(x => {
      const el = document.createElement("div"); el.className = "ritem" + (x.paused ? " paused" : "");
      const kind = x.kind === "job" ? (x.brief ? "утренняя сводка" : "дело по расписанию") + ({ telegram: " · в Telegram", max: " · в MAX" }[x.deliver] || " · голосом")
                   : { alarm: "будильник", timer: "таймер" }[x.kind];
      el.innerHTML = `<div class="rtime"></div><div class="rbody"><div class="rtext"></div><div class="rmeta"></div></div><div class="ract"></div>`;
      $(".rtime", el).textContent = x.at_hm; $(".rtext", el).textContent = x.text;
      const meta = $(".rmeta", el); const w = document.createElement("span"); w.textContent = x.paused ? "на паузе" : x.when; meta.appendChild(w);
      if (x.repeat_text) { const b = document.createElement("span"); b.className = "rbadge"; b.textContent = "↻ " + x.repeat_text; meta.appendChild(b); }
      if (kind) { const b = document.createElement("span"); b.className = "rbadge k"; b.textContent = kind; meta.appendChild(b); }
      if (x.att && x.note) {                          // картинки и файлы напоминания — в его заметке; нажатие открывает её
        const b = document.createElement("button"); b.className = "rbadge ratt"; b.title = "Открыть вложения";
        b.innerHTML = `<svg viewBox="0 0 24 24"><path d="m20.5 11.5-8.6 8.6a5.5 5.5 0 0 1-7.8-7.8l8.6-8.6a3.7 3.7 0 0 1 5.2 5.2l-8.6 8.6a1.8 1.8 0 0 1-2.6-2.6l8-8"/></svg>${x.att}`;
        b.onclick = () => ntGoto(x.note); meta.appendChild(b); }
      const act = $(".ract", el);
      const btn = (icon, title, fn) => { const b = document.createElement("button"); b.title = title; b.innerHTML = `<svg viewBox="0 0 24 24">${RI[icon]}</svg>`; b.onclick = fn; act.appendChild(b); };
      if (x.repeat) btn(x.paused ? "play" : "pause", x.paused ? "Возобновить" : "Пауза", () => core.call("reminder_update", { id: x.id, patch: { paused: !x.paused } }).then(loadReminders).catch(e => toast(e.message)));
      if (x.kind !== "job" && x.kind !== "alarm" && x.kind !== "timer")
        btn("ok", "Сделано", () => core.call("done_reminder", { id: x.id }).then(() => loadReminders()).catch(e => toast(e.message)));
      btn("edit", "Изменить", () => editReminder(el, x));
      btn("del", "Удалить", () => core.call("reminder_cancel", { id: x.id }).then(() => { toast("Удалено"); loadReminders(); }).catch(e => toast(e.message)));
      box.appendChild(el);
    });
  });
}
function editReminder(el, x) {
  const body = $(".rbody", el); body.innerHTML = "";
  const t = document.createElement("input"); t.value = x.text; t.placeholder = "о чём напомнить";
  const w = document.createElement("input"); w.placeholder = "когда: «завтра в 10», «каждый день в 8» — пусто, чтобы не менять"; w.style.marginTop = "6px";
  body.appendChild(t); body.appendChild(w); t.focus();
  const save = () => { const patch = { text: t.value.trim() || x.text }; if (w.value.trim()) patch.when = w.value.trim();
    core.call("reminder_update", { id: x.id, patch }).then(() => { toast("Сохранено"); loadReminders(); }).catch(e => toast(e.message)); };
  [t, w].forEach(i => i.onkeydown = (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") loadReminders(); });
  const act = $(".ract", el); act.innerHTML = ""; const ok = document.createElement("button"); ok.className = "btn primary"; ok.style.width = "auto"; ok.style.padding = "0 12px"; ok.textContent = "Сохранить"; ok.onclick = save; act.appendChild(ok);
}
/* ---------- готовые дела по расписанию ---------- */
const J_DAYS = { daily: "каждый день", weekdays: "по будням", fri: "по пятницам", sun: "по воскресеньям" };
const J_PRESETS = [
  { id: "brief", title: "Утренняя сводка", desc: "Дела и встречи на сегодня, погода, вчерашние траты — коротко, за минуту.", days: "daily", time: "08:00",
    task: "Утренняя сводка", brief: true, icon: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>' },
  { id: "fx", title: "Курс валют", desc: "Доллар и евро на сегодня по нацбанку вашей страны и насколько изменились со вчера.", days: "weekdays", time: "09:00",
    task: "Курс доллара и евро на сегодня (официальный, по нацбанку моей страны) и изменение со вчера", icon: '<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>' },
  { id: "evening", title: "Итог дня", desc: "Что сделано, сколько потрачено, что осталось и что запланировано на завтра.", days: "daily", time: "21:00",
    task: "Итог дня: что сделано из дел на сегодня, что осталось, сколько потрачено за день и что запланировано на завтра — коротко", icon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>' },
  { id: "week", title: "Итоги недели", desc: "По пятницам: что сделано за неделю, траты по категориям, планы на следующую.", days: "fri", time: "18:00",
    task: "Итоги недели: что я сделал за неделю, траты за неделю по категориям и что запланировано на следующую неделю", icon: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M8 15l2 2 4-4"/>' },
  { id: "news", title: "Главные новости", desc: "Пять главных новостей утра — по одному предложению, без воды.", days: "daily", time: "08:30",
    task: "Главные новости утра: 5 самых важных, по одному предложению каждая, с упоминанием источника", icon: '<path d="M4 5h13v14H6a2 2 0 0 1-2-2V5z"/><path d="M17 9h3v8a2 2 0 0 1-2 2M8 9h5M8 13h5M8 17h3"/>' },
  { id: "weather", title: "Погода и что надеть", desc: "Погода на день: утром, днём, вечером — и нужен ли зонт.", days: "daily", time: "07:30",
    task: "Погода на сегодня в моём городе: утром, днём и вечером, будут ли осадки, нужен ли зонт и что надеть — коротко", icon: '<path d="M7 18a4.5 4.5 0 0 1-.6-9A6 6 0 0 1 18 10a4 4 0 0 1-.5 8H7z"/>' },
];
/* Включённое дело для готовой карточки: по preset, а у созданных без него — по тексту поручения */
function jobOf(p) { return remItems.find(x => x.kind === "job" && (x.preset === p.id || (!x.preset && x.text === p.task))); }
function renderPresets() {
  const box = $("#j-pre"); if (!box) return; box.innerHTML = "";
  J_PRESETS.forEach(p => {
    const on = jobOf(p);
    const el = document.createElement("div"); el.className = "jcard" + (on ? " on" : ""); el.dataset.id = p.id;
    el.innerHTML = `<svg viewBox="0 0 24 24">${p.icon}</svg><b></b><span></span>
      <div class="jrow"><input type="text" inputmode="numeric" maxlength="5" placeholder="чч:мм" title="Время, например 8:30"><select><option value="voice">голосом</option><option value="telegram">в Telegram</option><option value="max">в MAX</option></select></div>
      <div class="jact"><button class="btn"></button><button class="jtry" title="Выполнить один раз прямо сейчас">попробовать</button></div>`;
    $("b", el).textContent = p.title; $(":scope > span", el).textContent = p.desc;
    const tm = $("input", el), dv = $("select", el), go = $(".jact .btn", el);
    tm.value = on ? on.at_hm : p.time; dv.value = on ? (on.deliver || "voice") : "voice";
    if (on) { const st = document.createElement("div"); st.className = "jstate"; st.textContent = `✓ включено · ${on.repeat_text || J_DAYS[p.days]}`; el.insertBefore(st, $(".jrow", el)); }
    go.textContent = on ? "Выключить" : "Включить"; if (!on) go.classList.add("primary");
    const save = async () => {
      try {
        const m = (tm.value.trim() || p.time).replace(/[.\-\s]/, ":").match(/^(\d{1,2})(?::(\d{2}))?$/);
        if (!m || +m[1] > 23 || +(m[2] || 0) > 59) { tm.focus(); return toast("Время — например 8:30 или 21:00"); }
        const hm = `${m[1].padStart(2, "0")}:${m[2] || "00"}`;
        // расписание ядро разбирает из фразы, как сказанное голосом: «каждый день в 08:00 делай …»
        const r = await core.call("job_add", { phrase: `${J_DAYS[p.days]} в ${hm} делай ${p.title.toLowerCase()}`, task: p.task,
          deliver: dv.value, brief: !!p.brief, preset: p.id });
        // время или способ изменили — старое дело заменяем новым; снимаем его только после удачного job_add:
        // иначе отказ ядра (например, «Telegram ещё не подключён») оставит без дела вовсе
        if (on) await core.call("reminder_cancel", { id: on.id });
        toast(`«${p.title}» — ${r.repeat_text}`); loadReminders();
      } catch (e) { toast(e.message); }
    };
    go.onclick = async () => {
      if (on) { try { await core.call("reminder_cancel", { id: on.id }); toast(`«${p.title}» выключено`); loadReminders(); } catch (e) { toast(e.message); } return; }
      save();
    };
    [tm, dv].forEach(i => i.onchange = () => { if (on) save(); });
    tm.onkeydown = (e) => { if (e.key === "Enter") on ? save() : go.click(); };
    $(".jtry", el).onclick = () => core.call("job_run", { task: p.task, brief: !!p.brief, deliver: dv.value })
      .then(r => toast({ telegram: "Выполняю — пришлю в Telegram", max: "Выполняю — пришлю в MAX" }[r.deliver] || "Выполняю — скажу голосом")).catch(e => toast(e.message));
    box.appendChild(el);
  });
}
async function loadReminders() {
  try { renderDone(await core.call("done")); } catch (e) { $("#d-list").textContent = e.message; }
  try { renderReminders(await core.call("reminders")); } catch (e) { $("#r-list").textContent = e.message; }
}
/* ---------- дела: сделано сегодня, серия, значки и неделя (core/done.py; то же на телефоне — «Мой день» → «Дела») ---------- */
const dEsc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const dela = (n) => n % 10 === 1 && n % 100 !== 11 ? "дело" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "дела" : "дел";
const dney = (n) => n % 10 === 1 && n % 100 !== 11 ? "день" : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "дня" : "дней";
function renderDone(d) {
  if (!d) return;
  const head = $("#d-head"), max = Math.max(1, ...d.week.map(w => w.n)), WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const chips = [];
  for (const g of ["day", "streak", "total"]) {
    const all = d.achievements.filter(a => a.group === g), next = all.find(a => !a.got);
    all.filter(a => a.got).forEach(a => chips.push(`<span class="dach on" title="${dEsc(a.what)} · получено">${a.icon} ${dEsc(a.name)}</span>`));
    if (next) chips.push(`<span class="dach" title="${dEsc(next.what)} · сейчас ${next.val} из ${next.n}">${next.icon} ${Math.min(next.val, next.n)}/${next.n}</span>`);
  }
  head.innerHTML = `<div class="dnum"><b>${d.today}</b><span>${d.today ? dela(d.today) + " сегодня" : "дел сегодня"}</span></div>`
    + `<div class="dmid"><div class="dstreak">${d.streak >= 2 ? `🔥 ${d.streak} ${dney(d.streak)} подряд` : d.today ? "Первый день серии" : d.total ? "Отметьте дело — серия продолжится" : "Сделали дело — отметьте его"}`
    + `<small>всего ${d.total} · лучший день ${d.best_day} · лучшая серия ${d.best_streak}</small></div><div class="dachs">${chips.join("")}</div></div>`
    + `<div class="dweek" aria-label="Дела за неделю">${d.week.map((w, i) => `<div title="${w.day}: ${w.n}"><i style="height:${Math.round(6 + 52 * w.n / max)}px"${i === 6 ? ' class="now"' : ""}></i><small>${WD[new Date(w.day + "T12:00").getDay()]}</small></div>`).join("")}</div>`;
  const box = $("#d-list"); box.innerHTML = "";
  if (d.items.length) { const h = document.createElement("div"); h.className = "rgroup"; h.textContent = "Сделано сегодня"; box.appendChild(h); }
  d.items.slice().reverse().forEach(x => {
    const el = document.createElement("div"); el.className = "ritem dn";
    const t = new Date(x.at);
    el.innerHTML = `<div class="dok"><svg viewBox="0 0 24 24">${RI.ok}</svg></div><div class="rbody"><div class="rtext"></div><div class="rmeta"><span></span></div></div><div class="ract"></div>`;
    $(".rtext", el).textContent = x.title;
    $(".rmeta span", el).textContent = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}` + (x.src && x.src !== "pc" ? " · на телефоне" : "");
    const b = document.createElement("button"); b.title = "Снять отметку"; b.innerHTML = `<svg viewBox="0 0 24 24">${RI.undo}</svg>`;
    b.onclick = () => core.call("done_undo", { id: x.id }).then(() => { toast("Отметка снята"); loadReminders(); }).catch(e => toast(e.message));
    $(".ract", el).appendChild(b); box.appendChild(el);
  });
}
function doneCelebrate(r) {
  if (!r || !r.added) return;
  const a = (r.fresh || [])[r.fresh.length - 1];
  toast(a ? `${a.icon} Новое достижение: ${a.name} — ${a.what}` : r.mile ? `🎉 ${r.mile} ${dela(r.mile)} за сегодня — так держать` : `✓ ${r.added.join(", ")} — сегодня ${r.today}`);
  if ((a || r.mile) && !matchMedia("(prefers-reduced-motion: reduce)").matches) { const h = $("#d-head"); h.classList.remove("cheer"); void h.offsetWidth; h.classList.add("cheer"); }
}
async function doneAdd() {
  const v = $("#d-phrase").value.trim(); if (!v) { $("#d-phrase").focus(); return; }
  try { await core.call("done_add", { title: v }); $("#d-phrase").value = ""; loadReminders(); } catch (e) { toast(e.message); }
}
$("#d-add").onclick = doneAdd;
$("#d-phrase").onkeydown = (e) => { if (e.key === "Enter") doneAdd(); };
// отметка любым путём — кнопкой, голосом («я позвонил в банк»), моделью — празднуем здесь один раз (отзыв 17.10: показать в видео)
core.on("done", e => { if (!$("#p-reminders").classList.contains("cur")) return; renderDone(e); if ((e.added || []).length) doneCelebrate(e); });
let rpT = null;
// пока пишут — показываем, как ядро поняло фразу (тот же разбор, что у голосовой команды)
$("#r-phrase").oninput = () => { clearTimeout(rpT); const v = $("#r-phrase").value.trim(); const el = $("#r-parse");
  if (!v) { el.textContent = ""; el.className = "rparse"; return; }
  rpT = setTimeout(() => core.call("reminder_parse", { phrase: v }).then(r => {
    if (!r) { el.className = "rparse bad"; el.textContent = "Не понял, когда напомнить — добавьте время: «в 18:00», «через час», «каждый понедельник в 10»"; return; }
    el.className = "rparse"; el.innerHTML = ""; const b = document.createElement("b"); b.textContent = r.when + (r.repeat_text ? ", " + r.repeat_text : "");
    el.append("⏰ ", b, " — " + r.text); }).catch(() => {}), 200); };
$("#r-phrase").onkeydown = (e) => { if (e.key === "Enter") $("#r-add").click(); };
$("#r-add").onclick = async () => { const v = $("#r-phrase").value.trim(); if (!v) return $("#r-phrase").focus();
  try { const r = await core.call("reminder_add", { phrase: v }); $("#r-phrase").value = ""; $("#r-parse").textContent = ""; toast(`Напомню ${r.when}`); loadReminders(); }
  catch (e) { $("#r-parse").className = "rparse bad"; $("#r-parse").textContent = e.message; } };
$("#r-listen").onclick = () => core.call("alarm_test", { sound: $("#r-sound").value }).catch(e => toast(e.message));
$("#r-try").onclick = () => core.call("reminder_add", { phrase: "через 10 секунд проверка будильника" }).then(() => { toast("Через 10 секунд прозвенит"); loadReminders(); }).catch(e => toast(e.message));
core.on("reminders", e => { if ($("#p-reminders").classList.contains("cur")) renderReminders(e.items); });

/* «Возможности» (24.10): выключенное прячется из меню — «Суфлёр» в списке разделов, «Рядом» и «Встречи» во вкладках
   «Близких». Открыта спрятанная вкладка — возвращаемся к переписке; открыт спрятанный раздел — к «Возможностям». */
function featuresApply() {
  const f = get("features") || {}, on = (k) => f[k] !== false;
  const nb = $('nav button[data-p="prompter"]'); if (nb) nb.hidden = !on("prompter");
  for (const t of ["near", "meet"]) { const b = $(`#cl-tabs button[data-t="${t}"]`); if (b) b.hidden = !on(t); }
  const cur = $("#cl-tabs button.on");
  if (cur && cur.hidden && window.rdTab) rdTab("chat");
  if (nb && nb.hidden && nb.classList.contains("cur")) { const fb = $('nav button[data-p="features"]'); if (fb) fb.click(); }
  if (window.hmNav) hmNav();                                     // меню — ещё и по «Главной» (home.js): скрытое там скрыто и тут
}

/* Strata: explicit API connection or an owned CLI process; discovery never loads weights. */
let strataApiProfile=null, strataPoll=null, strataBusy=false;
async function loadStrataStatus() {
  clearTimeout(strataPoll);
  try {
    const data=await core.call('strata_status'), select=$('#strata-package'), previous=select.value;
    select.innerHTML=(data.catalog || []).map(item=>{const ram=item.ram_required_gib ?? item.required_gib, disk=item.disk_gib;return `<option value="${escH(item.id)}"${item.ready ? '' : ' disabled'}>${escH(item.name)} · ОЗУ ~${ram} ГБ${disk!=null ? ` · диск ${disk} ГБ` : ''}${item.ready ? '' : ' · неполный пакет'}</option>`;}).join('');
    if ([...select.options].some(x=>x.value===previous)) select.value=previous;
    const desktop = data.desktop_running ? ` Strata Desktop найдена (${data.desktop_api}).` : ' Strata Desktop не обнаружена.';
    if (data.desktop_api && ['auto','http://127.0.0.1:8080/v1'].includes($('#strata-url').value.trim())) $('#strata-url').value=data.desktop_api;
    const ramFree=data.ram_available_gib ?? data.available_gib;
    $('#strata-status').textContent=(data.runtime ? 'CLI Strata найден. ' : 'CLI Strata не найден. ')+`ОЗУ свободно ${ramFree} ГБ.`+desktop+' '+({stopped:'Движок Dewiar остановлен.',loading:'Загружаю модель…',running:`Работает: ${data.model}.`,stopping:'Останавливаю движок…'}[data.state] || '')+(data.error ? ' '+data.error : '');
    $('#strata-start').disabled=strataBusy || !data.runtime || data.owned || ![...select.options].some(x=>!x.disabled);
    $('#strata-stop').disabled=strataBusy || !data.owned;
    $('#strata-use-owned').disabled=strataBusy || data.state !== 'running';
    if (data.state==='loading' && document.querySelector('#p-brain.cur'))strataPoll=setTimeout(loadStrataStatus,2000);
  } catch(error) { $('#strata-status').textContent=error.message; }
}

let strataMemoryRequired=0;
function strataMemoryText(data) {
  const required=Number(data?.required_gib || strataMemoryRequired || 0), free=Number(data?.available_gib ?? data?.free_gib ?? 0);
  const deficit=Number(data?.deficit_gib ?? data?.shortfall_gib ?? Math.max(0,required-free));
  return `ОЗУ для запуска нужно около ${required.toFixed(1)} ГБ. Сейчас свободно ОЗУ ${free.toFixed(1)} ГБ; не хватает ${deficit.toFixed(1)} ГБ. Размер файла модели на диске в этот показатель не входит.`;
}
function renderStrataMemory(data) {
  const dlg=$('#strata-memory-dlg'), list=$('#strata-memory-list'); if (!dlg || !list) return;
  $('#strata-memory-summary').textContent=strataMemoryText(data);
  const items=data?.processes || data?.items || [];
  list.innerHTML='';
  if (!items.length) { list.innerHTML='<div class="strata-memory-empty">Подходящих приложений не найдено. Закройте ненужные программы обычным способом или выберите меньшую модель.</div>'; return; }
  for (const item of items) {
    const row=document.createElement('label'); row.className='strata-memory-item';
    const check=document.createElement('input'); check.type='checkbox'; check.dataset.pid=item.pid; check.setAttribute('aria-label',`Выбрать ${item.name}`);
    const text=document.createElement('span'); const name=document.createElement('b'); name.textContent=item.name || 'Приложение';
    const cmd=document.createElement('small'); cmd.textContent=item.command || `PID ${item.pid}`; text.append(name,cmd);
    const rss=document.createElement('strong'); rss.textContent=`ОЗУ ${Number(item.rss_gib || 0).toFixed(2)} ГБ`;
    row.append(check,text,rss); list.append(row);
  }
}
async function refreshStrataMemory() {
  const r=await core.call('memory_processes',{required_gib:strataMemoryRequired,limit:24}); renderStrataMemory(r);
  $('#strata-memory-result').textContent=`ОЗУ свободно сейчас: ${Number(r.ram_available_gib ?? r.free_gib ?? 0).toFixed(1)} ГБ. Освобождение выполняется только для выбранных приложений.`;
}
async function openStrataMemory(memory) {
  strataMemoryRequired=Number(memory?.required_gib || 0);
  renderStrataMemory(memory);
  $('#strata-memory-result').textContent='Приложения не закрываются автоматически. Отметьте только то, что можно завершить, затем подтвердите действие.';
  const dlg=$('#strata-memory-dlg'); if (dlg && !dlg.open) dlg.showModal();
  try { await refreshStrataMemory(); } catch (e) { $('#strata-memory-result').textContent='Не удалось обновить список: '+e.message; }
}
$('#strata-memory-cancel').onclick=()=>$('#strata-memory-dlg').close();
$('#strata-memory-refresh').onclick=async()=>{const b=$('#strata-memory-refresh');b.disabled=true;try{await refreshStrataMemory();}catch(e){$('#strata-memory-result').textContent=e.message;}finally{b.disabled=false;}};
$('#strata-memory-close').onclick=async()=>{
  const pids=$$('#strata-memory-list input[type="checkbox"]:checked').map(x=>+x.dataset.pid); if(!pids.length){$('#strata-memory-result').textContent='Сначала отметьте приложения.';return;}
  if(!await ask(`Закрыть выбранные приложения (${pids.length})? Несохранённые данные в них могут потеряться.`,{ok:'Закрыть',danger:true}))return;
  const b=$('#strata-memory-close');b.disabled=true;
  try { const r=await core.call('memory_close',{pids,required_gib:strataMemoryRequired}); const freed=Number(r.freed_gib || 0).toFixed(1); const failed=(r.failed||[]).map(x=>x.name).filter(Boolean); const tail=failed.length?` Не удалось завершить: ${failed.join(', ')}. Закройте их обычным способом.`:Number(r.shortfall_gib || 0)>0?`Ещё не хватает ${Number(r.shortfall_gib).toFixed(1)} ГБ ОЗУ.`:'ОЗУ теперь достаточно — нажмите «Готово» и запустите Strata.'; $('#strata-memory-result').textContent=`Освобождено ОЗУ суммарно: ${freed} ГБ. ОЗУ свободно теперь: ${Number(r.ram_available_gib ?? r.free_gib ?? 0).toFixed(1)} ГБ.${tail}`; renderStrataMemory(r); await loadStrataStatus(); }
  catch(e){$('#strata-memory-result').textContent='Не удалось закрыть выбранные приложения: '+e.message;}
  finally{b.disabled=false;}
};
async function useStrataProfile(profile) {
  profile={...profile};delete profile.models;
  const existing=Object.entries(S.llm.profiles).find(([,p])=>profile.strata_package_id ? p.strata_package_id===profile.strata_package_id : p.base_url?.replace(/\/$/,'')===profile.base_url.replace(/\/$/,'') && p.model===profile.model);
  let id=existing?.[0];
  if (id) {
    await save({llm:{profiles:{...S.llm.profiles,[id]:{...existing[1],...profile}}}});
  } else {
    await addProfile('strata',profile);
    id=Object.entries(S.llm.profiles).find(([,p])=>p.base_url===profile.base_url && p.model===profile.model)?.[0];
  }
  if (!id)throw Error('Профиль не сохранился');
  await save({llm:{active:id}}); openProf=id;renderProfiles();
  toast(S.llm.auto_select ? 'Профиль Strata сохранён. Автовыбор учитывает его приоритет и часы.' : 'Модель Strata подключена.');
}
$('#strata-probe').onclick=async()=>{
  const button=$('#strata-probe'), result=$('#strata-api-result');button.disabled=true;$('#strata-use').disabled=true;strataApiProfile=null;
  try {
    const profile=await core.call('strata_probe',{base_url:$('#strata-url').value,api_key:$('#strata-key').value});
    strataApiProfile=profile;
    if(profile.base_url)$('#strata-url').value=profile.base_url;
    $('#strata-api-model').innerHTML=profile.models.map(id=>`<option value="${escH(id)}">${escH(id)}</option>`).join('');
    $('#strata-use').disabled=!profile.models.length;
    result.textContent=profile.models.length ? `Моделей в API: ${profile.models.length}. Выберите нужную.` : (profile.discovered ? 'Strata Desktop найдена, но в её API пока нет загруженной модели. Запустите модель в Strata, затем нажмите «Получить список» ещё раз.' : 'API отвечает, но нет загруженных моделей. Загрузите модель в Strata.');
  } catch(error){result.textContent=error.message;}
  finally{button.disabled=false;}
};
for (const id of ['strata-url','strata-key']) $('#'+id).oninput=()=>{strataApiProfile=null;$('#strata-use').disabled=true;};
$('#strata-use').onclick=async()=>{
  const button=$('#strata-use');button.disabled=true;
  try {
    if (!strataApiProfile)throw Error('Сначала получите список моделей');
    await useStrataProfile({...strataApiProfile,model:$('#strata-api-model').value});
  } catch(error){toast(error.message);}
  finally{button.disabled=!strataApiProfile;}
};
for (const [id,cmd] of [['strata-start','strata_start'],['strata-stop','strata_stop'],['strata-use-owned','strata_connection']]) {
  $('#'+id).onclick=async()=>{
    strataBusy=true;$('#'+id).disabled=true;
    let failed='';
    try {
      if (cmd==='strata_start' && !$('#strata-context').reportValidity())throw Error('Проверьте размер контекста');
      const result=await core.call(cmd,cmd==='strata_start' ? {model_id:$('#strata-package').value,context:+$('#strata-context').value} : {});
      if (cmd==='strata_connection')await useStrataProfile(result);
      if (cmd==='strata_start' && result && result.ok===false && result.memory) { await openStrataMemory(result.memory); failed=''; }
    } catch(error){failed=error.message;toast(error.message);}
    finally{strataBusy=false;await loadStrataStatus();if(failed)$('#strata-status').textContent+=' '+failed;}
  };
}
$('#strata-refresh').onclick=loadStrataStatus;
document.querySelector('nav button[data-p="brain"]').addEventListener('click',loadStrataStatus);
core.on('hello',()=>{if(document.querySelector('#p-brain.cur'))loadStrataStatus();});
