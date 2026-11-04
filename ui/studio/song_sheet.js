/* Manual chord positions: clicking a character creates an explicit anchor. */
window.SongSheet = (() => {
  const esc = v => escH(String(v ?? ''));
  const shifts = new Map();
  const offset = row => shifts.get(row.song_id) || 0;
  function transpose(name, steps) {
    if (!steps) return name;
    const notes = ['C','Db','D','Eb','E','F','Gb','G','Ab','A','Bb','B'];
    const pitch = {C:0,D:2,E:4,F:5,G:7,A:9,B:11};
    return String(name).replace(/(^|\/)([A-G])([#b]?)/g, (_,prefix,n,acc) => prefix + notes[((pitch[n]+(acc==='#'?1:acc==='b'?-1:0)+steps)%12+12)%12]);
  }
  function sheetRows(row) {
    const parts = new Map((row.sections || []).map(s => [row.phrases?.[s.phrase_indexes?.[0]]?.phrase_id,s.label]));
    const lines = [];
    let stanzaLines = 0;
    for (const line of row.guitar_sheet || []) {
      if (lines.length && (parts.has(line.phrase_id) || stanzaLines >= 4)) { lines.push({kind:'space',text:''}); stanzaLines=0; }
      const chordRows = [];
      for (const cell of line.cells || []) {
        for (const name of cell.chords || []) {
          if (!name || name === 'N') continue;
          const chord = transpose(name, offset(row)), at = cell.char_index || 0;
          let i = chordRows.findIndex(r => r.length < at);
          if (i < 0) { i = chordRows.length; chordRows.push(''); }
          chordRows[i] = chordRows[i].padEnd(at, ' ') + chord;
        }
      }
      lines.push(...chordRows.map(text => ({kind:'chords',text})));
      if (line.text) { lines.push({kind:'lyrics',text:line.text}); stanzaLines++; }
      const unplaced = (line.unplaced_chords || []).filter(c => c.name && c.name !== 'N');
      if (unplaced.length) lines.push({kind:'chords',text:unplaced.map(c => transpose(c.name, offset(row))).join('  ')});
    }
    return lines;
  }
  function plainText(row) {
    return sheetRows(row).map(r=>r.text).join('\n').replace(/^\n+|\n+$/g, '');
  }
  function sourceLinks(row) {
    const all = [...(row.source_candidates || [])];
    if (row.source_ref) all.push({url:row.source_ref,title:'Исходная запись'});
    const rank = c => /аккорд|chord/i.test(c.title || '') ? 0 : c.kind === 'lyrics' ? 1 : 2;
    all.sort((a,b)=>rank(a)-rank(b));
    const seen = new Set(), links = [];
    for (const c of all) {
      let url;
      try { url = new URL(c.url); } catch (_) { continue; }
      if (!['http:','https:'].includes(url.protocol)) continue;
      url.hash=''; const key=url.href;
      if (seen.has(key)) continue;
      seen.add(key); links.push({url:key,title:c.title || url.hostname});
      if (links.length===3) break;
    }
    return links;
  }
  function sheetHTML(row) {
    if (!row.guitar_sheet?.length) return '';
    const links = sourceLinks(row);
    return `<article class="song-paper" aria-label="Итоговый текст с аккордами"><h3 class="song-paper-title">${esc(row.title)}${row.artist ? ' — '+esc(row.artist) : ''}</h3><div class="song-paper-scroll" tabindex="0" aria-label="Лист песни; прокрутка длинных строк"><div class="song-paper-body">${sheetRows(row).map(r=>`<div class="song-paper-${r.kind}">${esc(r.text) || '&nbsp;'}</div>`).join('')}</div></div>${links.length ? `<footer class="song-paper-sources"><span>Источники</span>${links.map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)}</a>`).join('')}</footer>` : ''}</article>`;
  }
  function icon(name) {
    const paths = {
      down:'<path d="M12 4v16m-6-6 6 6 6-6"/>',
      up:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
      reset:'<path d="M20 7v5h-5M20 12a8 8 0 1 0-2 5"/>',
      copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
      text:'<path d="M4 5h16M12 5v15M8 20h8M4 5v3m16-3v3"/>',
      play:'<path d="m8 4 12 8-12 8Z"/>',
      pause:'<path d="M8 4v16M16 4v16"/>',
      stop:'<rect x="5" y="5" width="14" height="14" rx="1"/>'
    };
    return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || ''}</svg>`;
  }
  const shiftLabel = row => offset(row)>0 ? `+${offset(row)}` : String(offset(row));
  function toolbar(row) {
    const button = (id, glyph, title, extra='') => `<button type="button" class="btn song-icon-btn" id="${id}" title="${title}" aria-label="${title}" ${extra}>${icon(glyph)}</button>`;
    return `<div class="song-sheet-tools" role="group" aria-label="Тональность, копирование и прослушивание">
      <div class="song-tool-group">
        ${button('song-key-down','down','Понизить аккорды на один полутон; звук записи не меняется')}
        <span id="song-key-label" role="status" title="Сдвиг аккордов относительно исходной тональности, в полутонах" aria-label="Сдвиг аккордов: ${shiftLabel(row)} полутонов">${shiftLabel(row)}</span>
        ${button('song-key-up','up','Повысить аккорды на один полутон; звук записи не меняется')}
        ${button('song-key-reset','reset','Вернуть аккорды к исходной тональности')}
      </div>
      <div class="song-tool-group song-copy-group" role="group" aria-label="Текст и копирование">
        ${button('song-show-text','text','Показать текст с аккордами для копирования в выбранной тональности','aria-controls="song-copy-panel" aria-expanded="false"')}
        ${button('song-copy-sheet','copy','Скопировать текст с аккордами')}
      </div>
      <div class="song-tool-group" role="group" aria-label="Прослушивание песни">
        ${button('song-sheet-play','play',row.capabilities?.audio ? 'Проиграть всю песню в исходной тональности записи' : 'Для прослушивания добавьте аудиозапись',row.capabilities?.audio ? '' : 'disabled')}
        ${button('song-sheet-stop','stop','Остановить воспроизведение и вернуться к началу песни','disabled')}
      </div>
      <span id="song-copy-status" role="status"></span><span id="song-sheet-play-status" role="status"></span>
    </div><div class="song-copy-preview" id="song-copy-panel" hidden><p class="hint">Текст в выбранной тональности. Чтобы сохранить расположение аккордов при вставке, используйте моноширинный шрифт.</p><textarea id="song-copy-preview" aria-label="Чистый текст с аккордами" readonly rows="16" wrap="off">${esc(plainText(row))}</textarea></div>`;
  }
  function bindToolbar(row, getCurrent, render) {
    if (!document.getElementById('song-key-up')) return;
    function change(delta, reset=false) {
      shifts.set(row.song_id,reset ? 0 : offset(row)+delta); render();
      const label = document.getElementById('song-key-label');
      label.textContent = shiftLabel(row);
      label.setAttribute('aria-label', `Сдвиг аккордов: ${shiftLabel(row)} полутонов`);
      document.getElementById('song-copy-preview').value = plainText(getCurrent());
      document.getElementById('song-copy-status').textContent = '';
    }
    document.getElementById('song-key-up').onclick = () => change(1);
    document.getElementById('song-key-down').onclick = () => change(-1);
    document.getElementById('song-key-reset').onclick = () => change(0,true);
    function showText(open) {
      document.getElementById('song-copy-panel').hidden = !open;
      document.getElementById('song-show-text').setAttribute('aria-expanded', String(open));
    }
    document.getElementById('song-show-text').onclick = () => showText(document.getElementById('song-copy-panel').hidden);
    document.getElementById('song-copy-sheet').onclick = async () => {
      const text = plainText(getCurrent());
      document.getElementById('song-copy-preview').value = text;
      try { await navigator.clipboard.writeText(text); document.getElementById('song-copy-status').textContent='Скопировано'; }
      catch (_) { const box=document.getElementById('song-copy-preview');box.value=text;showText(true);box.focus();box.select();document.getElementById('song-copy-status').textContent='Нажмите ⌘C / Ctrl+C, чтобы скопировать выделенный текст'; }
    };
  }
  function html(row) {
    if (!row.phrases?.length) return '';
    return `<details class="song-chord-editor"><summary>Править аккорды и положение над текстом</summary>
      <p class="hint">Выберите смену или добавьте новую. Время задаётся от начала песни. Чтобы поставить аккорд над гласной распева, нажмите на эту букву.</p>
      <label class="f">Смена<select id="song-chord-select"><option value="new">Новая смена</option>${(row.chords || []).map((c,i) => c.name === 'N' ? '' : `<option value="${i}">${(c.start_ms/1000).toFixed(2)} с — ${esc(c.name)}</option>`).join('')}</select></label>
      <label class="f">Аккорд<input id="song-chord-name" placeholder="Am, E7, Bb"></label>
      <label class="f">Время, секунды<input id="song-chord-time" type="number" min="0" step="0.01" value="0"></label>
      <p id="song-chord-letters" class="song-chord-letters"></p>
      <div class="actions"><button class="btn small" id="song-chord-save">Сохранить аккорд</button><button class="btn small" id="song-chord-remove">Удалить смену</button><button class="btn small" id="song-sheet-export">Скачать ChordPro</button></div>
      <p id="song-chord-status" role="status"></p></details>
      <details class="song-chord-editor"><summary>Завершить проверку песни</summary>
      <label class="song-reviewed"><input id="song-check-all-lyrics" type="checkbox"> Текст полностью проверен по записи</label>
      <label class="song-reviewed"><input id="song-check-all-chords" type="checkbox"> Аккорды и места смены проверены по записи</label>
      <button class="btn small" id="song-finish-review">Отметить песню готовой</button><p id="song-finish-status" role="status"></p></details>`;
  }
  function bind(row, getCurrent, accept) {
    const select = document.getElementById('song-chord-select');
    if (!select) return;
    const name = document.getElementById('song-chord-name'), time = document.getElementById('song-chord-time');
    const status = document.getElementById('song-chord-status'), letters = document.getElementById('song-chord-letters');
    let anchor = null;
    function showLetters() {
      const ms = Number(time.value)*1000;
      const phrase = getCurrent().phrases.find(p => p.start_ms <= ms && ms < p.end_ms);
      if (!phrase) { anchor = null; letters.textContent = 'В этом месте нет фразы — аккорд останется в проигрыше.'; return; }
      letters.innerHTML = Array.from(phrase.text).map((c,i) => `<button type="button" data-letter="${i}" class="song-letter ${anchor?.phrase_id === phrase.phrase_id && anchor?.grapheme_index === i ? 'on' : ''}" aria-label="${esc(c)} — позиция ${i+1}">${c === ' ' ? '&nbsp;' : esc(c)}</button>`).join('');
      letters.querySelectorAll('[data-letter]').forEach(b => b.onclick = () => {
        anchor = {kind:'manual', phrase_id:phrase.phrase_id, grapheme_index:Number(b.dataset.letter)};
        showLetters();
      });
    }
    select.onchange = () => {
      const event = getCurrent().chords?.[Number(select.value)];
      name.value = event?.name || ''; time.value = (event?.start_ms || 0)/1000;
      anchor = event?.anchor || null; showLetters();
    };
    time.oninput = () => { anchor = null; showLetters(); };
    async function save(remove) {
      try {
        const current = getCurrent(), events = structuredClone(current.chords || []).filter(c => c.name !== 'N');
        const original = current.chords?.[Number(select.value)];
        const index = original ? events.findIndex(c => c.start_ms === original.start_ms && c.name === original.name) : -1;
        if (remove) { if (index < 0) throw Error('Сначала выберите смену'); events.splice(index,1); }
        else {
          const event = {name:name.value.trim(),start_ms:Math.round(Number(time.value)*1000),anchor,verified:true,method:'manual'};
          if (index < 0) events.push(event); else events[index] = event;
        }
        events.sort((a,b) => a.start_ms-b.start_ms);
        const saved = await core.call('song_edit_chords',{song_id:row.song_id,base_revision:current.revision,chords:events});
        accept(saved); window.loadSongs(); status.textContent = 'Аккорд сохранён';
      } catch (e) { status.textContent = e.message; }
    }
    document.getElementById('song-chord-save').onclick = () => save(false);
    document.getElementById('song-chord-remove').onclick = () => save(true);
    document.getElementById('song-finish-review').onclick = async () => {
      const status = document.getElementById('song-finish-status');
      try {
        const current = getCurrent();
        const saved = await core.call('song_finish_review',{song_id:row.song_id,base_revision:current.revision,
          lyrics_checked:document.getElementById('song-check-all-lyrics').checked,
          chords_checked:document.getElementById('song-check-all-chords').checked});
        accept(saved); window.loadSongs(); status.textContent='Песня отмечена как проверенная';
      } catch(e) { status.textContent=e.message; }
    };
    document.getElementById('song-sheet-export').onclick = () => {
      const current = getCurrent();
      const lines = [`{title: ${current.title.replace(/[{}\n]/g,' ')}}`, `{artist: ${(current.artist || '').replace(/[{}\n]/g,' ')}}`,
        '{comment: Предварительный подбор Dewiar. B=си, Bb=си-бемоль.}'];
      for (const line of current.guitar_sheet || []) {
        lines.push((line.cells || []).map(c => (c.chords || []).map(n => `[${transpose(n,offset(current))}]`).join('')+c.text).join(''));
        for (const c of line.unplaced_chords || []) lines.push(`{comment: ${(c.start_ms/1000).toFixed(2)} с — ${transpose(c.name,offset(current))}, привязка не проверена}`);
      }
      const url = URL.createObjectURL(new Blob([lines.join('\n')+'\n'],{type:'text/plain;charset=utf-8'}));
      const link = document.createElement('a'); link.href=url; link.download=(current.title.replace(/[\\/:*?"<>|]/g,'_') || 'song')+'.cho'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    };
    showLetters();
  }
  return {html,bind,toolbar,bindToolbar,transpose,offset,plainText,sheetRows,sheetHTML,sourceLinks,icon};
})();
