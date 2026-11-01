/* One reviewable batch launch, shared by the HUD and Studio. No model prompt required. */
(() => {
  const title = 'Получить текст речи из видео или аудио: один источник или пакет файлов и ссылок. Сначала используются субтитры, иначе скачивается звук и запускается GigaAM для русского языка. Уже скачанные источники и готовые результаты используются повторно. Затем создаётся чистовой Markdown без номеров и таймкодов, с очисткой речи и отдельным списком сомнительных мест для сверки с аудио. Исходные TXT/SRT сохраняются. Ход работы доступен в задаче.';
  let panel, paths = [], uploads = [], sending = false, importing = false;
  const by = id => panel.querySelector('#' + id);
  function identity(source) {
    try {
      const u = new URL(source), host = u.hostname.toLowerCase();
      if (host === 'youtu.be' || host === 'youtube.com' || host.endsWith('.youtube.com'))
        return 'youtube:' + (host === 'youtu.be' ? u.pathname.slice(1) : u.searchParams.get('v') || u.pathname.split('/').filter(Boolean).pop());
      u.hash = ''; return u.href;
    } catch (_) { return source; }
  }
  function sources() {
    const found = new Set();
    return [...paths, ...by('mt-links').value.split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('#'))]
      .filter(s => { const key = identity(s); if (found.has(key)) return false; found.add(key); return true; });
  }
  function render() {
    const list = by('mt-files'); list.replaceChildren();
    [...paths.map(path => ({name:path, path})), ...uploads.map(file => ({name:file.name, file}))].forEach(item => {
      const row = document.createElement('li'), label = document.createElement('span'), remove = document.createElement('button');
      label.textContent = item.name; label.title = item.name;
      remove.type = 'button'; remove.textContent = 'Убрать'; remove.title = 'Убрать этот источник из очереди обработки';
      remove.onclick = () => { if (item.path) paths = paths.filter(p => p !== item.path); else uploads = uploads.filter(f => f !== item.file); render(); };
      row.append(label, remove); list.append(row);
    });
    const n = sources().length + uploads.length;
    by('mt-count').textContent = n > 1 ? `Пакетная обработка: ${n} источников, последовательно. Ошибка одного не остановит остальные.` : n ? 'Один источник. Будут сохранены исходные TXT/SRT, чистовой Markdown и общее резюме.' : 'Добавьте ссылки или выберите файлы.';
    by('mt-start').textContent = n > 1 ? `Запустить пакет (${n})` : 'Транскрибировать';
    by('mt-start').disabled = sending || importing || !n;
  }
  function appendSources(text) {
    const input = by('mt-links');
    const incoming = String(text || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    if (!incoming.length) throw Error('В буфере нет текста. Скопируйте ссылку или полный путь к файлу.');
    const seen = new Set(sources().map(identity));
    const fresh = incoming.filter(s => { const key = identity(s); if (seen.has(key)) return false; seen.add(key); return true; });
    if (!fresh.length) { by('mt-import-status').textContent = 'Эти источники уже добавлены.'; return; }
    input.value = [input.value.trimEnd(), ...fresh].filter(Boolean).join('\n');
    render(); input.focus(); input.setSelectionRange(input.value.length, input.value.length);
    by('mt-import-status').textContent = `Добавлено источников: ${fresh.length}.`;
  }
  function importState(busy) {
    importing = busy;
    by('mt-paste').disabled = busy;
    by('mt-refresh-browsers').disabled = busy;
    by('mt-browser').disabled = busy || !by('mt-browser').value;
    by('mt-from-browser').disabled = busy || !by('mt-browser').value;
    by('mt-start').disabled = busy || sending || !(sources().length + uploads.length);
  }
  async function importSource(button, action) {
    if (importing) return;
    const label = button.textContent;
    by('mt-error').textContent = ''; by('mt-import-status').textContent = '';
    importState(true); button.textContent = 'Получаю…';
    try { appendSources(await action()); }
    catch (error) { by('mt-error').textContent = error.message || String(error); }
    finally { button.textContent = label; importState(false); }
  }
  async function loadBrowsers() {
    const select = by('mt-browser'), previous = select.value;
    select.replaceChildren(new Option('Загрузка…', ''));
    importState(importing);
    try {
      const browsers = await window.api?.()?.transcription_browsers?.() || [];
      select.replaceChildren();
      if (!browsers.length) select.append(new Option('Нет открытых браузеров', ''));
      for (const browser of browsers) {
        const option = document.createElement('option'); option.value = browser.id; option.textContent = browser.name; select.append(option);
      }
      if (browsers.some(b => b.id === previous)) select.value = previous;
      by('mt-browser-hint').textContent = browsers.length ? 'Ссылка из активной вкладки переднего окна выбранного браузера.' : 'Откройте Safari, Chrome, Яндекс Браузер или другой поддерживаемый браузер на macOS и нажмите «Обновить». Также можно вставить ссылку из буфера.';
    } catch (_) {
      select.replaceChildren(new Option('Браузеры недоступны', ''));
      by('mt-browser-hint').textContent = 'Не удалось получить список браузеров. Вставьте ссылку из буфера.';
    }
    importState(importing);
  }
  function init() {
    if (panel) return;
    panel = document.createElement('dialog'); panel.className = 'media-panel'; panel.id = 'media-panel'; panel.setAttribute('aria-labelledby','mt-title');
    panel.innerHTML = `<form method="dialog"><header><h2 id="mt-title">Транскрибировать</h2><button id="mt-close" value="cancel" title="Закрыть окно. Начатая фоновая обработка продолжится.">Закрыть</button></header>
      <p>Получить текст речи, а не только скачать ролик.</p>
      <label for="mt-links">Ссылки или полные пути — по одному на строку</label><textarea id="mt-links" rows="4" placeholder="https://www.youtube.com/watch?v=…" title="Вставьте одну или несколько ссылок на видео или пути к ранее сохранённым аудио/видеофайлам. Повторы будут исключены."></textarea>
      <div class="mt-source-actions"><button type="button" id="mt-paste">Вставить из буфера</button><button type="button" id="mt-pick" title="Выбрать сразу несколько локальных видео- и аудиофайлов. Они будут прочитаны с диска без повторной загрузки и без ограничения размера вложений чата.">Выбрать файлы…</button></div>
      <label for="mt-browser">Из браузера</label><div class="mt-browser-actions"><select id="mt-browser" aria-describedby="mt-browser-hint" disabled></select><button type="button" id="mt-from-browser" disabled>Взять ссылку</button><button type="button" id="mt-refresh-browsers">Обновить</button></div>
      <p id="mt-browser-hint" class="mt-hint"></p><p id="mt-import-status" role="status" aria-live="polite"></p><ul id="mt-files"></ul>
      <p id="mt-count" role="status" aria-live="polite"></p><p class="mt-hint">Готовый текст и скачанный звук используются повторно. После распознавания текст соединяется в связные фразы и очищается по правилам. Затем модель готовит общее резюме: о чём ролик, кто выступает, основные идеи и вывод. Сомнительные места сохраняются для сверки по аудио. Исходник остаётся.</p>
      <p id="mt-error" role="alert"></p><footer><button type="button" id="mt-start" title="Запустить всю очередь как одну фоновую задачу: субтитры → при их отсутствии звук → распознавание → исходные TXT/SRT → чистовой Markdown → общее резюме → отчёт очистки и места для сверки по аудио. Повторное нажатие не создаёт копию уже работающей или завершённой задачи.">Транскрибировать</button></footer></form>`;
    document.body.append(panel);
    by('mt-links').oninput = render;
    by('mt-paste').onclick = () => importSource(by('mt-paste'), async () => {
      const api = window.api?.();
      if (api?.clipboard_text) return await api.clipboard_text();
      try { return await navigator.clipboard.readText(); }
      catch (_) { throw Error('Не удалось прочитать буфер. Нажмите на поле и используйте Cmd+V или Ctrl+V.'); }
    });
    by('mt-from-browser').onclick = () => importSource(by('mt-from-browser'), async () => {
      const result = await window.api?.()?.transcription_browser_url?.(by('mt-browser').value);
      if (result?.error) throw Error(result.error);
      if (!result?.url) throw Error('Не удалось получить ссылку. Скопируйте её из адресной строки браузера.');
      return result.url;
    });
    by('mt-refresh-browsers').onclick = loadBrowsers;
    by('mt-pick').onclick = async () => {
      by('mt-error').textContent = '';
      try {
        const api = window.api?.();
        if (!api?.pick_media_files) throw Error('Выбор файлов доступен в приложении Dewiar. Здесь можно вставить ссылку или полный путь.');
        const selected = await api.pick_media_files(); paths = [...new Set([...paths, ...(selected || [])])]; render();
      } catch (error) { by('mt-error').textContent = error.message; }
    };
    by('mt-start').onclick = async () => {
      if (sending || importing) return;
      sending = true; render(); by('mt-error').textContent = '';
      try {
        const task = await core.call('media_transcribe', {sources:sources(), files:uploads.map(f => ({name:f.name,b64:f.b64})), language:'ru'});
        document.dispatchEvent(new CustomEvent('media-task-started',{detail:task}));
        window.consumeTranscriptionAttachments?.(uploads);
        panel.close();
        const api = window.api?.();
        if (api?.open_settings) await api.open_settings('tasks','&task=' + encodeURIComponent(task.id));
        else if (typeof openTask === 'function') { document.querySelector('nav button[data-p="tasks"]')?.click(); openTask(task.id); }
      } catch (error) { by('mt-error').textContent = error.message; }
      finally { sending = false; render(); }
    };
  }
  window.openTranscription = () => {
    init();
    uploads = window.getTranscriptionAttachments?.() || [];
    by('mt-error').textContent = '';
    by('mt-import-status').textContent = '';
    render(); panel.showModal(); by('mt-links').focus(); loadBrowsers();
  };
  document.querySelectorAll('[data-transcribe-open]').forEach(button => {
    button.title = title; button.setAttribute('aria-label','Транскрибировать видео или аудио, в том числе пакет источников');
    button.onclick = window.openTranscription;
  });
})();
