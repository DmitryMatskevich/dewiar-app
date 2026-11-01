/* Prepare a translation package for OpenMontage; dubbing/render/publication stay explicit later stages. */
(() => {
  const title = 'Перевести видео для OpenMontage: выбрать язык оригинала и целевой язык, получить литературный перевод, целевые субтитры и сценарий озвучки. Исходный текст сохраняется, дубляж и рендер не запускаются автоматически.';
  let panel, paths = [], sending = false;
  const by = id => panel.querySelector('#' + id);
  function sources() {
    const found = new Set();
    return [...paths, ...by('tr-links').value.split(/\r?\n/).map(s => s.trim()).filter(Boolean)]
      .filter(s => { if (found.has(s)) return false; found.add(s); return true; });
  }
  function render() {
    const list = by('tr-files'); list.replaceChildren();
    paths.forEach(path => {
      const row = document.createElement('li'), label = document.createElement('span'), remove = document.createElement('button');
      label.textContent = path; label.title = path; remove.type = 'button'; remove.textContent = 'Убрать'; remove.onclick = () => { paths = paths.filter(p => p !== path); render(); };
      row.append(label, remove); list.append(row);
    });
    const n = sources().length;
    by('tr-count').textContent = n > 1 ? `Пакетный перевод: ${n} источников.` : n ? 'Один источник.' : 'Добавьте ссылку или выберите файл.';
    by('tr-start').textContent = n > 1 ? `Запустить пакет (${n})` : 'Подготовить перевод';
    by('tr-start').disabled = sending || !n;
  }
  function init() {
    if (panel) return;
    panel = document.createElement('dialog'); panel.className = 'media-panel'; panel.id = 'translation-panel';
    panel.innerHTML = `<form method="dialog"><header><h2>Перевести видео</h2><button id="tr-close" value="cancel">Закрыть</button></header>
      <p>Подготовлю материалы для OpenMontage. Исходник не изменяется; дубляж, монтаж и публикация остаются отдельными этапами.</p>
      <label for="tr-source">Язык оригинала</label><select id="tr-source"><option value="auto">Определить автоматически</option><option value="ru">Русский</option><option value="en">Английский</option></select>
      <label for="tr-target">Перевести на</label><select id="tr-target"><option value="ru">Русский</option><option value="en">Английский</option></select>
      <label for="tr-links">Ссылки или полные пути — по одному на строку</label><textarea id="tr-links" rows="4" placeholder="https://www.youtube.com/watch?v=…"></textarea>
      <button type="button" id="tr-pick" title="Выбрать локальные видеофайлы без повторной загрузки через чат.">Выбрать файлы…</button><ul id="tr-files"></ul>
      <p id="tr-count" role="status" aria-live="polite"></p><p class="mt-hint">Результаты: перевод.md, целевые субтитры .srt, dubbing-script.md и openmontage.json с чек-листом QA.</p><p id="tr-error" role="alert"></p>
      <footer><button type="button" id="tr-start">Подготовить перевод</button></footer></form>`;
    document.body.append(panel); by('tr-links').oninput = render;
    by('tr-pick').onclick = async () => { try { const api = window.api?.(); if (!api?.pick_media_files) throw Error('Выбор файлов доступен в приложении Dewiar. Вставьте ссылку или полный путь.'); paths = [...new Set([...paths, ...(await api.pick_media_files() || [])])]; render(); } catch (e) { by('tr-error').textContent = e.message; } };
    by('tr-start').onclick = async () => { if (sending) return; sending = true; render(); by('tr-error').textContent = ''; try { const task = await core.call('media_translate', {sources:sources(), source_language:by('tr-source').value, target_language:by('tr-target').value}); document.dispatchEvent(new CustomEvent('media-task-started', {detail:task})); panel.close(); const api = window.api?.(); if (api?.open_settings) await api.open_settings('tasks','&task=' + encodeURIComponent(task.id)); else if (typeof openTask === 'function') { document.querySelector('nav button[data-p="tasks"]')?.click(); openTask(task.id); } } catch (e) { by('tr-error').textContent = e.message; } finally { sending = false; render(); } };
  }
  window.openTranslation = () => { init(); by('tr-error').textContent = ''; render(); panel.showModal(); by('tr-links').focus(); };
  document.querySelectorAll('[data-translate-open]').forEach(button => { button.title = title; button.setAttribute('aria-label', 'Перевести видео для OpenMontage'); button.onclick = window.openTranslation; });
})();
