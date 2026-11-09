/* Create a reviewed contact from extracted data or a vCard using the shared dialog shell. */
(() => {
  const esc = value => String(value || '').trim();
  const rows = value => Array.isArray(value) ? value : value ? [value] : [];
  const input = (label, key, value = '', multiline = false) => {
    const wrap = document.createElement('label'); wrap.className = 'f'; wrap.textContent = label;
    const field = document.createElement(multiline ? 'textarea' : 'input'); field.dataset.contactField = key;
    field.value = Array.isArray(value) ? value.join('\n') : String(value || '');
    if (!multiline) field.maxLength = 300;
    wrap.append(field); return wrap;
  };
  function open(draft = {}, opts = {}) {
    if (!window.DewiarDialogs || !window.ask || !window.core?.call) throw Error('Функции создания контакта пока недоступны.');
    const content = document.createElement('div');
    const note = document.createElement('p'); note.textContent = opts.source || 'Проверьте данные. Dewiar сохранит карточку только после вашего подтверждения.'; content.append(note);
    const grid = document.createElement('div'); grid.className = 'row';
    for (const [label, key] of [['Фамилия','family_name'],['Имя','given_name'],['Отчество','middle_name'],['Организация','organization']]) grid.append(input(label, key, draft[key]));
    grid.append(input('Телефоны, по одному в строке', 'phones', rows(draft.phones), true));
    grid.append(input('Электронная почта, по одному адресу в строке', 'emails', rows(draft.emails), true));
    grid.append(input('Сайты, по одному в строке', 'websites', draft.websites || (draft.website ? [draft.website] : []), true));
    const birthday = input('Дата рождения (необязательно)', 'birthday');
    const birthdayField = birthday.querySelector('input'); birthdayField.type = 'date';
    if (draft.birthday) birthdayField.value = `${draft.birthday.year || 2000}-${String(draft.birthday.month).padStart(2,'0')}-${String(draft.birthday.day).padStart(2,'0')}`;
    grid.append(birthday);
    grid.append(input('Telegram', 'telegram', draft.telegram));
    grid.append(input('WhatsApp', 'whatsapp', draft.whatsapp)); content.append(grid);
    const destinations = document.createElement('div'); destinations.className = 'row';
    const localLabel = document.createElement('p'); localLabel.className = 'hint';
    localLabel.textContent = 'Новая карточка будет сохранена в адресной книге Dewiar.';
    const macLabel = document.createElement('label'); macLabel.className = 'f';
    const mac = document.createElement('input'); mac.type = 'checkbox'; mac.checked = !!window.api?.()?.contacts_permission;
    macLabel.append(mac, document.createTextNode(' Сохранить в Контактах macOS'));
    destinations.append(localLabel, macLabel); content.append(destinations);
    const view = window.DewiarDialogs.mount({title: opts.title || 'Создать контакт', content, cancelValue: false});
    view.addAction('Отмена', () => view.close(false));
    view.addAction('Проверить и создать', async button => {}, 'dw-primary');
    const createButton = view.actions.lastElementChild;
    createButton.onclick = async () => {
      const get = key => view.panel.querySelector(`[data-contact-field="${key}"]`).value.trim();
      const contact = {};
      for (const key of ['given_name','family_name','middle_name','organization','telegram','whatsapp']) contact[key] = get(key);
      for (const key of ['phones','emails','websites']) contact[key] = get(key).split(/[\n;,]+/).map(esc).filter(Boolean);
      if (get('birthday')) { const [year,month,day] = get('birthday').split('-').map(Number); contact.birthday = {year,month,day}; }
      if (!contact.given_name && !contact.family_name && !contact.organization) { view.status.textContent = 'Укажите имя, фамилию или организацию.'; return; }
      const toMac = mac.checked;
      if (!await window.ask(`Создать контакт «${[contact.family_name,contact.given_name,contact.middle_name].filter(Boolean).join(' ') || contact.organization}» ${toMac ? 'в Dewiar и Контактах macOS' : 'в Контактах Dewiar'}?\n${[...contact.phones,...contact.emails,...contact.websites].filter(Boolean).join('\n')}`, {title:'Подтвердите создание контакта', ok:'Создать'})) return;
      createButton.disabled = true;
      try {
        const result = await core.call('contacts_create', {contact, to_mac:toMac, request_id:globalThis.crypto?.randomUUID?.() || `contact_${Date.now()}_${Math.random().toString(36).slice(2)}`, confirmed:true});
        view.close(true);
        await window.messagePopup?.(result.already_exists ? 'Такая карточка уже есть в Dewiar; дубликат не создавался.' : result.saved_mac ? 'Контакт сохранён в Dewiar и Контактах macOS.' : 'Контакт сохранён в Dewiar.', {title:'Контакт создан'});
      } catch (error) { view.status.textContent = error.message || String(error); createButton.disabled = false; }
    };
    view.show(); return view.promise;
  }
  function parseVCard(text) {
    const unfolded = String(text).replace(/\r?\n[ \t]/g, '');
    const cards = unfolded.split(/BEGIN:VCARD/i).slice(1).map(part => part.split(/END:VCARD/i)[0]);
    return cards.map(card => {
      const result = {phones:[], emails:[]};
      for (const line of card.split(/\r?\n/)) {
        const colon = line.indexOf(':'); if (colon < 0) continue;
        const key = line.slice(0, colon).split(';')[0].toUpperCase(), value = line.slice(colon + 1).replace(/\\([nN])/g, '\n').replace(/\\([,;\\])/g, '$1');
        if (key === 'FN') result.formatted = value;
        else if (key === 'N') { const [family='', given='', middle=''] = value.split(';'); Object.assign(result,{family_name:family,given_name:given,middle_name:middle}); }
        else if (key === 'ORG') result.organization = value.replace(/;/g, ' ');
        else if (key === 'TEL') result.phones.push(value);
        else if (key === 'EMAIL') result.emails.push(value);
        else if (key === 'URL') (result.websites ||= []).push(value);
        else if (key === 'BDAY') { const m = value.match(/^(\d{4})-?(\d{2})-?(\d{2})$/); if (m) result.birthday = {year:+m[1],month:+m[2],day:+m[3]}; }
      }
      if (result.formatted && !result.given_name && !result.family_name) result.given_name = result.formatted;
      return result;
    });
  }
  window.DewiarContactCreate = {open, parseVCard};
})();
