/* Native shell owns the once-per-build startup request; this page shows its live status. */
(() => {
  const box = document.getElementById('mac-access');
  if (!box) return;
  const byId = id => document.getElementById('mac-access-' + id);
  let timer = 0, busy = false, started = false, mic = {}, screen = {}, files = {};
  const api = () => window.api && window.api();
  const labels = {workspace:'Рабочая папка', documents:'Документы', desktop:'Рабочий стол', downloads:'Загрузки', selected:'Выбранная папка'};
  function render() {
    const supported = screen.request_supported !== false;
    const pending = !!(mic.pending || screen.pending || files.pending);
    byId('source').hidden = supported;
    byId('mic').textContent = mic.pending ? 'Ожидается ваш ответ в системном окне…' : mic.granted ? 'Доступ к микрофону разрешён.' : 'Микрофон не разрешён. Нажмите «Разрешить микрофон» или откройте настройки macOS.';
    byId('mic-allow').disabled = !supported || pending || !!mic.granted || !!mic.retry_blocked;
    byId('mic-allow').hidden = !!mic.granted;
    byId('screen').textContent = screen.error ? 'Не удалось проверить экран: ' + screen.error : screen.pending ? 'Ожидается системный запрос записи экрана…' : screen.granted ? 'Доступ к записи экрана разрешён.' : 'Доступ к экрану не подтверждён. Разрешите его в macOS; после изменения может понадобиться перезапуск Dewiar.';
    byId('screen-allow').disabled = !supported || pending || !!screen.granted;
    byId('screen-allow').hidden = !!screen.granted;
    byId('screen-allow').textContent = screen.requested && !screen.granted ? 'Открыть настройки экрана' : 'Разрешить запись экрана';
    byId('screen-recovery').hidden = !supported || !!screen.granted || !!screen.pending;
    const rows = [];
    for (const [key, value] of Object.entries(files.folders || {})) {
      const row = document.createElement('p'); row.className = 'hint';
      let state = files.pending === key ? 'Ожидается проверка / ответ macOS…' : value.state === 'authorized' ? 'Чтение разрешено' : value.state === 'unchecked' ? 'Ещё не проверено' : value.error || 'Не удалось проверить';
      row.textContent = `${labels[key] || key}: ${state}${value.path ? ' · ' + value.path : ''}`;
      rows.push(row);
    }
    byId('files').replaceChildren(...rows);
    box.querySelectorAll('[data-mac-folder]').forEach(button => button.disabled = !supported || pending);
  }
  async function refresh() {
    const a = api();
    if (!a?.files_access_state || !a?.screen_capture_permission || busy) return;
    busy = true; box.hidden = false;
    try {
      [mic, screen, files] = await Promise.all([a.microphone_permission(), a.screen_capture_permission(), a.files_access_state()]);
      render();
      clearTimeout(timer);
      if (mic.pending || screen.pending || files.pending) timer = setTimeout(refresh, 1000);
    } catch (error) { byId('result').textContent = 'Не удалось проверить доступы: ' + (error.message || error); }
    finally { busy = false; }
  }
  async function action(button, run) {
    if (button.disabled) return;
    button.disabled = true; byId('result').textContent = '';
    try { await run(api()); await refresh(); }
    catch (error) { byId('result').textContent = error.message || String(error); }
    finally { button.disabled = false; render(); }
  }
  byId('mic-allow').onclick = e => action(e.currentTarget, a => a.request_microphone_access());
  byId('screen-allow').onclick = e => action(e.currentTarget, a => screen.requested ? a.open_screen_settings() : a.request_screen_access());
  for (const [button, method] of [['mic-settings','open_microphone_settings'],['screen-settings','open_screen_settings'],['files-settings','open_files_settings']]) {
    byId(button).onclick = e => action(e.currentTarget, a => a[method]());
  }
  box.querySelectorAll('[data-mac-folder]').forEach(button => button.onclick = e => action(e.currentTarget, a => a.request_files_access(button.dataset.macFolder)));
  byId('refresh').onclick = refresh;
  byId('later').onclick = () => { byId('setup').hidden = true; };
  byId('startup').onchange = async e => {
    try {
      const result = await core.call('settings_save', {patch:{ui:{mac_access_at_startup:e.target.checked}}});
      if (typeof S !== 'undefined') S = result;
      byId('result').textContent = 'Настройка запуска сохранена.';
    } catch (error) { e.target.checked = !e.target.checked; byId('result').textContent = error.message || String(error); }
  };
  byId('screen-startup').onchange = async e => {
    try {
      const result = await core.call('settings_save', {patch:{ui:{mac_screen_request_at_startup:e.target.checked}}});
      if (typeof S !== 'undefined') S = result;
    } catch (error) { e.target.checked = !e.target.checked; byId('result').textContent = error.message || String(error); }
  };
  async function start(settings) {
    await refresh();
    if (!api()?.files_access_state) return;
    byId('startup').checked = !!settings?.ui?.mac_access_at_startup;
    byId('screen-startup').checked = settings?.ui?.mac_screen_request_at_startup !== false;
    if (!settings?.ui || started || new URLSearchParams(location.search).get('mac_access_setup') !== '1') return;
    started = true;
    document.querySelector('nav button[data-p="safety"]')?.click();
    byId('setup').hidden = false;
    box.scrollIntoView({block:'start'});
    try {
      const result = await core.call('settings_save', {patch:{ui:{mac_access_setup_seen:true}}});
      if (typeof S !== 'undefined') S = result;
    } catch (error) { byId('result').textContent = 'Не удалось сохранить показ настройки: ' + (error.message || error); }
  }
  core.on('hello', data => start(data.settings));
  window.addEventListener('pywebviewready', () => start(typeof S !== 'undefined' ? S : null));
  window.addEventListener('focus', refresh);
  document.querySelector('nav button[data-p="safety"]')?.addEventListener('click', refresh);
  setTimeout(() => start(typeof S !== 'undefined' ? S : null), 300);
})();
