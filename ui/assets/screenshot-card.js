/* Shared assistant-message card for HUD and Chat. OCR is always literal text. */
(() => {
  const icons = {
    pencil: '<path d="m4 16 12-12 4 4-12 12-5 1zM14 6l4 4"/>',
    image: '<rect x="3" y="3" width="14" height="14" rx="2"/><path d="m4 14 4-4 3 3 3-5 3 4M8 21h11a2 2 0 0 0 2-2V8"/>',
    text: '<path d="M4 5h12M10 5v14M6 19h8M18 9h3M18 13h3M18 17h3"/>',
    note: '<path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>'
  };
  async function editText(value, options, anchor) {
    // Reload the persisted version: another Chat/HUD window may have edited it.
    const current = await core.call('screenshot_text', {id:value.id});
    let saved = current.text, revision = current.revision, pending = null, timer, closing = false;
    const dialog = document.createElement('dialog'); dialog.className = 'ss-dialog ss-text-dialog';
    dialog.setAttribute('aria-label', 'Редактирование текста снимка');
    const header = document.createElement('div'); header.className = 'ss-dialog-bar';
    const title = document.createElement('h2'); title.textContent = 'Текст снимка';
    const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Закрыть';
    header.append(title, close);
    const input = document.createElement('textarea'); input.className = 'ss-text-input'; input.value = saved;
    input.setAttribute('aria-label', 'Текст снимка'); input.spellcheck = true;
    const hint = document.createElement('p'); hint.className = 'ss-info'; hint.textContent = 'Изменения сохраняются в снимке автоматически.';
    const status = document.createElement('p'); status.className = 'ss-info'; status.setAttribute('role', 'status');
    const actions = document.createElement('div'); actions.className = 'ss-region-actions';
    const copy = document.createElement('button'); copy.type = 'button'; copy.textContent = 'Скопировать';
    const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Сохранить'; save.className = 'ss-primary';
    actions.append(copy, save); dialog.append(header, hint, input, status, actions);
    const refresh = () => { save.disabled = !!pending || input.value === saved; copy.disabled = !input.value; };
    async function flush() {
      clearTimeout(timer);
      if (pending) return pending;
      pending = (async () => {
        while (input.value !== saved) {
          const text = input.value;
          status.textContent = 'Сохраняю…';
          try {
            const result = await core.call('screenshot_text_save', {id:value.id, text, revision});
            saved = result.text; revision = result.revision;
            value.text = saved; value.text_revision = revision;
            anchor.hidden = !saved.trim();
            status.textContent = 'Сохранено';
          } catch (error) { status.textContent = 'Не удалось сохранить: ' + (error.message || String(error)); return false; }
        }
        return true;
      })();
      refresh();
      const ok = await pending;
      pending = null; refresh();
      return ok;
    }
    input.oninput = () => {
      clearTimeout(timer); status.textContent = 'Есть несохранённые изменения'; refresh();
      timer = setTimeout(flush, 500);
    };
    save.onclick = flush;
    copy.onclick = async () => {
      copy.disabled = true;
      try {
        const text = input.value;
        if (options.copyText) await options.copyText(text, dialog);
        else await navigator.clipboard.writeText(text);
        if (await flush()) status.textContent = 'Текст скопирован · Сохранено';
      } catch (error) { status.textContent = 'Не удалось скопировать: ' + (error.message || String(error)); }
      finally { refresh(); }
    };
    close.onclick = async () => {
      if (closing) return;
      closing = true; input.readOnly = true; close.disabled = true;
      if (await flush()) dialog.close();
      else { closing = false; input.readOnly = false; close.disabled = false; input.focus(); }
    };
    dialog.addEventListener('cancel', event => { event.preventDefault(); close.click(); });
    dialog.addEventListener('close', () => {
      clearTimeout(timer); dialog.remove();
      const card = document.querySelector(`[data-screenshot-id="${CSS.escape(value.id)}"]`);
      (anchor.isConnected ? anchor : card?.querySelector('[data-ss-action="text"]:not([hidden]), [data-ss-open]'))?.focus();
    }, {once:true});
    document.body.append(dialog); refresh(); dialog.showModal(); input.focus();
  }
  function card(value, options = {}) {
    const root = document.createElement('article'); root.className = 'ss-card'; root.dataset.screenshotId = value.id;
    root.setAttribute('aria-label', 'Сообщение ассистента: снимок экрана');
    const head = document.createElement('div'); head.className = 'ss-head';
    const label = document.createElement('b'); label.textContent = 'Ассистент · Снимок экрана'; head.append(label);
    const image = document.createElement('img'); image.className = 'ss-image'; image.src = value.url; image.alt = value.title;
    const open = document.createElement('button'); open.type = 'button'; open.className = 'ss-open'; open.setAttribute('aria-label', 'Открыть исходный снимок экрана'); open.title = 'Открыть снимок экрана'; open.dataset.ssOpen = value.id;
    open.append(image);
    open.onclick = async () => { open.disabled = true; try { await DewiarCapture.openImage(value); } catch (e) { status.textContent = e.message || String(e); } finally { open.disabled = false; } };
    const status = document.createElement('div'); status.className = 'ss-status'; status.setAttribute('role', 'status');
    status.textContent = value.note_path ? 'В заметках' : value.status === 'error' ? 'Распознать текст не удалось; изображение сохранено.' : '';
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
      if(value.markup){await core.call('visual_copy',{asset_id:value.id});status.textContent='Скопировано с пометками';return;}
      const url = await core.call('screenshot_image', {id:value.id});
      const native = window.api?.();
      if (native?.copy_image_data) { if (!await native.copy_image_data(url)) throw Error('Не удалось скопировать картинку'); }
      else if (navigator.clipboard?.write && window.ClipboardItem) {
        const blob = await (await fetch(url)).blob();
        await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
      } else throw Error('Копирование картинки доступно в приложении Dewiar или браузере с поддержкой буфера изображений');
      status.textContent = 'Картинка скопирована';
    });
    button('pencil', 'Разметить снимок', async () => {
      const native=window.api?.();
      if(native?.open_visual_editor) await native.open_visual_editor('screenshot',value.id);
      else { const target=new URL('visual-editor.html',location.href); target.search=location.search; target.searchParams.set('id',value.id);window.open(target.href,'visual-'+value.id); }
    });
    root.visualUpdate = data => { if(data.id===value.id){value.markup=data.markup;image.src=data.markup.thumb||value.url;status.textContent=data.markup.revision===data.markup.export_revision?'С пометками':'Пометки сохранены · PNG подготовьте в редакторе';} };
    if(value.markup){image.src=value.markup.thumb||value.url;status.textContent='С пометками';}
    const textButton = button('text' , 'Открыть и редактировать текст снимка', () => editText(value, options, textButton));
    textButton.setAttribute('aria-haspopup', 'dialog');
    textButton.hidden = !value.text?.trim();
    textButton.disabled = !!options.readOnly || textButton.hidden;
    const save = button('note', 'Перенести картинку и текст в заметки', async () => {
      const result = value.markup ? await core.call('visual_to_note',{asset_id:value.id}) : await core.call('screenshot_to_note', {id:value.id});
      value.note_path = result.path; status.textContent = value.markup ? 'Картинка с пометками перенесена в заметки' : 'Картинка и текст перенесены в заметки';
    });
    save.disabled = !!options.readOnly || !!value.note_path;
    actions.append(actions.querySelector('[data-ss-action=pencil]'));
    const close = button('close', 'Убрать сообщение со снимком', async () => {
      await core.call('screenshot_dismiss', {id:value.id}); root.remove(); options.removed?.(value.id);
    });
    head.append(close); // close belongs at the message's top-right, not in the copy toolbar
    const preview = document.createElement('div'); preview.className = 'ss-preview'; preview.append(open, actions);
    root.append(head, preview);
    root.append(status);
    return root;
  }
  function fromMessage(item) {
    const attachment = item.att?.[0];
    return attachment?.capture ? {...attachment.capture, id:item.id, title:attachment.name,
      image:attachment.path, url:attachment.thumb, note_path:attachment.note_path || ''} : null;
  }
  core.on('visual_changed', data => document.querySelectorAll('[data-screenshot-id]').forEach(node=>node.visualUpdate?.(data)));
  window.DewiarScreenshots = {card, fromMessage};
})();
