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
      <label for="tr-voice">Голос озвучки</label><div style="display:flex;gap:8px;align-items:center"><select id="tr-voice" style="flex:1" aria-describedby="tr-voice-hint"></select><button type="button" id="tr-voice-test" title="Прослушать короткий пример выбранного голоса">▶ Прослушать</button></div>
      <p id="tr-voice-hint" class="mt-hint">Голос будет сохранён в проекте OpenMontage. Перед запуском можно прослушать короткий фрагмент.</p>
      <label for="tr-sample">Текст тестового фрагмента</label><input id="tr-sample" value="Это короткий пример выбранного голоса для озвучки видео." type="text">
      <label for="tr-links">Ссылки или полные пути — по одному на строку</label><textarea id="tr-links" rows="4" placeholder="https://www.youtube.com/watch?v=…"></textarea>
      <button type="button" id="tr-pick" title="Выбрать локальные видеофайлы без повторной загрузки через чат.">Выбрать файлы…</button><ul id="tr-files"></ul>
      <p id="tr-count" role="status" aria-live="polite"></p><p class="mt-hint">Результаты: перевод.md, целевые субтитры .srt, dubbing-script.md и openmontage.json с чек-листом QA.</p><p id="tr-error" role="alert"></p>
      <footer><button type="button" id="tr-start">Подготовить перевод</button></footer></form>`;
    document.body.append(panel); by('tr-links').oninput = render; by('tr-sample').oninput = () => { by('tr-sample').dataset.edited = '1'; };
    async function loadVoices() { try { const target = by('tr-target').value; const sample = by('tr-sample'); if (!sample.dataset.edited) sample.value = target === 'en' ? 'This is a short sample of the selected voice for the video.' : 'Это короткий пример выбранного голоса для озвучки видео.'; const data = await core.call('translation_voices', {target_language: target}); const select = by('tr-voice'); select.replaceChildren(); (data.options || []).forEach(v => { const option = document.createElement('option'); option.value = v.id; option.textContent = `${v.label} — ${v.engine} / ${v.model}` + (v.installed ? ' · готов' : ' · потребуется загрузка'); option.title = v.about || ''; option.selected = v.id === data.default; select.append(option); }); by('tr-voice-hint').textContent = 'Профиль — это имя и роль голоса. Реальная модель указана после тире; её можно скачать при первом прослушивании.'; } catch (e) { by('tr-error').textContent = e.message; } }
    by('tr-target').onchange = loadVoices;
    by('tr-voice-test').onclick = async () => { const voice = by('tr-voice').value; if (!voice) return; by('tr-voice-test').disabled = true; by('tr-voice-test').textContent = 'Готовлю…'; by('tr-error').textContent = ''; try { await core.call('translation_voice_preview', {voice, text: by('tr-sample').value || 'Это короткий пример выбранного голоса для озвучки видео.'}); } catch (e) { by('tr-error').textContent = e.message; } finally { by('tr-voice-test').disabled = false; by('tr-voice-test').textContent = '▶ Прослушать'; } };
    by('tr-pick').onclick = async () => { try { const api = window.api?.(); if (!api?.pick_media_files) throw Error('Выбор файлов доступен в приложении Dewiar. Вставьте ссылку или полный путь.'); paths = [...new Set([...paths, ...(await api.pick_media_files() || [])])]; render(); } catch (e) { by('tr-error').textContent = e.message; } };
    by('tr-start').onclick = async () => { if (sending) return; sending = true; render(); by('tr-error').textContent = ''; try { const task = await core.call('media_translate', {sources:sources(), source_language:by('tr-source').value, target_language:by('tr-target').value, voice:by('tr-voice').value}); document.dispatchEvent(new CustomEvent('media-task-started', {detail:task})); panel.close(); const api = window.api?.(); if (api?.open_settings) await api.open_settings('tasks','&task=' + encodeURIComponent(task.id)); else if (typeof openTask === 'function') { document.querySelector('nav button[data-p="tasks"]')?.click(); openTask(task.id); } } catch (e) { by('tr-error').textContent = e.message; } finally { sending = false; render(); } };
    loadVoices();
  }
  window.openTranslation = () => { init(); by('tr-error').textContent = ''; render(); panel.showModal(); by('tr-links').focus(); };
  document.querySelectorAll('[data-translate-open]').forEach(button => { button.title = title; button.setAttribute('aria-label', 'Перевести видео для OpenMontage'); button.onclick = window.openTranslation; });
})();
