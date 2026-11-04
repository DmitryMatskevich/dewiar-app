/* Проверка текста по фразам: прослушивание, пометки, автосохранение, undo/redo.
   Время слов не выдумывается. Черновик остаётся при обновлении карточки;
   конфликт ревизий показан рядом с полем и не перезаписывает введённый текст. */
window.SongReview = (() => {
  const drafts = new Map(), timers = new Map();
  let queue = Promise.resolve(), audioRequest = 0, stopPlayback = null, togglePlayback = null;
  window.addEventListener('beforeunload', event => {
    if (drafts.size) { event.preventDefault(); event.returnValue = ''; }
  });
  const esc = (s) => escH(s == null ? '' : s);
  const time = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
  const notes = [['', 'Без пометки'], ['unclear', 'Неразборчиво'], ['sung_differently', 'Спето иначе'], ['vocalization', 'Вокализ'], ['sustain', 'Предполагаемый распев']];
  const parts = [['continue','Продолжение части'],['verse','Начало куплета'],['chorus','Начало припева'],['prechorus','Предприпев'],['bridge','Бридж'],['unknown','Новая часть']];
  const key = (song, phrase) => `${song}:${phrase}`;
  function evidenceHtml(row) {
    const evidence = row.review_evidence || {};
    const issues = evidence.issues || [];
    return `${issues.length ? `<p class="song-note" role="status">Найдено подозрительных признаков: ${issues.length}. Пометки показаны рядом с фразами.</p>` : ''}` +
      (evidence.references || []).map(r => `<details class="song-note song-reference-diff"><summary>Сверка с публикацией: ${r.differences.length} различий</summary>
        ${/^https?:\/\//i.test(r.url || '') ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">Источник опубликованного текста</a>` : ''}
        <p>${esc(r.label)}. Запись может отличаться от опубликованного текста. Правки автоматически не применяются.</p>
        ${r.differences.map(d => `<div class="song-reference-change"><b>${time(d.start_ms || 0)}${d.anchor_estimated ? ' · рядом с фразой' : ''}</b>
          <p>Распознано: ${esc(d.recognized || '— нет соответствующего текста —')}</p><p>В публикации: ${esc(d.published || '— нет соответствующего текста —')}</p></div>`).join('')}</details>`).join('');
  }
  function timedText(phrase) {
    if (phrase.alignment_stale || !phrase.words?.length) return esc(phrase.text);
    let cursor = 0, output = '';
    for (const word of phrase.words) {
      if (word.char_start < cursor || word.char_end > phrase.text.length) continue;
      output += esc(phrase.text.slice(cursor, word.char_start));
      const vote = phrase.agreement?.find(a => a.char_start === word.char_start);
      output += `<span data-word-start="${word.start_ms}" data-word-end="${word.end_ms}" class="song-word ${vote?.needs_review ? 'uncertain' : ''}" title="${esc(vote ? `Совпало моделей: ${vote.agree}/${vote.models}. Варианты: ${vote.alternatives.join(' / ')}` : 'Предварительное время слова')}">${esc(phrase.text.slice(word.char_start, word.char_end))}</span>`;
      cursor = word.char_end;
    }
    return output + esc(phrase.text.slice(cursor));
  }
  function html(row) {
    const header = `<p class="song-note">${row.timing_quality === 'word_estimated' ? 'Времена слов определены моделью и требуют проверки. Подчёркнуты слова без согласия двух распознавателей.' : 'Время определено для фраз. Точные времена слов ещё не получены.'}</p>
      ${row.candidate_run_id ? `<details class="song-note"><summary>Новый вариант распознавания — ваши правки сохранены</summary>${(row.analysis_runs || []).find(r => r.run_id === row.candidate_run_id)?.phrases.map(p => `<p>${esc(p.text)}</p>`).join('') || 'В новом варианте нет текста'}</details>` : ''}
      <div class="song-player"><audio id="song-review-audio" controls preload="none" aria-label="Прослушивание песни"></audio><button class="btn small" id="song-play-full">Слушать всю песню</button><label><input id="song-review-loop" type="checkbox"> Повторять фрагмент</label><span id="song-play-status" role="status"></span></div>`;
    if (!row.phrases?.length) return (row.capabilities?.audio ? header : '') + '<p class="song-note">После распознавания здесь появятся фразы для прослушивания и правки.</p>';
    const starts = new Map((row.sections || []).map(s => [s.phrase_indexes?.[0], s]));
    return header + evidenceHtml(row) + row.phrases.map((p, i) => {
      const d = drafts.get(key(row.song_id, p.phrase_id)), h = row.phrase_history?.[p.phrase_id];
      const v = d || p, section = starts.get(i);
      return `${section ? `<h3 class="song-part-title">${esc(section.label)} <small>${esc(section.evidence)}</small></h3>` : ''}
        <article class="song-phrase" data-phrase-id="${esc(p.phrase_id)}">
          <div class="song-phrase-head"><span>${time(p.start_ms)}–${time(p.end_ms)}</span><span data-review-state>${p.review_state === 'verified' ? 'Текст проверен' : 'Нужно проверить'}</span></div>
          ${(row.review_evidence?.issues || []).filter(w => w.phrase_id === p.phrase_id).map(w => `<p class="song-note song-quality-warning">${esc(w.label)}</p>`).join('')}
          <p class="song-phrase-text">${d ? esc(v.text) : timedText(p)}</p>
          <div class="actions"><button class="btn small" data-phrase-play>Слушать фразу ${i + 1}</button><button class="btn small" data-phrase-edit aria-expanded="${!!d}">Править фразу ${i + 1}</button></div>
          <div class="song-phrase-editor" ${d ? '' : 'hidden'}>
            <label class="f">Текст фразы ${i + 1}<textarea rows="3" data-phrase-text>${esc(v.text)}</textarea></label>
            <label class="f">Пометка<select data-phrase-note>${notes.map(([n,t]) => `<option value="${n}" ${n === v.note ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
            <label class="song-reviewed"><input type="checkbox" data-phrase-reviewed ${v.review_state === 'verified' ? 'checked' : ''}> Проверено по записи</label>
            <label class="f">Часть песни<select data-phrase-part>${parts.map(([k,t]) => `<option value="${k}" ${k === (p.section_start || 'continue') ? 'selected' : ''}>${t}</option>`).join('')}</select></label>
            <div class="actions"><button class="btn small" data-phrase-part-save>Сохранить часть ${i + 1}</button><button class="btn small" data-phrase-split>Разделить фразу ${i + 1}</button><button class="btn small" data-phrase-merge ${i+1 >= row.phrases.length ? 'disabled' : ''}>Объединить со следующей</button></div>
            <p class="hint">Для разделения поставьте курсор между словами. Если времён слов нет, сначала остановите прослушивание в месте разделения.</p>
            <div class="actions"><button class="btn small" data-phrase-save>Сохранить фразу ${i + 1}</button><button class="btn small" data-phrase-undo ${h?.cursor > 0 ? '' : 'disabled'}>Отменить правку ${i + 1}</button><button class="btn small" data-phrase-redo ${h && h.cursor + 1 < (h.version_count ?? h.versions?.length ?? 0) ? '' : 'disabled'}>Повторить правку ${i + 1}</button></div>
            <p class="song-save-status" role="status">${d ? 'Есть несохранённая правка' : 'Изменения сохраняются автоматически'}</p>
          </div>
        </article>`;
    }).join('');
  }
  function protectedView(songId) {
    const active = document.activeElement;
    return [...drafts.keys()].some(k => k.startsWith(songId + ':')) ||
      !!(active?.matches('input,textarea,select') && active.closest('.song-phrase-editor, .song-properties')) ||
      !!document.querySelector('#song-review-audio')?.matches(':not([data-idle]),[data-session],[data-loading],[data-sheet-hold]');
  }
  function stop() {
    audioRequest++;
    if (stopPlayback) stopPlayback();
  }
  function bind(row, getCurrent, accept) {
    const player = document.getElementById('song-review-audio');
    if (!player) return;
    const play = document.getElementById('song-sheet-play');
    const stopButton = document.getElementById('song-sheet-stop');
    const previousButton = document.getElementById('song-sheet-previous');
    const nextButton = document.getElementById('song-sheet-next');
    const full = document.getElementById('song-play-full');
    const status = document.getElementById('song-play-status');
    const sheetStatus = document.getElementById('song-sheet-play-status');
    let fullData = null, followSheet = true;
    const message = text => { status.textContent = text; if (sheetStatus) sheetStatus.textContent = text; };
    function sync() {
      if (!player.isConnected) return;
      const loading = !!player.dataset.loading, playing = !player.paused && !player.ended;
      if (play) {
        const title = loading ? 'Подготавливаем запись для прослушивания' : !row.capabilities?.audio ? 'Для прослушивания добавьте аудиозапись' : playing ? 'Приостановить воспроизведение; продолжить можно с этого места' : player.dataset.session && !player.dataset.phraseId ? 'Продолжить воспроизведение песни с места паузы' : 'Проиграть всю песню в исходной тональности записи';
        play.innerHTML = SongSheet.icon(playing ? 'pause' : 'play');
        play.title = title; play.setAttribute('aria-label',title);
        play.setAttribute('aria-busy',String(loading));
        play.disabled = loading || !row.capabilities?.audio;
      }
      if (stopButton) stopButton.disabled = !(loading || player.dataset.session || playing || player.currentTime > 0);
      for (const [button,direction] of [[previousButton,-1],[nextButton,1]]) {
        if(button) button.disabled = loading || !player.dataset.session || !!player.dataset.phraseId ||
          SongSheet.lineTarget(SongSheet.lineStarts(row.song_id),player.currentTime*1000,direction)===null;
      }
      full.disabled = loading || !row.capabilities?.audio;
      window.onSongPlayback?.(row.song_id,{loading,playing,active:loading || !!player.dataset.session || playing || player.currentTime>0});
    }
    function clearHighlights() {
      document.querySelectorAll('.song-phrase.playing, .song-word.current-word').forEach(el => el.classList.remove('playing','current-word'));
    }
    stopPlayback = () => {
      player.pause();
      player.dataset.sheetStopped = '1';
      player.dataset.sheetHold = '1';
      if (player.src) player.currentTime = 0;
      delete player.dataset.loading; delete player.dataset.session;
      player.dataset.idle = '1'; clearHighlights(); message(''); sync();
    };
    player.dataset.idle = '1';
    function followWords(scroll) {
      if (!player.dataset.sheetStopped) SongSheet.updatePlayback(row.song_id,Number(player.dataset.clipStart || 0)+player.currentTime*1000,scroll);
    }
    player.addEventListener('timeupdate', () => { followWords(followSheet && !player.paused); sync(); });
    player.addEventListener('seeked', () => { followWords(followSheet && !player.paused); sync(); });
    player.onplay = () => { delete player.dataset.idle; delete player.dataset.sheetStopped; delete player.dataset.sheetHold; player.dataset.session = '1'; followWords(followSheet); sync(); };
    player.onpause = () => { player.dataset.idle = '1'; followWords(false); sync(); };
    player.addEventListener('ended', () => { delete player.dataset.session; player.dataset.idle = '1'; clearHighlights(); message(''); sync(); });
    player.addEventListener('error', () => { delete player.dataset.session; delete player.dataset.loading; player.dataset.idle = '1'; message('Не удалось воспроизвести запись. Попробуйте ещё раз.'); sync(); });
    document.getElementById('song-review-loop').onchange = e => { player.loop = e.target.checked; };
    async function playFull(restart=false) {
      const request = ++audioRequest;
      player.pause(); player.dataset.loading = '1'; sync();
      message(fullData ? '' : 'Подготавливаем запись…');
      try {
        if (!fullData) {
          const clip = await core.call('song_full_audio',{song_id:row.song_id});
          if (request !== audioRequest || !player.isConnected) return;
          fullData = clip.data;
        }
        if (request !== audioRequest || !player.isConnected) return;
        if (player.src !== fullData) player.src = fullData;
        if (restart || player.ended) player.currentTime = 0;
        player.dataset.clipStart = '0'; delete player.dataset.phraseId;
        player.loop = false; document.getElementById('song-review-loop').checked = false;
        player.ontimeupdate = () => {
          const position = player.currentTime*1000;
          document.querySelectorAll('.song-phrase[data-phrase-id]').forEach(el => {
            const phrase = getCurrent()?.phrases.find(p=>p.phrase_id === el.dataset.phraseId);
            el.classList.toggle('playing',!!phrase && phrase.start_ms <= position && position < phrase.end_ms);
            el.querySelectorAll('[data-word-start]').forEach(w => w.classList.toggle('current-word',
              position >= Number(w.dataset.wordStart) && position < Number(w.dataset.wordEnd)));
          });
        };
        player.onended = null;
        await player.play();
        if (request === audioRequest) message('');
      } catch (error) { if (request === audioRequest) message(error.message || 'Не удалось воспроизвести запись'); }
      finally { if (request === audioRequest && player.isConnected) { delete player.dataset.loading; sync(); } }
    }
    full.onclick = () => { followSheet=true; return playFull(true); };
    togglePlayback = (follow=true) => { followSheet=follow; if (!player.paused && !player.ended) { player.pause(); return; } return playFull(); };
    if (play) play.onclick = () => togglePlayback(true);
    if (stopButton) stopButton.onclick = stop;
    function seekLine(direction) {
      if(player.dataset.loading || !player.dataset.session || player.dataset.phraseId) return;
      const target=SongSheet.lineTarget(SongSheet.lineStarts(row.song_id),player.currentTime*1000,direction);
      if(target===null) return;
      // Round forward so browser microsecond precision cannot land before the cue.
      player.currentTime=Math.ceil(target)/1000;
      followWords(true); sync();
    }
    if(previousButton) previousButton.onclick=()=>seekLine(-1);
    if(nextButton) nextButton.onclick=()=>seekLine(1);
    sync();
    document.querySelectorAll('.song-phrase[data-phrase-id]').forEach(el => {
      const pid = el.dataset.phraseId, k = key(row.song_id, pid);
      const text = el.querySelector('[data-phrase-text]'), note = el.querySelector('[data-phrase-note]'), checked = el.querySelector('[data-phrase-reviewed]');
      const status = el.querySelector('.song-save-status');
      async function structure(action) {
        if (drafts.has(k)) { status.textContent = 'Сначала сохраните текст фразы'; return; }
        try {
          const current = getCurrent();
          const args = {song_id: row.song_id, phrase_id: pid, base_revision: current.revision, action,
            char_index: text.selectionStart, kind: el.querySelector('[data-phrase-part]').value};
          if (action === 'split' && player.dataset.phraseId === pid && player.currentTime > 0) {
            args.time_ms = Math.round(Number(player.dataset.clipStart)+player.currentTime*1000);
          }
          const saved = await core.call('song_edit_structure',args);
          stop(); accept(saved); window.loadSongs();
          status.textContent = 'Разметка сохранена. Обновите карточку, если она открыта для правки.';
        } catch (error) { status.textContent = error.message; }
      }
      el.querySelector('[data-phrase-part-save]').onclick = () => structure('section');
      el.querySelector('[data-phrase-split]').onclick = () => structure('split');
      el.querySelector('[data-phrase-merge]').onclick = () => structure('merge');
      const read = () => ({text: text.value, note: note.value, review_state: checked.checked ? 'verified' : 'needs_review'});
      function save(action = 'edit') {
        clearTimeout(timers.get(k));
        const value = read();
        if (action === 'edit') drafts.set(k, value);
        status.textContent = 'Сохраняем…';
        queue = queue.catch(() => {}).then(async () => {
          const current = getCurrent();
          if (current?.song_id !== row.song_id) { status.textContent = 'Правка сохранена в черновике этой вкладки'; return; }
          try {
            const saved = await core.call('song_edit_phrase', {song_id: row.song_id, phrase_id: pid,
              base_revision: current.revision, text: value.text, note: value.note,
              reviewed: value.review_state === 'verified', action});
            accept(saved);
            const p = saved.phrases.find(x => x.phrase_id === pid), history = saved.phrase_history[pid];
            if (action !== 'edit' || JSON.stringify(drafts.get(k)) === JSON.stringify(value)) {
              drafts.delete(k); text.value = p.text; note.value = p.note || ''; checked.checked = p.review_state === 'verified';
              el.querySelector('.song-phrase-text').textContent = p.text;
              el.querySelector('[data-review-state]').textContent = checked.checked ? 'Текст проверен' : 'Нужно проверить';
              status.textContent = 'Сохранено';
            } else status.textContent = 'Сохраняем следующие изменения…';
            el.querySelector('[data-phrase-undo]').disabled = history.cursor === 0;
            el.querySelector('[data-phrase-redo]').disabled = history.cursor + 1 >= (history.version_count ?? history.versions?.length ?? 0);
          } catch (error) {
            status.textContent = `${error.message || 'Ошибка сохранения'}. Текст оставлен в поле. Обновите карточку и повторите сохранение.`;
          }
        });
      }
      function changed() {
        drafts.set(k, read()); status.textContent = 'Есть несохранённая правка';
        clearTimeout(timers.get(k)); timers.set(k, setTimeout(() => save(), 800));
      }
      text.oninput = () => { checked.checked = false; changed(); };
      note.onchange = changed; checked.onchange = changed;
      el.querySelector('[data-phrase-save]').onclick = () => save();
      el.querySelector('[data-phrase-undo]').onclick = () => save('undo');
      el.querySelector('[data-phrase-redo]').onclick = () => save('redo');
      el.querySelector('[data-phrase-edit]').onclick = e => {
        const editor = el.querySelector('.song-phrase-editor'); editor.hidden = !editor.hidden;
        e.target.setAttribute('aria-expanded', String(!editor.hidden));
        if (!editor.hidden) text.focus();
      };
      el.querySelector('[data-phrase-play]').onclick = async e => {
        const request = ++audioRequest, button = e.target;
        const playStatus = document.getElementById('song-play-status');
        player.pause(); player.dataset.loading = '1'; sync();
        button.disabled = true; playStatus.textContent = 'Готовим фрагмент…';
        try {
          const clip = await core.call('song_phrase_audio', {song_id: row.song_id, phrase_id: pid});
          if (request !== audioRequest || !player.isConnected) return;
          player.src = clip.data;
          player.dataset.clipStart = clip.start_ms;
          player.dataset.phraseId = pid;
          player.ontimeupdate = () => {
            const position = clip.start_ms + player.currentTime * 1000;
            el.querySelectorAll('[data-word-start]').forEach(w => w.classList.toggle('current-word',
              position >= Number(w.dataset.wordStart) && position < Number(w.dataset.wordEnd)));
          };
          document.querySelectorAll('.song-phrase.playing').forEach(x => x.classList.remove('playing'));
          el.classList.add('playing');
          player.onended = () => { el.classList.remove('playing'); player.dataset.idle = '1'; };
          await player.play();
          playStatus.textContent = 'Фраза с небольшим контекстом';
        } catch (error) { playStatus.textContent = error.message || 'Не удалось воспроизвести'; }
        finally { button.disabled = false; if (request === audioRequest && player.isConnected) { delete player.dataset.loading; sync(); } }
      };
    });
  }
  return {html, bind, protectedView, stop, toggleFull:(follow=true)=>togglePlayback?.(follow)};
})();
