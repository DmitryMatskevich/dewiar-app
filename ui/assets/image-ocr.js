/* Explicit local OCR; extracted text remains reviewable data until the user inserts it. */
(() => {
  let panel, serial = 0;
  const by = id => panel.querySelector('#' + id);
  function init() {
    if (panel) return;
    panel = document.createElement('dialog'); panel.className = 'image-ocr-panel';
    panel.setAttribute('aria-labelledby', 'image-ocr-title');
    panel.innerHTML = `<header><h2 id="image-ocr-title">Текст с картинки</h2><button type="button" id="image-ocr-close" title="Отменить" aria-label="Отменить">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>
      </button></header>
      <p id="image-ocr-status" role="status" aria-live="polite"></p>
      <label for="image-ocr-text">Распознанный текст — можно исправить перед вставкой</label>
      <textarea id="image-ocr-text" rows="10" spellcheck="false"></textarea>
      <p id="image-ocr-error" role="alert"></p>
      <footer>
        <button type="button" id="image-ocr-create-contact" hidden>Создать контакт</button>
        <button type="button" id="image-ocr-copy" title="Скопировать текст в буфер обмена" aria-label="Скопировать текст в буфер обмена" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3"/></svg>
        </button>
        <button type="button" id="image-ocr-insert" title="Вставить текст в черновик сообщения" aria-label="Вставить текст в черновик сообщения" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>
        </button>
      </footer>`;
    document.body.append(panel);
    by('image-ocr-close').onclick = () => panel.close();
    panel.addEventListener('close', () => { serial++; });
    by('image-ocr-text').oninput = () => controls(!by('image-ocr-text').value.trim());
    by('image-ocr-create-contact').onclick = async () => {
      if (window._imageOcrContactDraft) await window.DewiarContactCreate?.open(window._imageOcrContactDraft, {title:'Создать контакт из распознанных данных', source:'Проверьте контактные данные, при необходимости дополните имя и выберите сохранение в Контактах macOS.'});
    };
    by('image-ocr-copy').onclick = async () => {
      by('image-ocr-error').textContent = '';
      try {
        const text = by('image-ocr-text').value;
        const fallback = () => { by('image-ocr-text').focus(); by('image-ocr-text').select(); if (!document.execCommand('copy')) throw Error('Выделите текст и скопируйте его через Cmd+C или Ctrl+C.'); };
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text).catch(fallback);
        else fallback();
        by('image-ocr-status').textContent = 'Текст скопирован.';
      } catch (error) { by('image-ocr-error').textContent = error.message; }
    };
  }
  function controls(disabled) {
    by('image-ocr-copy').disabled = disabled; by('image-ocr-insert').disabled = disabled;
  }
  window.extractImageText = async (attachment, insert) => {
    init();
    const request = ++serial;
    by('image-ocr-title').textContent = 'Текст с картинки';
    by('image-ocr-create-contact').hidden = true; window._imageOcrContactDraft = null;
    by('image-ocr-text').previousElementSibling.textContent = 'Распознанный текст — можно исправить перед вставкой';
    by('image-ocr-text').value = ''; by('image-ocr-error').textContent = ''; controls(true);
    by('image-ocr-create-contact').hidden = true; window._imageOcrContactDraft = null;
    by('image-ocr-status').textContent = 'Распознаю текст локально…';
    by('image-ocr-text').disabled = true;
    by('image-ocr-insert').onclick = () => {
      const text = by('image-ocr-text').value;
      panel.close();
      insert(text);
    };
    if (!panel.open) panel.showModal();
    try {
      const result = await core.call('image_ocr', {image: attachment.ocrData || attachment.url || attachment.b64});
      if (serial !== request || !panel.open) return;
      if (result.status === 'error') throw Error(result.error || 'Локальный OCR недоступен.');
      if (result.status === 'empty' || !result.text?.trim()) {
        by('image-ocr-status').textContent = 'На картинке не найден читаемый текст.'; return;
      }
      by('image-ocr-text').value = result.text;
      by('image-ocr-status').textContent = `Текст извлечён${result.engine ? ' · ' + result.engine : ''}. Проверьте результат перед использованием.`;
      by('image-ocr-text').disabled = false; controls(false);
    } catch (error) {
      if (serial !== request || !panel.open) return;
      by('image-ocr-status').textContent = 'Не удалось извлечь текст.';
      by('image-ocr-error').textContent = error.message || String(error);
    }
  };
  window.extractImageContacts = async (attachment, insert) => {
    init();
    const request = ++serial;
    by('image-ocr-text').value = ''; by('image-ocr-error').textContent = ''; controls(true);
    by('image-ocr-text').disabled = true;
    by('image-ocr-title').textContent = 'Контакты с картинки';
    by('image-ocr-status').textContent = 'Ищу контактные данные локально…';
    by('image-ocr-text').previousElementSibling.textContent = 'Найденные контакты — проверьте перед использованием';
    by('image-ocr-insert').onclick = () => { const text = by('image-ocr-text').value; panel.close(); insert?.(text); };
    if (!panel.open) panel.showModal();
    try {
      const result = await core.call('image_contacts', {image: attachment.ocrData || attachment.url || attachment.b64});
      if (serial !== request || !panel.open) return;
      if (result.status === 'error') throw Error(result.error || 'Не удалось извлечь контакты.');
      const rows = [['Телефон', result.contacts?.phone], ['Электронная почта', result.contacts?.email], ['Сайт', result.contacts?.website]]
        .flatMap(([label, values]) => (values || []).map(value => `${label}: ${value}`));
      if (!rows.length) { by('image-ocr-status').textContent = result.error || 'Контакты не найдены.'; return; }
      by('image-ocr-text').value = rows.join('\n'); by('image-ocr-text').disabled = false; controls(false);
      window._imageOcrContactDraft = {phones:result.contacts?.phone||[], emails:result.contacts?.email||[], websites:result.contacts?.website||[]};
      by('image-ocr-create-contact').hidden = false;
      by('image-ocr-status').textContent = 'Контакты извлечены локально. Проверьте результат.';
    } catch (error) {
      if (serial !== request || !panel.open) return;
      by('image-ocr-status').textContent = 'Не удалось извлечь контакты.'; by('image-ocr-error').textContent = error.message || String(error);
    }
  };
})();
