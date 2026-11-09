/* Студия Dewiar, разделы «Задачи» и обновление в «О программе».
   Задачи — долгие поручения мастерской (core/tasks.py): список, карточка с вкладками (план, блокнот, результаты, журнал,
   изменения кода, итог), просмотр и выгрузка результатов, «идеи» для пустой страницы. Команды ядра: tasks_list, task_get,
   task_create, task_tell, task_answer, task_resume/stop/accept/discard/delete, task_export, ws_open, ws_read;
   событие "task" перерисовывает раздел, пока он открыт. Обновление: renderUpdate() показывает update_status,
   ставит его окно (api().install_update), экран хода установки — updShow/updPoll в day.js.
   Файлы студии подключаются по порядку из ui/settings.html и делят одну глобальную область: при загрузке здесь
   вызываются только $/$$ из core.js; escH и md (folder.js) нужны лишь при отрисовке, когда всё уже загружено. */
/* ---------- задачи ---------- */
let tCur = "", tTab = "plan", tTimer = null;
let taskLinkOpened = false;
function openLinkedTask() {
  const id = new URLSearchParams(location.search).get('task');
  if (!id || !/^[a-f0-9]{8}$/.test(id) || taskLinkOpened) return;
  taskLinkOpened = true;
  tCur = id;
  document.querySelector('nav button[data-p="tasks"]')?.click();
  openTask(id);
}
core.on('hello', openLinkedTask);
if (core.connected) setTimeout(openLinkedTask, 0);
const taskTeamDrafts = new Map();
const COUNCIL_STATES = {pending:"Ожидает обсуждения",discussing:"Модели обсуждают план",evaluating:"Оценщик проверяет закрытие замечаний",agreed:"Обсуждение завершено и план согласован",no_consensus:"Консенсус не достигнут",blocked:"Участник или оценщик недоступен",cancelled:"Обсуждение остановлено",budget_exhausted:"Время обсуждения истекло"};
function taskTeamEditor(prefix, team = {}, locked = false) {
  const options = (selected,empty) => `<option value="">${escH(empty)}</option>` + Object.entries(S.llm.profiles || {}).map(([id,p]) => `<option value="${escH(id)}"${id === selected ? ' selected' : ''}>${escH(p.name || id)} · ${escH(p.model || 'модель не выбрана')}</option>`).join('');
  return `<div class="task-team" data-task-team="${prefix}"><label><input type="checkbox" data-team="enabled"${team.enabled ? ' checked' : ''}${locked ? ' disabled' : ''}> Совет планирования и команда моделей</label>
  <p class="hint">Каждый круг расходует лимиты выбранных моделей. План передаётся по очереди; только согласие всех с одной версией разрешает разработку. Недоступная назначенная модель не заменяется автоматически.</p>
  <div class="row" data-team-fields style="${team.enabled ? '' : 'display:none'}">
  ${Array.from({length:6},(_,i)=>`<label class="f">Планировщик ${i+1}<select data-planner="${i}">${options(team.planners?.[i],i<2 ? 'Выберите модель' : 'Не участвует')}</select></label>`).join('')}
  <label class="f">Оценка завершения обсуждения<select data-team="judge">${options(team.judge,'Активная модель при запуске обсуждения')}</select></label>
  <label class="f">Разработчик<select data-team="developer">${options(team.developer,'Автовыбор / модель для задач')}</select></label>
  <label class="f">Анализ тестов<select data-team="tester">${options(team.tester,'Без отдельной модели')}</select></label>
  <label class="f">Ревью кода и результата<select data-team="reviewer">${options(team.reviewer,'Самопроверка разработчиком')}</select></label>
  <label class="f">Максимум кругов<input type="number" data-team="max_rounds" min="1" max="8" step="1" value="${team.max_rounds || 3}"></label>
  <label class="f">Время обсуждения, минут<input type="number" data-team="budget_minutes" min="0.5" max="30" step="0.5" value="${(team.budget_seconds || 600)/60}"></label>
  <p class="hint">Нужно минимум две разные модели. После единогласия оценщик проверяет закрытие замечаний; нерешённые вопросы возвращаются участникам. Выбранный оценщик закрепляется до перезапуска обсуждения. Сейчас поддерживаются модели с текстовым JSON-ответом; числовая reward-модель требует отдельного адаптера. При отсутствии консенсуса задача приостанавливается. Анализ тестов — проверка покрытия и результатов; сами тесты запускает ядро в рабочей копии.</p></div>
  ${locked ? '<p class="hint">Для изменения команды сначала остановите задачу.</p>' : ''}</div>`;
}
function readTaskTeam(prefix) {
  const box = document.querySelector(`[data-task-team="${prefix}"]`);
  if (!box || !box.querySelector('[data-team=enabled]').checked) return {enabled:false};
  for (const input of box.querySelectorAll('input[type=number]')) if (!input.reportValidity()) throw Error('Проверьте пределы обсуждения');
  const value = key => box.querySelector(`[data-team="${key}"]`).value;
  return {enabled:true,planners:[...box.querySelectorAll('[data-planner]')].map(s=>s.value).filter(Boolean),
          judge:value('judge'),developer:value('developer'),tester:value('tester'),reviewer:value('reviewer'),max_rounds:+value('max_rounds'),budget_seconds:+value('budget_minutes')*60};
}
function wireTaskTeam(prefix, locked = false, onChange = () => {}) {
  const box = document.querySelector(`[data-task-team="${prefix}"]`); if (!box) return;
  if (locked) box.querySelectorAll('input,select').forEach(el=>el.disabled=true);
  box.querySelectorAll('input,select').forEach(el=>el.onchange=()=>{
    box.querySelector('[data-team-fields]').style.display = box.querySelector('[data-team=enabled]').checked ? '' : 'none';
    onChange();
  });
}
const tsince = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
function taskPin(id, on, pins) {
  const next = pins.filter(x => x !== id).concat(on ? [id] : []);
  S.ui = Object.assign({}, S.ui, { task_pins: next });                 // сразу — без ожидания ответа ядра
  save({ ui: { task_pins: next } }, true).then(loadTasks); loadTasks();
}
async function loadTasks() {
  try {
    let list = await core.call("tasks_list");
    const box = $("#t-list"); box.innerHTML = list.length ? "" : '<div class="fempty" style="padding:30px 8px">Пока пусто</div>';
    $("#t-box").style.display = list.length ? "" : "none";
    $("#t-ideas").style.display = list.length ? "none" : "";
    // №201 (24.10): «закреплять пин — видеть, что исполняется, а не скролить вверх и вниз»: закреплённые — сверху,
    // в порядке закрепления (ui.task_pins); булавка — при наведении, у закреплённых — всегда
    const ids = new Set(list.map(t => t.id)), pins = ((S.ui && S.ui.task_pins) || []).filter(id => ids.has(id));
    list = pins.map(id => list.find(t => t.id === id)).concat(list.filter(t => !pins.includes(t.id)));
    list.forEach(t => {
      const pinned = pins.includes(t.id);
      const el = document.createElement("div"); el.className = "titem" + (t.id === tCur ? " cur" : "") + (pinned ? " pinned" : "");
      const pr = t.progress && t.progress[1] ? t.progress[0] / t.progress[1] : 0;
      el.innerHTML = `<b></b><span><i class="badge ${t.status}">${t.status_ru}</i>${tsince(t.created)}${t.project ? " · код" : ""}</span>` +
        (["running", "waiting", "sleeping"].includes(t.status) || pr ? `<i class="tbar${t.status === "running" ? " live" : ""}"><b style="width:${Math.max(3, (t.status === "done" ? 1 : pr) * 100)}%"></b></i>` : "");
      $("b", el).textContent = t.title; el.onclick = () => openTask(t.id);
      const pb = document.createElement("button"); pb.className = "tpin"; pb.type = "button";
      pb.title = pinned ? "Открепить" : "Закрепить сверху"; pb.setAttribute("aria-label", pb.title); pb.setAttribute("aria-pressed", String(pinned));
      pb.innerHTML = '<svg viewBox="0 0 24 24"><path d="M9 3.5h6l-1 5.5 3.5 3.5v1.5h-11V12.5L10 9z"/><path d="M12 14v6.5"/></svg>';
      pb.onclick = (e) => { e.stopPropagation(); taskPin(t.id, !pinned, pins); };
      el.appendChild(pb); box.appendChild(el);
    });
    if (!tCur && list.length) openTask(list[0].id);
    $("#t-llm").innerHTML = `<option value="">Та же, что для разговора</option>` + Object.entries(S.llm.profiles || {}).map(([k, p]) => `<option value="${k}">${escH(p.name || k)}</option>`).join("");
    $("#t-llm").value = S.tasks && S.tasks.llm || "";
  } catch (e) { toast(e.message); }
}
function renderDiff(d) {
  return `<div class="diff">${d.split("\n").map(l => { const c = l.startsWith("diff --git") ? "file" : l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : "";
    return c === "file" ? `<div class="file">${escH(l.replace(/^diff --git a\/(.*) b\/.*/, "$1"))}</div>` : (l.startsWith("index ") || l.startsWith("+++") || l.startsWith("---")) ? "" : `<div class="${c}">${escH(l) || " "}</div>`; }).join("")}</div>`;
}
let tOpenedFor = "", tOutSel = "";   // для какой задачи уже выбрана вкладка по умолчанию; выбранный файл результата
let taskViewVersion = 0;
const TASK_COPY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>';
const TASK_EDIT_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16.5-.8 4.3 4.3-.8L19.8 7.7a2.1 2.1 0 0 0-3-3L4 16.5Z"/><path d="m14.8 6.7 3 3"/></svg>';
const TASK_SEND_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6"/></svg>';
const TASK_DELETE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 7h15M9.5 7V5a1.5 1.5 0 0 1 1.5-1h2a1.5 1.5 0 0 1 1.5 1v2M6.5 7l1 12.5a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4L17.5 7"/></svg>';
function taskCopySource(button, text, truncated = false, empty = "В этой вкладке пока нет текста для копирования") {
  if (!button?.isConnected) return;
  button._copyText = String(text || ""); button._copyTruncated = !!truncated;
  button.disabled = !button._copyText.trim();
  button.title = button.disabled ? empty : "Скопировать выделенный текст этой вкладки; если ничего не выделено — весь её текст. Для Markdown и кода сохраняется исходное оформление." + (truncated ? " Файл большой: доступен только загруженный фрагмент, не весь файл." : "");
  button.setAttribute('aria-label', button.disabled ? empty : 'Скопировать текст открытой вкладки задачи');
}
function wireTaskCopy(t, tab) {
  const button = $('#t-copy'), body = $('#t-content');
  button.hidden = tab === 'team';
  const fields = {plan:'plan',notebook:'notebook',journal:'journal',summary:'summary',diff:'diff',discussion:'discussion',team_review:'team_review'};
  taskCopySource(button, fields[tab] ? t[fields[tab]] : '', false, tab === 'results' ? 'Текст результата загружается; для изображений, аудио и видео используйте текстовую расшифровку' : undefined);
  button.onpointerdown = e => e.preventDefault(); // preserve an existing text selection when clicking the icon
  button.onclick = async () => {
    const selection = window.getSelection();
    const selected = selection && !selection.isCollapsed && body.contains(selection.anchorNode) && body.contains(selection.focusNode) ? selection.toString() : '';
    const text = selected || button._copyText;
    if (!text?.trim()) return;
    const truncated = !selected && button._copyTruncated;
    button.disabled = true;
    try {
      // The shared Studio clipboard helper is also used by chat message buttons.
      await chatCopyText(text);
      toast(truncated ? 'Скопирован загруженный фрагмент. Полный файл откройте в его программе.' : selected ? 'Выделенный текст скопирован' : 'Текст скопирован', truncated ? 5000 : 1800);
    } catch (e) { toast(e.message || 'Не удалось скопировать текст', 5000, 'bad'); }
    finally { if (button.isConnected) button.disabled = !button._copyText?.trim(); }
  };
}
function taskHtmlText(html) {
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  doc.querySelectorAll('script,style,noscript,iframe,template').forEach(e => e.remove());
  doc.querySelectorAll('br').forEach(e => e.replaceWith(doc.createTextNode('\n')));
  doc.querySelectorAll('p,div,section,article,h1,h2,h3,h4,h5,h6,li,tr').forEach(e => e.append(doc.createTextNode('\n')));
  return (doc.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
}
/* план — живым чек-листом: сделано ✓, текущий пункт крутится, остальные ждут */
function renderPlan(text, live, activity) {
  const lines = (text || "").split("\n"), items = [], rest = [];
  lines.forEach(l => { const m = l.match(/^\s*[-*]\s*\[( |x|X)\]\s*(.*)$/); if (m) items.push({ done: m[1] !== " ", text: m[2] }); else if (!items.length) rest.push(l); });
  if (!items.length) return `<div class="md">${md(text || "План ещё не составлен.")}</div>`;
  const cur = live ? items.findIndex(x => !x.done) : -1, done = items.filter(x => x.done).length;
  return `<div class="md" style="margin-bottom:6px">${md(rest.join("\n").replace(/^#.*$/m, ""))}</div>
    <div class="pchk">${items.map((x, i) => `<div class="pi ${x.done ? "done" : i === cur ? "now" : ""}"><i></i><span>${md(x.text).replace(/^<p>|<\/p>\s*$/g, "")}</span></div>`).join("")}</div>
    ${live && activity ? `<div class="pnow"><i class="tspin"></i><span>${escH(activity)}</span></div>` : ""}
    <div class="hint" style="margin-top:8px">Выполнено ${done} из ${items.length}</div>`;
}
const OUT_ICON = { html: "🌐", htm: "🌐", pdf: "📕", png: "🖼", jpg: "🖼", jpeg: "🖼", webp: "🖼", gif: "🖼", bmp: "🖼", tif: "🖼", tiff: "🖼", svg: "🖼", md: "📝", txt: "📝", srt: "💬",
                   csv: "📊", tsv: "📊", xlsx: "📊", docx: "📄", odt: "📄", pptx: "📽", mp3: "🎵", mpeg: "🎵", wav: "🎵", flac: "🎵", ogg: "🎵", opus: "🎵", m4a: "🎵", aac: "🎵", mp4: "🎬", zip: "🗜" };
/* Результат показывается прямо с диска (file://): # и ? в имени файла иначе читаются как якорь и строка запроса;
   t.dir — абсолютный путь ядра, в Windows с буквой диска и «\» -> file:///C:/… */
function outUrl(t, rel) { const p = (t.dir + "/" + rel).replace(/\\/g, "/"); return "file://" + (p.startsWith("/") ? "" : "/") + encodeURI(p).replace(/#/g, "%23").replace(/\?/g, "%3F"); }
/* Windows: страницы студии отдаёт http://127.0.0.1 (ui/app.py, _url), а со страницы http браузер не загружает file://
   («Not allowed to load local resource») — там страницу и SVG берём текстом через ws_read, картинку покажет своя программа */
const OUT_FILE_OK = location.protocol === "file:";
function renderOutputs(t) {
  const outs = t.outputs || [];
  const stages = ["media-transcription", "text-extraction", "contact-extraction"].includes(t.workflow);
  const preferred = t.workflow === 'media-transcription' ? (outs.slice().reverse().find(o => o.path.endsWith('/transcript.summary.md')) || outs.slice().reverse().find(o => o.path.endsWith('/transcript.clean.md')) || outs.slice().reverse().find(o => o.path.endsWith('/transcript.txt'))) : null;
  if (!outs.find(o => o.path === tOutSel)) tOutSel = (preferred || outs[outs.length - 1] || {}).path || "";
  const shown = t.workflow === 'media-transcription' ? outs.slice() : outs.slice().reverse();
  return `<div class="outs${stages ? " stages" : ""}">${shown.map(o => { const ext = o.path.split(".").pop().toLowerCase();
      const textFile = ["md", "txt", "srt", "csv", "tsv", "json"].includes(ext);
      const editable = ["media-transcription", "text-extraction", "contact-extraction"].includes(t.workflow) && ["md", "txt", "srt", "csv", "tsv"].includes(ext);
      return `<div class="oi-row"><button class="oi${o.path === tOutSel ? " on" : ""}" data-p="${escH(o.path)}"><b>${OUT_ICON[ext] || "📎"}</b><span>${escH(o.title || o.path)}<small>${escH(o.path)}</small></span></button>${stages && textFile ? `<div class="oi-actions"><button class="btn small icon task-output-icon" data-out-copy="${escH(o.path)}" title="Скопировать текст этапа" aria-label="Скопировать текст этапа">${TASK_COPY_ICON}</button>${editable ? `<button class="btn small icon task-output-icon" data-out-edit="${escH(o.path)}" title="Редактировать файл этапа" aria-label="Редактировать файл этапа">${TASK_EDIT_ICON}</button>` : ""}</div>` : ""}</div>`; }).join("")}</div>
    <div class="opane"><div class="obar"><span id="o-name"></span><span style="flex:1"></span>
      <button class="btn small" id="o-full">На весь экран</button><button class="btn small" id="o-open">Открыть</button><button class="btn small" id="o-dir">Папка</button>
      <button class="btn small primary" id="o-dl">Скачать</button><button class="btn small primary" id="o-pdf">PDF</button></div>
      <div id="o-view"></div></div>`;
}
async function wireOutputs(t) {
  const rel = tOutSel; if (!rel) return;
  const dirRel = "Задачи/" + t.dir.replace(/\\/g, "/").split("/Задачи/").pop();   // ws_* принимают пути от корня папки ассистента
  $$("#t-view .oi").forEach(b => b.onclick = () => { tOutSel = b.dataset.p; openTask(t.id); });
  $$("#t-view [data-out-copy]").forEach(b => b.onclick = async e => {
    e.stopPropagation(); b.disabled = true;
    try { const f = await core.call("ws_read", {path:dirRel + "/" + b.dataset.outCopy});
      if (f.truncated || f.binary) throw Error("Файл слишком большой или не содержит текст для копирования");
      await chatCopyText(f.text || ""); toast("Текст этапа скопирован");
    } catch (error) { toast(error.message || "Не удалось скопировать текст", 5000, "bad"); }
    finally { b.disabled = false; }
  });
  $$("#t-view [data-out-edit]").forEach(b => b.onclick = async e => {
    e.stopPropagation(); tOutSel = b.dataset.outEdit;
    $$("#t-view .oi").forEach(item => item.classList.toggle("on", item.dataset.p === tOutSel));
    $("#o-name").textContent = tOutSel;
    const view = $("#o-view"); if (!view) return;
    try {
      const f = await core.call("ws_read", {path:dirRel + "/" + tOutSel});
      if (f.truncated || f.binary) throw Error("Этот файл слишком большой для встроенного редактора");
      const area = document.createElement("textarea"); area.className = "otext oedit"; area.value = f.text || "";
      area.setAttribute("aria-label", `Редактирование ${tOutSel}`); view.replaceChildren(area);
      const controls = document.createElement("div"); controls.className = "oedit-actions";
      const save = document.createElement("button"); save.className = "btn small primary"; save.textContent = "Сохранить";
      const cancel = document.createElement("button"); cancel.className = "btn small"; cancel.textContent = "Отмена";
      controls.append(save, cancel); view.append(controls); area.focus();
      cancel.onclick = () => wireOutputs(t);
      save.onclick = async () => { save.disabled = true;
        try { await core.call("task_write_result", {id:t.id,path:tOutSel,text:area.value}); toast("Изменения сохранены"); await openTask(t.id); }
        catch (error) { toast(error.message || "Не удалось сохранить файл", 6000, "bad"); save.disabled = false; }
      };
    } catch (error) { toast(error.message || "Не удалось открыть файл для редактирования", 6000, "bad"); }
  });
  $("#o-name").textContent = rel;
  $("#o-open").onclick = () => core.call("ws_open", { path: dirRel + "/" + rel }).catch(e => toast(e.message));
  const ext0 = rel.split(".").pop().toLowerCase(), page = ["html", "htm"].includes(ext0);
  $("#o-dl").textContent = page ? "Скачать HTML" : "Скачать";
  $("#o-pdf").style.display = page || ext0 === "md" ? "" : "none";
  const dl = async (fmt, b) => {                      // копия в «Загрузки»; PDF печатает браузер компьютера без окон (core/export.py)
    const was = b.textContent; b.disabled = true; b.textContent = fmt === "pdf" ? "Делаю PDF…" : "Сохраняю…";
    try { const r = await core.call("task_export", { id: t.id, path: rel, fmt }); toast(`Сохранено в «${r.dir.split(/[\\/]/).pop()}»: ${r.name}`, 5000); }
    catch (e) { toast(e.message, 7000); }
    finally { b.disabled = false; b.textContent = was; }
  };
  $("#o-dl").onclick = () => dl(page ? "html" : "file", $("#o-dl"));
  $("#o-pdf").onclick = () => dl("pdf", $("#o-pdf"));
  $("#o-dir").onclick = () => core.call("ws_open", { path: dirRel + "/" + rel.split("/").slice(0, -1).join("/") }).catch(e => toast(e.message));
  const ext = rel.split(".").pop().toLowerCase(), v = $("#o-view"), copy = $('#t-copy');
  const copyResult = f => taskCopySource(copy, f.binary ? '' : page ? taskHtmlText(f.text) : f.text, f.truncated, 'У этого файла нет доступного текста. Для аудио и видео выберите расшифровку.');
  $("#o-full").style.display = ["html", "htm", "png", "jpg", "jpeg", "webp", "svg"].includes(ext) ? "" : "none";
  $("#o-full").onclick = () => { const el = v.firstElementChild; if (el) (el.requestFullscreen || el.webkitRequestFullscreen || (() => {})).call(el); };
  const openHint = `<p class="hint" style="padding:30px">Этот файл открывается в своей программе — нажмите «Открыть».</p>`;
  if (["html", "htm"].includes(ext)) {
    if (OUT_FILE_OK) {
      v.innerHTML = `<iframe src="${outUrl(t, rel)}" sandbox="allow-scripts allow-same-origin allow-popups allow-forms"></iframe>`;
      try { copyResult(await core.call('ws_read', {path:dirRel + '/' + rel})); }
      catch (e) { taskCopySource(copy, '', false, 'Не удалось прочитать текст результата: ' + e.message); }
    }
    else try {
      const f = await core.call("ws_read", { path: dirRel + "/" + rel });
      copyResult(f);
      v.innerHTML = `<iframe sandbox="allow-scripts allow-popups allow-forms"></iframe>${f.truncated ? '<p class="hint">Показано начало страницы — целиком она откроется по кнопке «Открыть».</p>' : ""}`;
      $("iframe", v).srcdoc = f.text || "";
    } catch (e) { v.innerHTML = `<p class="hint">${escH(e.message)}</p>`; taskCopySource(copy, '', false, 'Не удалось прочитать текст результата: ' + e.message); }
  } else if (["png", "jpg", "jpeg", "webp", "svg", "gif"].includes(ext)) {
    taskCopySource(copy, '', false, 'Это изображение. Для копирования текста выберите текстовый результат задачи.');
    if (OUT_FILE_OK) v.innerHTML = `<img src="${outUrl(t, rel)}" alt="">`;
    else if (ext === "svg") try {
      const f = await core.call("ws_read", { path: dirRel + "/" + rel });
      v.innerHTML = `<img alt="">`; $("img", v).src = URL.createObjectURL(new Blob([f.text || ""], { type: "image/svg+xml" }));
    } catch (e) { v.innerHTML = `<p class="hint">${escH(e.message)}</p>`; }
    else v.innerHTML = openHint;
  } else if (["md", "txt", "srt", "csv", "json", "py", "js", "css"].includes(ext)) {
    try { const f = await core.call("ws_read", { path: dirRel + "/" + rel }); copyResult(f); v.innerHTML = ext === "md" ? `<div class="md otext">${md(f.text || "")}</div>` : `<pre class="otext"></pre>`; if (ext !== "md") $("pre", v).textContent = f.text || "";
      v.querySelectorAll("[data-contact-frame]").forEach(link => link.addEventListener("click", event => {
        event.preventDefault();
        const imagePath = `${rel.slice(0, rel.lastIndexOf("/"))}/${link.dataset.contactFrame}`;
        if ((t.outputs || []).some(item => item.path === imagePath)) { tOutSel = imagePath; openTask(t.id); }
        else core.call("ws_open", { path: dirRel + "/" + imagePath }).catch(error => toast(error.message || "Не удалось открыть кадр"));
      }));
      if (f.truncated) v.insertAdjacentHTML('beforeend', '<p class="hint">Показан и доступен для копирования только загруженный фрагмент. Полный файл откройте в его программе.</p>'); }
    catch (e) { v.innerHTML = `<p class="hint">${escH(e.message)}</p>`; taskCopySource(copy, '', false, 'Не удалось прочитать текст результата: ' + e.message); }
  } else {
    v.innerHTML = openHint;
    if (['pdf','docx','odt'].includes(ext)) {
      try { copyResult(await core.call('ws_read', {path:dirRel + '/' + rel})); }
      catch (e) { taskCopySource(copy, '', false, 'Не удалось извлечь текст результата: ' + e.message); }
    } else taskCopySource(copy, '', false, 'У этого файла нет доступного текста. Для аудио и видео выберите расшифровку.');
  }
}
async function openTask(id) {
  const version = ++taskViewVersion;
  tCur = id;
  try {
    const t = await core.call("task_get", { id });
    if (version !== taskViewVersion || tCur !== id) return;
    // карточка перерисовывается целиком на каждое событие задачи — запоминаем набранный текст и фокус полей
    const keep = tOpenedFor === id ? ["t-msg", "t-ans"].map(k => [k, $("#" + k) ? $("#" + k).value : "", document.activeElement && document.activeElement.id === k]) : [];
    const outs = t.outputs || [];
    const nbFacts = (t.notebook || "").split("\n").filter(l => /^\s*[-*]\s+\S/.test(l)).length;
    const tabs = [["plan", "План"]].concat(nbFacts ? [["notebook", `Блокнот (${nbFacts})`]] : []).concat(outs.length ? [["results", `Результаты (${outs.length})`]] : []).concat([["journal", "Журнал"]])
      .concat(t.project ? [["diff", `Изменения${t.changes ? " (" + t.changes.length + ")" : ""}`]] : []).concat(t.summary ? [["summary", "Итог"]] : []).concat([["team","Команда"]]).concat(t.team?.enabled ? [["discussion","Обсуждение плана"],["team_review","Проверки команды"]] : []);
    if (tOpenedFor !== id) { tOpenedFor = id; tTab = outs.length && t.status === "done" ? "results" : t.summary ? "summary" : "plan"; }
    if (!tabs.find(x => x[0] === tTab)) tTab = t.summary ? "summary" : "plan";
    const acts = [];
    if (t.status === "sleeping") acts.push(`<button class="btn primary" data-a="task_resume">Продолжить сейчас</button>`);
    if (["running", "waiting", "sleeping"].includes(t.status)) acts.push(`<button class="btn" data-a="task_stop">Остановить</button>`);
    if (["paused", "stopped", "failed"].includes(t.status)) {
      if (t.team?.enabled && ['no_consensus','budget_exhausted'].includes(t.council?.status)) acts.push(`<button class="btn primary" data-a="task_team_restart">Перезапустить обсуждение</button>`);
      else acts.push(`<button class="btn primary" data-a="task_resume">Продолжить</button>`);
    }
    if (t.project && t.changes && t.changes.length && !["running", "waiting", "sleeping"].includes(t.status)) acts.push(`<button class="btn primary" data-a="task_accept">Принять изменения</button><button class="btn" data-a="task_discard">Отклонить</button>`);
    const deleteAction = `<button class="btn small danger t-delete" data-a="task_delete" title="Удалить задачу вместе с её папкой" aria-label="Удалить задачу">${TASK_DELETE_ICON}</button>`;
    if (t.status === "waiting") acts.push(deleteAction);
    const prog = t.progress && t.progress[1] ? ` · план ${t.progress[0]}/${t.progress[1]}` : "";
    let body = "";
    if (tTab === "plan") body = renderPlan(t.plan, ["running", "waiting"].includes(t.status), t.activity);
    if (tTab === "results") body = renderOutputs(t);
    if (tTab === "notebook") body = `<div class="md">${md(t.notebook)}</div><p class="hint">Блокнот — память задачи: факты, номера, договорённости. Ассистент ведёт его сам и сверяется с ним на каждом шаге.</p>`;
    if (tTab === "journal") body = `<div class="md">${md(t.journal)}</div>`;
    if (tTab === "discussion") body = `<div class="md">${md(t.discussion || 'Обсуждение ещё не началось.')}</div>`;
    if (tTab === "team_review") body = `<div class="md">${md(t.team_review || 'Заключений пока нет. Проверки начнутся после разработки и запуска тестов.')}</div>`;
    if (tTab === "team") {
      const locked = ["running","waiting","sleeping"].includes(t.status);
      body = taskTeamEditor('edit',taskTeamDrafts.get(id) || t.team || {},locked) + `<div class="actions"><button class="btn primary" id="task-team-save"${locked ? ' disabled' : ''}>Сохранить команду</button><button class="btn" id="task-team-restart"${locked ? ' disabled' : ''}>Перезапустить обсуждение</button></div>`;
    }
    if (tTab === "summary") body = `<div class="md">${md(t.summary)}</div>`;
    if (tTab === "diff") body = t.diff ? renderDiff(t.diff) : `<div class="fempty">${t.code_state === "accepted" ? "Изменения уже перенесены в проект." : t.code_state === "discarded" ? "Изменения отклонены." : "Изменений пока нет."}</div>`;
    const pr = t.status === "done" ? 1 : (t.progress && t.progress[1] ? t.progress[0] / t.progress[1] : 0);
    const mins = Math.max(0, Math.round((Date.now() - new Date(t.created)) / 60000));
    const live = ["running", "waiting"].includes(t.status);
    $("#t-view").innerHTML = `<div class="fhead"><div class="t"><b></b><span><i class="badge ${t.status}">${t.status_ru}</i>шагов ${t.steps}${prog}${t.project ? " · " + escH(t.project) : ""}</span></div><button type="button" class="btn task-copy" id="t-copy" title="Скопировать текст открытой вкладки задачи" aria-label="Скопировать текст открытой вкладки задачи">${TASK_COPY_ICON}</button></div>
      <div class="tprog${live ? " live" : ""}"><div class="bar"><b style="width:${Math.max(2, pr * 100)}%"></b></div>
        <div class="tline">${live ? `<i class="tspin"></i>` : ""}<span>${t.status === "done" ? "готово" : live ? (escH(t.activity) || "думает…") : t.status_ru}</span><em>${t.progress && t.progress[1] ? `${t.progress[0]} из ${t.progress[1]} пунктов плана · ` : ""}${live ? `идёт ${mins} мин` : ""}</em></div></div>
      ${t.error ? `<p class="res bad" style="margin-bottom:10px">${escH(t.error)}</p>` : ""}
      ${t.team?.enabled ? `<p class="hint task-council-status" role="status">${escH(COUNCIL_STATES[t.council?.status] || 'Совет ещё не начал работу')} · круг ${t.council?.round || 0}/${t.team.max_rounds} · запросов ${t.council?.calls || 0} · голосов за текущую версию ${Object.keys(t.council?.votes || {}).length}/${t.team.planners.length}</p>` : ''}
      ${t.council?.judge ? `<p class="hint">Оценщик: ${escH(t.council.judge.name)} · проверок ${t.council.judge_calls || 0}${t.council.evaluation?.reason ? ' · '+escH(t.council.evaluation.reason) : ''}</p>` : ''}
      ${t.status === "sleeping" ? `<div class="twait">⏳ <b>${escH(t.status_ru)}</b>${t.wait_reason ? " — " + escH(t.wait_reason) : ""}. Напишите задаче ниже — продолжит сразу.</div>` : ""}
      ${t.status === "waiting" ? `<div class="tq"><b>Вопрос:</b> ${escH(t.question)}<div class="actions" style="margin-top:8px"><input type="text" id="t-ans" placeholder="Ваш ответ" style="flex:1"><button class="btn primary" id="t-ansgo">Ответить</button></div></div>` : ""}
      <div class="tabs">${tabs.map(([k, n]) => `<button data-tab="${k}" class="${k === tTab ? "on" : ""}">${n}</button>`).join("")}</div><div id="t-content">${body}</div>
      ${t.status !== "waiting" ? `<div class="tmsg"><input type="text" id="t-msg" placeholder="${t.status === "done" ? "Дополнить задачу: что ещё сделать…" : "Написать задаче: новые сведения, уточнение, просьба…"}" aria-label="Команда выбранной задаче"><div class="tmsg-foot"><button class="btn small primary tmsg-send" id="t-msggo" title="Отправить команду задаче" aria-label="Отправить команду">${TASK_SEND_ICON}</button><button class="btn small tmsg-copy" id="t-msgcopy" title="Скопировать текст открытой вкладки задачи" aria-label="Скопировать текст открытой вкладки задачи">${TASK_COPY_ICON}</button><span></span>${deleteAction}</div></div>` : ""}
      <div class="actions">${acts.join("")}</div>`;
    $("#t-view .fhead b").textContent = t.title;
    wireTaskCopy(t, tTab);
    keep.forEach(([k, v, f]) => { const el = $("#" + k); if (el && v) el.value = v; if (el && f) el.focus(); });
    if ($("#t-msggo")) {
      const send = () => { const v = $("#t-msg").value.trim(); if (!v) return;
        core.call("task_tell", { id, text: v }).then(() => { toast("Передал задаче"); loadTasks(); openTask(id); }).catch(e => toast(e.message)); };
      $("#t-msggo").onclick = send; $("#t-msg").onkeydown = (e) => { if (e.key === "Enter") send(); };
    }
    if ($("#t-msgcopy")) $("#t-msgcopy").onclick = () => $("#t-copy")?.click();
    if (tTab === "results") wireOutputs(t);
    if (tTab === "team") {
      wireTaskTeam('edit',["running","waiting","sleeping"].includes(t.status),()=>{try {taskTeamDrafts.set(id,readTaskTeam('edit'));} catch(e) {toast(e.message);}});
      for (const [button,command] of [['task-team-save','task_team_set'],['task-team-restart','task_team_restart']]) {
        $("#"+button).onclick = async () => {
          const b = $("#"+button); b.disabled = true;
          try {
            await core.call(command,{id,team:readTaskTeam('edit')}); taskTeamDrafts.delete(id);
            toast(command === 'task_team_set' ? 'Команда сохранена' : 'Обсуждение запущено'); await openTask(id); loadTasks();
          } catch(e) {toast(e.message,7000,'error'); b.disabled=false;}
        };
      }
    }
    $$("#t-view [data-tab]").forEach(b => b.onclick = () => { tTab = b.dataset.tab; openTask(id); });
    $$("#t-view [data-a]").forEach(b => b.onclick = async () => {
      if (b.dataset.a === "task_delete" && !await ask("Удалить задачу вместе с её папкой?")) return;
      if (b.dataset.a === "task_discard" && !await ask("Отклонить изменения? Рабочая копия будет удалена.")) return;
      try { const r = await core.call(b.dataset.a, { id }); if (typeof r === "string") toast(r); if (b.dataset.a === "task_delete") tCur = ""; loadTasks(); if (b.dataset.a !== "task_delete") openTask(id); else $("#t-view").innerHTML = ""; } catch (e) { toast(e.message); }
    });
    if ($("#t-ansgo")) $("#t-ansgo").onclick = () => core.call("task_answer", { id, text: $("#t-ans").value }).then(() => openTask(id)).catch(e => toast(e.message));
    $$("#t-list .titem").forEach(el => el.classList.toggle("cur", $("b", el).textContent === t.title));
  } catch (e) { toast(e.message); }
}
$("#t-new").onclick = () => {
  if (!S) return toast('Дождитесь подключения к ядру');
  let previous; try {previous = readTaskTeam('new');} catch(e) {return toast(e.message);}
  $("#t-new-team").innerHTML = taskTeamEditor('new',previous); wireTaskTeam('new');
  $("#t-form").style.display = ""; $("#t-goal").focus();
};
/* Идеи для первой задачи: пустая страница сразу показывает, на что способна мастерская.
   Поля: название, подпись, текст поручения (подставляется в форму), примерное время, содержимое SVG-значка 24×24. */
const T_IDEAS = [
  ["Презентация", "6 слайдов о вашем проекте — файл в «Результатах».", "Сделай презентацию на 6 слайдов о том, что умеет голосовой ассистент Dewiar: для кого он, главные возможности, как начать. Сохрани в папку «Результаты».", "~3 мин",
   '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v4M8 20h8M7 12l3-3 2 2 4-4"/>'],
  ["Сравнить и выбрать", "Таблица вариантов с плюсами, минусами и выводом.", "Сравни три хороших ноутбука до 1000 долларов для учёбы и работы: таблица с характеристиками, плюсы и минусы, и что выбрать. Оформи красивой страницей.", "~4 мин, нужен интернет",
   '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'],
  ["Фильмы на вечер", "Подборка карточками: постер, год, о чём фильм.", "Подбери 8 фильмов на вечер в жанре фантастика — хорошие и не самые очевидные. Покажи красиво карточками: название, год, о чём, почему стоит посмотреть.", "~3 мин, нужен интернет",
   '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 9h18M7 5l2 4M12 5l2 4M17 5l2 4"/>'],
  ["Расходы за месяц", "Таблица Excel и диаграмма по категориям.", "Собери мои расходы и доходы за этот месяц в таблицу Excel: по дням и по категориям, с итогами и диаграммой. Сохрани в «Результаты».", "~2 мин",
   '<path d="M12 3v9l6 4"/><circle cx="12" cy="12" r="9"/>'],
  ["План поездки", "Маршрут по дням, бюджет и список вещей.", "Составь план поездки в Санкт-Петербург на 3 дня в октябре: маршрут по дням, где поесть, примерный бюджет и список вещей. Оформи страницей, которую удобно открыть с телефона.", "~4 мин, нужен интернет",
   '<path d="M9 18l-6 3V6l6-3 6 3 6-3v15l-6 3-6-3z"/><path d="M9 3v15M15 6v15"/>'],
  ["Калькулятор", "Готовая страница: ввели числа — получили ответ.", "Сделай калькулятор ипотеки отдельной страницей: сумма, ставка, срок, первый взнос — ежемесячный платёж, переплата и график платежей.", "~3 мин",
   '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2M12 11h2M16 11h0M8 15h2M12 15h2M16 15v3M8 18h6"/>'],
];
$("#t-ideas-grid").innerHTML = T_IDEAS.map((x, i) => `<button class="tidea" data-i="${i}"><svg viewBox="0 0 24 24">${x[4]}</svg><b>${x[0]}</b><span>${x[1]}</span><em>${x[3]}</em></button>`).join("");
$$("#t-ideas-grid .tidea").forEach(b => b.onclick = () => {
  const x = T_IDEAS[+b.dataset.i]; $("#t-new").onclick(); $("#t-goal").value = x[2]; $("#t-title").value = x[0];
  $("#t-form").scrollIntoView({ behavior: "smooth", block: "center" }); $("#t-goal").focus();
});
$("#t-cancel").onclick = () => { $("#t-form").style.display = "none"; };
$("#t-pick").onclick = async () => { const a = api(); if (!a) return toast("Выбор папки доступен в окне студии"); const p = await a.pick_folder(); if (p) $("#t-proj").value = p; };
$("#t-go").onclick = async () => {
  const goal = $("#t-goal").value.trim(); if (!goal) return toast("Опишите, что сделать");
  const button = $("#t-go"); button.disabled=true;
  try { const t = await core.call("task_create", { goal, title: $("#t-title").value.trim(), project: $("#t-proj").value.trim(),team:readTaskTeam('new') });
    $("#t-form").style.display = "none"; $("#t-goal").value = ""; tCur = t.id; tTab = "plan"; loadTasks(); } catch (e) { toast(e.message); }
  finally {button.disabled=false;}
};
$("#t-llm").onchange = () => save({ tasks: { llm: $("#t-llm").value } });
core.on("task", e => {                     // события идут потоком на каждый шаг — перерисовываем не чаще раза в 400 мс
  if (!$("#p-tasks").classList.contains("cur")) return;
  clearTimeout(tTimer); tTimer = setTimeout(() => { loadTasks(); if (tCur === e.task.id) openTask(tCur); }, 400);
});

/* ---------- обновления ---------- */
function renderUpdate(u) {
  if (!u) return;
  $("#ab-title").textContent = `Dewiar ${u.current}`;
  const a = u.available;
  $("#up-state").textContent = a ? `Доступна версия ${a.version}${a.date ? " от " + a.date : ""} — уже скачана и проверена.` :
    u.error ? `Не удалось обновиться: ${u.error}` : u.checked ? `У вас последняя версия${u.remote ? " (на сайте — " + u.remote + ")" : ""}. Проверено ${new Date(u.checked * 1000).toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" })}.` : "Проверка — через минуту после запуска и раз в 6 часов.";
  $("#up-notes").innerHTML = a && a.notes ? md(a.notes) : "";
  $("#up-install").style.display = a ? "" : "none";
  $("#up-install").onclick = async () => {
    const x = api(); if (!x) return toast("Обновление запускается из окна студии");
    // у окна нет install_update: оно запущено из ui/app.py без этого метода — нужен перезапуск интерфейса
    if (typeof x.install_update !== "function") { $("#up-state").textContent = "Закройте Dewiar (и виджет) и откройте снова — затем нажмите «Обновить»."; return; }
    updShow(a.version, "auth");
    try { const r = await x.install_update(a.path);
          if (!r.ok) return updShow(a.version, "error", r.error);
          if (r.started) updPoll(a.version); else updShow(a.version, "restart"); }
    catch (e) { updShow(a.version, "error", e.message); }
  };
}
