/* Shared assistant-message card for HUD and Chat. OCR is always literal text. */
(() => {
  const icons = {
    image: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="m4 14 4-4 3 3 3-5 3 4M8 21h11a2 2 0 0 0 2-2V8"/>',
    text: '<path d="M4 5h12M10 5v14M6 19h8M18 9h3M18 13h3M18 17h3"/>',
    note: '<path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>'
  };
  function card(value, options = {}) {
    const root = document.createElement('article'); root.className = 'ss-card'; root.dataset.screenshotId = value.id;
    root.setAttribute('aria-label', 'Сообщение ассистента: снимок экрана');
    const head = document.createElement('div'); head.className = 'ss-head';
    const label = document.createElement('b'); label.textContent = 'Ассистент · Снимок экрана'; head.append(label);
    const image = document.createElement('img'); image.className = 'ss-image'; image.src = value.url; image.alt = value.title;
    const status = document.createElement('div'); status.className = 'ss-status'; status.setAttribute('role', 'status');
    status.textContent = value.note_path ? 'В заметках' : value.summary;
    const actions = document.createElement('div'); actions.className = 'ss-actions';
    const button = (kind, title, action) => {
      const b = document.createElement('button'); b.type = 'button'; b.title = title; b.setAttribute('aria-label', title); b.dataset.ssAction = kind;
      b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[kind]}</svg>`;
      b.onclick = async event => {
        event.stopPropagation(); if (b.disabled) return; b.disabled = true;
        try { await action(); }
        catch (error) { status.textContent = error.message || String(error); }
        finally { b.disabled = !!options.readOnly || (kind === 'text' && !value.text) || (kind === 'note' && !!value.note_path); }
      };
      b.disabled = !!options.readOnly; actions.append(b); return b;
    };
    button('image', 'Скопировать картинку в буфер', async () => {
      const url = await core.call('screenshot_image', {id:value.id});
      const native = window.api?.();
      if (native?.copy_image_data) { if (!await native.copy_image_data(url)) throw Error('Не удалось скопировать картинку'); }
      else if (navigator.clipboard?.write && window.ClipboardItem) {
        const blob = await (await fetch(url)).blob();
        await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
      } else throw Error('Копирование картинки доступно в приложении Dewiar или браузере с поддержкой буфера изображений');
      status.textContent = 'Картинка скопирована';
    });
    const copyText = button('text', 'Скопировать распознанный текст', async () => {
      if (options.copyText) await options.copyText(value.text);
      else await navigator.clipboard.writeText(value.text);
      status.textContent = 'Распознанный текст скопирован';
    });
    copyText.disabled = !!options.readOnly || !value.text;
    const save = button('note', 'Перенести картинку и текст в заметки', async () => {
      const result = await core.call('screenshot_to_note', {id:value.id});
      value.note_path = result.path; status.textContent = 'Картинка и текст перенесены в заметки';
    });
    save.disabled = !!options.readOnly || !!value.note_path;
    const close = button('close', 'Убрать сообщение со снимком', async () => {
      await core.call('screenshot_dismiss', {id:value.id}); root.remove(); options.removed?.(value.id);
    });
    head.append(close); // close belongs at the message's top-right, not in the copy toolbar
    const preview = document.createElement('div'); preview.className = 'ss-preview'; preview.append(image, actions);
    root.append(head, preview);
    if (value.text) {
      const details = document.createElement('details'), summary = document.createElement('summary'), text = document.createElement('pre');
      summary.textContent = 'Распознанный текст'; text.textContent = value.text;
      details.append(summary, text); root.append(details);
    }
    root.append(status);
    return root;
  }
  function fromMessage(item) {
    const attachment = item.att?.[0];
    return attachment?.capture ? {...attachment.capture, id:item.id, title:attachment.name,
      image:attachment.path, url:attachment.thumb, note_path:attachment.note_path || ''} : null;
  }
  window.DewiarScreenshots = {card, fromMessage};
})();
