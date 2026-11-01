/* «Руки → Голосовые команды»: editable templates, app aliases, preview without execution. */
(() => {
  const hands = document.getElementById('p-hands');
  const abilities = document.createElement('div'); abilities.id = 'hands-abilities';
  while (hands.children.length > 1) abilities.append(hands.children[1]);
  const tabs = document.createElement('div'); tabs.className = 'stabs'; tabs.setAttribute('role', 'tablist');
  tabs.innerHTML = '<button class="on" id="hands-tab-abilities" role="tab" aria-selected="true" aria-controls="hands-abilities">Умения</button><button id="hands-tab-commands" role="tab" aria-selected="false" aria-controls="hands-commands">Голосовые команды</button>';
  const panel = document.createElement('div'); panel.id = 'hands-commands'; panel.hidden = true; panel.className = 'vc-panel';
  panel.innerHTML = `
    <p class="lead">Настройте фразы и названия приложений. Общие настройки для всех персонажей; разрешения в «Умениях» продолжают действовать.</p>
    <div class="vc-toolbar"><label class="f">Поиск команд и приложений<input id="vc-search" type="search" placeholder="Например: музыка, закрыть, Клод"></label>
      <label class="f">Показать<select id="vc-filter"><option value="all">Все</option><option value="actions">Действия</option><option value="apps">Приложения</option></select></label>
      <button class="btn primary" id="vc-save">Сохранить изменения</button><button class="btn" id="vc-reset">Вернуть стандартные</button></div>
    <p id="vc-status" role="status" aria-live="polite"></p>
    <div class="card vc-preview"><h2>Проверить команду</h2><p class="hint">Проверка показывает действие и приложение или поисковый запрос. Программы не открываются и не закрываются. Используются текущие, ещё не сохранённые изменения.</p>
      <label class="f">Фраза без обращения «Лира»<input id="vc-test-text" type="text" placeholder="Включи песню Кино — Звезда по имени Солнце"></label>
      <button class="btn" id="vc-test">Проверить без выполнения</button><p id="vc-test-result" role="status" aria-live="polite"></p></div>
    <div id="vc-actions"></div><div id="vc-apps"></div>`;
  hands.append(tabs, abilities, panel);
  abilities.setAttribute('role', 'tabpanel'); abilities.setAttribute('aria-labelledby', 'hands-tab-abilities');
  panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', 'hands-tab-commands');
  const el = id => document.getElementById(id);
  let draft = null, defaults = null, installed = [], page = 0, busy = false;
  function status(text) { el('vc-status').textContent = text; }
  function dirty() { status('Есть несохранённые изменения.'); }
  function lines(text) { return text.split(/\n|,/).map(s => s.trim()).filter(Boolean); }
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  function field(label, value, change, multiline = false) {
    const wrap = node('label', null, 'f'), caption = node('span', label), input = document.createElement(multiline ? 'textarea' : 'input');
    input.value = value; if (multiline) input.rows = 3;
    else input.type = 'text';
    input.oninput = () => { change(input.value); dirty(); };
    wrap.append(caption, input); return wrap;
  }
  async function load() {
    if (draft || busy) return; busy = true; status('Загружаю команды…');
    try {
      const data = await core.call('voice_commands'); draft = data.config; defaults = data.defaults; installed = data.installed;
      render(); status('Настройки загружены.');
    } catch (e) { status('Не удалось загрузить: ' + e.message + '. Нажмите вкладку ещё раз.'); }
    finally { busy = false; }
  }
  tabs.querySelectorAll('button').forEach((b, index) => {
    b.onclick = () => { tabs.querySelectorAll('button').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', String(x === b)); });
      abilities.hidden = index !== 0; panel.hidden = index !== 1; if (index === 1) load(); };
    b.onkeydown = e => { if (['ArrowLeft', 'ArrowRight'].includes(e.key)) { e.preventDefault(); const next = tabs.querySelectorAll('button')[1 - index]; next.focus(); next.click(); } };
  });
  function render() {
    if (!draft) return;
    const q = el('vc-search').value.toLocaleLowerCase(), filter = el('vc-filter').value;
    const matches = value => JSON.stringify(value).toLocaleLowerCase().includes(q);
    const actions = el('vc-actions'); actions.replaceChildren(); actions.hidden = filter === 'apps';
    actions.append(node('h2', 'Действия'));
    actions.append(node('p', 'Одна фраза на строку. {приложение} — название программы, {запрос} — название песни. Для «Не беспокоить» указывайте цельную фразу без подстановок: «не беспокоить» или «переговоры».', 'hint'));
    const found = draft.actions.filter(matches);
    if (!found.length) actions.append(node('p', 'Подходящих действий нет.', 'hint'));
    found.forEach(a => {
      const box = node('details', null, 'card vc-item'), summary = node('summary');
      summary.append(node('b', a.title), node('span', a.phrases.length + ' фраз · ' + (a.enabled ? 'включено' : 'выключено'), 'hint'));
      const label = node('label', null, 'chk'), enabled = document.createElement('input'); enabled.type = 'checkbox'; enabled.checked = a.enabled;
      enabled.onchange = () => { a.enabled = enabled.checked; summary.lastChild.textContent = a.phrases.length + ' фраз · ' + (a.enabled ? 'включено' : 'выключено'); dirty(); };
      label.append(enabled, document.createTextNode('Выполнять эту голосовую команду'));
      box.append(summary, label, field('Фразы команды', a.phrases.join('\n'), text => { a.phrases = text.split('\n').map(s => s.trim()).filter(Boolean); summary.lastChild.textContent = a.phrases.length + ' фраз · ' + (a.enabled ? 'включено' : 'выключено'); }, true));
      actions.append(box);
    });
    const apps = el('vc-apps'); apps.replaceChildren(); apps.hidden = filter === 'actions';
    apps.append(node('h2', 'Названия приложений'));
    const add = node('button', 'Добавить приложение', 'btn');
    add.onclick = () => { draft.applications.unshift({name: '', target: '', aliases: []}); el('vc-search').value = ''; page = 0; dirty(); render(); apps.querySelector('details').open = true; apps.querySelector('input').focus(); };
    apps.append(add, node('p', 'Выберите установленное приложение. Варианты названия — по одному на строку или через запятую. «Код» не используется как сокращение VS Code.', 'hint'));
    const list = draft.applications.filter(matches), pages = Math.max(1, Math.ceil(list.length / 20)); page = Math.min(page, pages - 1);
    apps.append(node('p', 'Найдено приложений: ' + list.length, 'hint'));
    list.slice(page * 20, page * 20 + 20).forEach(app => {
      const box = node('details', null, 'card vc-item'), summary = node('summary'); summary.append(node('b', app.name || 'Новое приложение'), node('span', app.aliases.join(', ') || 'Нет дополнительных названий', 'hint'));
      const grid = node('div', null, 'vc-app-fields');
      grid.append(field('Название в списке', app.name, text => { app.name = text; summary.firstChild.textContent = text || 'Новое приложение'; }));
      const target = node('label', null, 'f'), select = document.createElement('select');
      target.append(node('span', 'Приложение на компьютере'), select);
      const options = installed.slice();
      if (!options.some(x => x.target === app.target)) options.unshift({name: app.target ? app.name + ' — не обнаружено' : 'Выберите приложение', target: app.target});
      options.forEach(x => { const option = node('option', x.name); option.value = x.target; select.append(option); });
      select.value = app.target;
      select.onchange = () => { app.target = select.value; if (!app.name) { app.name = installed.find(x => x.target === select.value)?.name || ''; grid.querySelector('input').value = app.name; summary.firstChild.textContent = app.name; } dirty(); };
      grid.append(target);
      const aliases = field('Как вы называете приложение', app.aliases.join('\n'), text => { app.aliases = lines(text); summary.lastChild.textContent = app.aliases.join(', '); }, true);
      const remove = node('button', 'Удалить из списка названий', 'btn'); remove.onclick = async () => { if (await ask('Удалить названия приложения «' + app.name + '» из голосовых команд? Сама программа останется на компьютере.', {ok: 'Удалить'})) { draft.applications.splice(draft.applications.indexOf(app), 1); dirty(); render(); } };
      box.append(summary, grid, aliases, remove); apps.append(box);
    });
    if (pages > 1) { const pager = node('div', null, 'vc-pager'); const prev = node('button', 'Назад', 'btn'), next = node('button', 'Далее', 'btn'); prev.disabled = page === 0; next.disabled = page === pages - 1; prev.onclick = () => { page--; render(); }; next.onclick = () => { page++; render(); }; pager.append(prev, node('span', `${page + 1} / ${pages}`), next); apps.append(pager); }
  }
  el('vc-search').oninput = () => { page = 0; render(); };
  el('vc-filter').onchange = render;
  el('vc-save').onclick = async () => {
    if (!draft || busy) return; busy = true; el('vc-save').disabled = true;
    try { const settings = await core.call('settings_save', {patch: {voice_commands: draft}}); S = settings; status('Сохранено. Команды применены без перезапуска.'); }
    catch (e) { status('Не сохранено: ' + e.message); }
    finally { busy = false; el('vc-save').disabled = false; }
  };
  el('vc-reset').onclick = async () => { if (defaults && await ask('Вернуть стандартные фразы и названия приложений? Ваши изменения будут заменены после сохранения.', {ok: 'Вернуть'})) { draft = JSON.parse(JSON.stringify(defaults)); page = 0; render(); dirty(); } };
  el('vc-test').onclick = async () => {
    if (!draft) return;
    try {
      const r = await core.call('voice_command_test', {text: el('vc-test-text').value, config: draft});
      const appName = r && (draft.applications.find(x => x.target === r.application)?.name || installed.find(x => x.target === r.application)?.name || r.application);
      const destination = r && r.action.startsWith('dnd_') ? 'Фокусировка macOS: «Не беспокоить»' : r?.query ? 'Поиск: ' + r.query : 'Приложение: ' + appName;
      el('vc-test-result').textContent = r ? `${r.title}. ${destination}. ${!r.enabled ? 'Команда выключена.' : !r.allowed ? 'Умение недоступно или запрещено.' : r.confirm ? 'Перед выполнением потребуется разрешение.' : 'Разрешено.'} Ничего не выполнено.` : 'Команда не распознана. Проверьте фразу и название приложения.';
    } catch (e) { el('vc-test-result').textContent = 'Проверьте настройки: ' + e.message; }
  };
})();
