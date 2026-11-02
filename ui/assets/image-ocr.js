/* Explicit local OCR; extracted text remains reviewable data until the user inserts it. */
(() => {
  let panel, serial = 0;
  const by = id => panel.querySelector('#' + id);
  function init() {
    if (panel) return;
    panel = document.createElement('dialog'); panel.className = 'image-ocr-panel';
    panel.setAttribute('aria-labelledby', 'image-ocr-title');
    panel.innerHTML = `<header><h2 id="image-ocr-title">Текст с картинки</h2><button type="button" id="image-ocr-close">Закрыть</button></header>
      <p id="image-ocr-status" role="status" aria-live="polite"></p>
      <label for="image-ocr-text">Распознанный текст — можно исправить перед вставкой</label>
      <textarea id="image-ocr-text" rows="10" spellcheck="false"></textarea>
      <p id="image-ocr-error" role="alert"></p>
      <footer><button type="button" id="image-ocr-copy" disabled>Скопировать</button><button type="button" id="image-ocr-insert" disabled>Вставить в сообщение</button></footer>`;
    document.body.append(panel);
    by('image-ocr-close').onclick = () => panel.close();
    panel.addEventListener('close', () => { serial++; });
    by('image-ocr-text').oninput = () => controls(!by('image-ocr-text').value.trim());
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
    by('image-ocr-text').value = ''; by('image-ocr-error').textContent = ''; controls(true);
    by('image-ocr-status').textContent = 'Распознаю текст локально…';
    by('image-ocr-text').disabled = true;
    by('image-ocr-insert').onclick = () => { insert(by('image-ocr-text').value); panel.close(); };
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
})();
