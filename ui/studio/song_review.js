/* Проверка текста по фразам: прослушивание, пометки, автосохранение, undo/redo.
   Время слов не выдумывается. Черновик остаётся при обновлении карточки;
   конфликт ревизий показан рядом с полем и не перезаписывает введённый текст. */
window.SongReview = (() => {
  const drafts = new Map(), timers = new Map();
  let queue = Promise.resolve(), audioRequest = 0, stopPlayback = null, togglePlayback = null, selectPlaybackVariant = null, setPlaybackVariant = null;
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
      `;
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
      !!(active?.matches('input,textarea,select') && active.closest('.song-phrase-editor, .song-properties, .song-live-editor')) ||
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
    let fullData = new Map(), followSheet = true, variant = 'original';
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
      window.onSongPlayback?.(row.song_id,{loading,playing,variant,active:loading || !!player.dataset.session || playing || player.currentTime>0});
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
      player.dataset.idle = '1'; clearHighlights(); SongSheet.updatePlayback(row.song_id,-1,false); message(''); sync();
    };
    player.dataset.idle = '1';
    function followWords(scroll) {
      if (!player.dataset.sheetStopped) SongSheet.updatePlayback(row.song_id,Number(player.dataset.clipStart || 0)+player.currentTime*1000,scroll);
    }
    player.addEventListener('timeupdate', () => { followWords(followSheet && !player.paused); sync(); });
    player.addEventListener('seeked', () => { followWords(followSheet && !player.paused); sync(); });
    player.onplay = () => { delete player.dataset.idle; delete player.dataset.sheetStopped; delete player.dataset.sheetHold; player.dataset.session = '1'; followWords(followSheet); sync(); };
    player.onpause = () => { player.dataset.idle = '1'; followWords(false); sync(); };
    player.addEventListener('ended', () => { delete player.dataset.session; player.dataset.idle = '1'; clearHighlights(); SongSheet.updatePlayback(row.song_id,-1,false); message(''); sync(); });
    player.addEventListener('error', () => { delete player.dataset.session; delete player.dataset.loading; player.dataset.idle = '1'; message('Не удалось воспроизвести запись. Попробуйте ещё раз.'); sync(); });
    document.getElementById('song-review-loop').onchange = e => { player.loop = e.target.checked; };
    async function playFull(restart=false, autoplay=true, seekTo=null) {
      const request = ++audioRequest;
      player.pause(); player.dataset.loading = '1'; sync();
      message(fullData ? '' : 'Подготавливаем запись…');
      try {
        if (!fullData.has(variant)) {
          const clip = await core.call('song_full_audio',{song_id:row.song_id,variant});
          if (request !== audioRequest || !player.isConnected) return;
          fullData.set(variant, clip.data);
        }
        if (request !== audioRequest || !player.isConnected) return;
        const source = fullData.get(variant);
        if (player.src !== source) {
          player.src = source;
          if (typeof player.load === 'function') await new Promise((resolve,reject) => {
            const done=()=>{cleanup();resolve();}, fail=()=>{cleanup();reject(new Error('Не удалось загрузить дорожку'))};
            const cleanup=()=>{player.removeEventListener('loadedmetadata',done);player.removeEventListener('error',fail)};
            player.addEventListener('loadedmetadata',done,{once:true});player.addEventListener('error',fail,{once:true});player.load();
          });
          if (request !== audioRequest || !player.isConnected) return;
        }
        if (seekTo !== null && Number.isFinite(seekTo)) player.currentTime=Math.min(seekTo,Math.max(0,(player.duration||seekTo)-0.05));
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
        if (autoplay) await player.play();
        if (request === audioRequest) message('');
      } catch (error) { if (request === audioRequest) message(error.message || 'Не удалось воспроизвести запись'); }
      finally { if (request === audioRequest && player.isConnected) { delete player.dataset.loading; sync(); } }
    }
    full.onclick = () => { followSheet=true; return playFull(true); };
    const variantSelect = document.getElementById('song-play-variant');
    setPlaybackVariant = value => {
      if (!variantSelect || (value === 'instrumental' && row.karaoke?.state !== 'ready')) return false;
      variant=variantSelect.value=value;
      return true;
    };
    selectPlaybackVariant = value => {
      if (!setPlaybackVariant(value)) return Promise.resolve();
      followSheet=true;
      return playFull(false,true,player.dataset.session ? player.currentTime : null);
    };
    if (variantSelect) variantSelect.onchange = async () => {
      const wasPlaying = !player.paused && !player.ended;
      const position = player.currentTime;
      const next = variantSelect.value;
      if (next === 'instrumental' && row.karaoke?.state !== 'ready') { variantSelect.value = variant; return; }
      variant = next;
      if (player.dataset.session) {
        player.pause();
        await playFull(false,wasPlaying,position);
      }
    };
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
    const liveEditor=document.getElementById('song-live-editor');
    const liveMode=document.getElementById('song-live-edit-mode');
    const liveText=document.getElementById('song-live-edit-text');
    const livePreview=document.getElementById('song-live-edit-preview');
    const livePreviewChords=document.getElementById('song-live-edit-preview-chords');
    const liveChords=document.getElementById('song-live-chords');
    const liveStatus=document.getElementById('song-live-edit-status');
    let liveTarget=null, livePhraseText='', liveChordDraft=[];
    function targetPhrase() {
      const current=getCurrent();
      return liveTarget && current?.phrases?.find(p=>p.phrase_id===liveTarget.phraseId);
    }
    function phraseChords(phrase, chords=getCurrent()?.chords||[]) {
      const current=getCurrent(), ids=new Set(liveTarget?.phraseIds?.length?liveTarget.phraseIds:[phrase.phrase_id]);
      const indexes=(current?.phrases||[]).map((item,index)=>ids.has(item.phrase_id)?index:-1).filter(index=>index>=0);
      const first=indexes[0]??-1,last=indexes.at(-1)??first;
      const gapStart=first>0?Number(current.phrases[first-1].end_ms):0;
      const end=last>=0?Number(current.phrases[last].end_ms):Number(phrase.end_ms);
      return chords.filter(c=>c.name!=='N' && (ids.has(c.anchor?.phrase_id) ||
        (Number(c.start_ms)>=gapStart && Number(c.start_ms)<end)));
    }
    function updateLivePreview() {
      if(!livePreview||!liveTarget)return;
      let text=livePhraseText;
      if(liveMode.value==='phrase') text=liveText.value;
      else if(liveMode.value==='word') {
        const start=liveTarget.charStart,end=liveTarget.charEnd;
        text=livePhraseText.slice(0,start)+liveText.value+livePhraseText.slice(end);
      }
      text=(text||'').trim();
      livePreview.textContent=text||'…';
      if(!livePreviewChords)return;
      const events=liveChordDraft.map((chord,index)=>{
        const rowElement=liveChords.querySelector(`[data-live-chord="${index}"]`);
        const name=rowElement?.querySelector('[data-live-chord-name]')?.value.trim()||chord.name||'';
        const seconds=Number(rowElement?.querySelector('[data-live-chord-time]')?.value);
        const startMs=Number.isFinite(seconds)&&seconds>=0?Math.round(seconds*1000):Number(chord.start_ms)||0;
        const phraseId=chord.anchor?.phrase_id;
        const span=liveTarget.sourceSpans?.find(item=>item.phrase_id===phraseId &&
          Number(chord.anchor?.grapheme_index)>=Number(item.phrase_char_start) &&
          Number(chord.anchor?.grapheme_index)<=Number(item.phrase_char_end));
        const anchorIndex=span
          ? Number(span.display_char_start)+Number(chord.anchor.grapheme_index)-Number(span.phrase_char_start)
          : liveTarget.phraseIds?.includes(phraseId)?Number(chord.anchor?.grapheme_index):null;
        const changedTime=startMs!==Number(chord.original_start_ms??chord.start_ms);
        const ratio=(startMs-liveTarget.startMs)/Math.max(1,liveTarget.endMs-liveTarget.startMs);
        const charIndex=changedTime||!Number.isFinite(anchorIndex)
          ? Math.round(Math.max(0,Math.min(1,ratio))*Math.max(0,text.length-1))
          : anchorIndex;
        return {...chord,name,char_index:Math.max(0,Math.min(Math.max(0,text.length-1),charIndex)),start_ms:startMs};
      }).filter(chord=>chord.name);
      const width=Math.max(42,text.length+Math.max(1,...events.map(chord=>chord.name.length)));
      const placed=window.SongSheet?.compactChords({text,start_ms:liveTarget.startMs,end_ms:liveTarget.endMs,chords:events},0,width)||[];
      let chordLine='',cursor=0;
      for(const chord of placed) {
        const at=Math.max(cursor,chord.at);
        chordLine+=esc(' '.repeat(at-cursor))+`<span class="song-chord-tip" title="${esc(window.SongSheet.chordTooltip(chord.name))}">${esc(chord.name)}</span>`;
        cursor=at+chord.name.length;
      }
      livePreviewChords.innerHTML=chordLine;
      livePreviewChords.hidden=!chordLine;
    }
    function renderLiveChords() {
      liveChords.innerHTML=liveChordDraft.map((chord,index)=>`<div class="song-live-chord" data-live-chord="${index}">
        <label class="f">Аккорд<input data-live-chord-name value="${esc(chord.name||'')}" maxlength="24" placeholder="Am, D7"></label>
        <label class="f">Секунда<input data-live-chord-time type="number" min="0" step="0.01" value="${(Number(chord.start_ms)||0)/1000}"></label>
        <button type="button" class="btn small" data-live-chord-remove="${index}" aria-label="Убрать аккорд ${index+1}" title="Убрать аккорд">×</button>
      </div>`).join('') || '<p class="hint">В этой фразе пока нет аккордов.</p>';
      liveChords.querySelectorAll('[data-live-chord-remove]').forEach(button=>button.onclick=()=>{
        liveChordDraft.splice(Number(button.dataset.liveChordRemove),1);renderLiveChords();
      });
      liveChords.querySelectorAll('[data-live-chord-name]').forEach(input=>{
        input.title=window.SongSheet?.chordTooltip(input.value)||'Название аккорда';
        input.addEventListener('input',()=>{input.title=window.SongSheet?.chordTooltip(input.value)||'Название аккорда';updateLivePreview();});
      });
      liveChords.querySelectorAll('[data-live-chord-time]').forEach(input=>input.addEventListener('input',updateLivePreview));
      updateLivePreview();
    }
    function openPhraseEditor(phraseId,mode='phrase',displayLineId='') {
      const current=getCurrent();
      const displayLine=mode==='phrase'&&displayLineId
        ? current?.guitar_sheet?.find(line=>line.phrase_id===displayLineId&&line.text&&line.source_spans?.length>1)
        : null;
      const sourcePhraseIds=displayLine?.source_spans?.map(span=>span.phrase_id).filter((id,index,all)=>id&&all.indexOf(id)===index)||[phraseId];
      const phrase=current?.phrases?.find(item=>item.phrase_id===sourcePhraseIds[0]);
      if(!phrase) return;
      player.pause();
      const sourcePhrases=sourcePhraseIds.map(id=>current.phrases.find(item=>item.phrase_id===id)).filter(Boolean);
      liveTarget={phraseId:phrase.phrase_id,phraseIds:sourcePhraseIds,displayLineId:displayLine?.phrase_id||'',
        text:displayLine?.text||phrase.text,charStart:0,charEnd:(displayLine?.text||phrase.text).length,
        sourceSpans:displayLine?.source_spans||[],
        startMs:Math.min(...sourcePhrases.map(item=>Number(item.start_ms)||0)),
        endMs:Math.max(...sourcePhrases.map(item=>Number(item.end_ms)||0))};
      liveMode.value=mode;
      livePhraseText=liveTarget.text;
      liveText.value=liveTarget.text;
      document.getElementById('song-live-edit-target').textContent=`${time(liveTarget.startMs)}–${time(liveTarget.endMs)} · ${mode==='chords'?'Аккорды фразы':displayLine?'Текст строки':'Текст фразы'}`;
      liveEditor.classList.toggle('song-live-editor--chords',mode==='chords');
      document.querySelector('#song-live-editor .song-live-editor-head b').textContent=mode==='chords'?'Правка аккордов фразы':'Правка фразы';
      document.getElementById('song-live-edit-save').textContent=mode==='chords'?'Сохранить аккорды':'Сохранить текст и аккорды';
      document.getElementById('song-live-reviewed').checked=sourcePhrases.every(item=>item.review_state==='verified');
      liveChordDraft=phraseChords(phrase).map(c=>({...structuredClone(c),original_start_ms:Number(c.start_ms)||0}));
      renderLiveChords();updateLivePreview();liveStatus.textContent='Песня приостановлена. Правки сохраняются в карточке песни.';
      liveEditor.hidden=false;
      liveEditor.scrollIntoView({block:'nearest',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
      if(mode==='phrase') liveText.focus();
      else liveChords.querySelector('[data-live-chord-name]')?.focus();
    }
    window.songPaperEditContext={songId:row.song_id,open:openPhraseEditor};
    if(!window.songPaperEditListener) {
      document.addEventListener('click',event=>{
        const button=event.target.closest?.('[data-song-edit-phrase][data-song-edit-kind]');
        const context=window.songPaperEditContext;
        if(!button||button.closest('.song-paper')?.dataset.songId!==context?.songId) return;
        context.open(button.dataset.songEditPhrase,button.dataset.songEditKind==='chords'?'chords':'phrase',button.dataset.songEditLine||'');
      });
      window.songPaperEditListener=true;
    }
    liveText?.addEventListener('input',updateLivePreview);
    document.getElementById('song-live-chord-add')?.addEventListener('click',()=>{
      const phrase=targetPhrase();if(!phrase)return;
      liveChordDraft.push({name:'',start_ms:liveTarget?.startMs ?? phrase.start_ms,original_start_ms:liveTarget?.startMs ?? phrase.start_ms,
        anchor:liveTarget?{kind:'manual',phrase_id:phrase.phrase_id,grapheme_index:liveTarget.charStart}:null,
        method:'manual',verified:true});renderLiveChords();
    });
    document.getElementById('song-live-edit-close')?.addEventListener('click',()=>{liveEditor.hidden=true;});
    document.getElementById('song-live-edit-save')?.addEventListener('click',async()=>{
      const button=document.getElementById('song-live-edit-save');button.disabled=true;liveStatus.textContent='Сохраняем…';
      try {
        let current=getCurrent(), phrase=targetPhrase();
        if(!phrase||!liveTarget)throw Error('Фраза уже изменилась — обновите карточку и повторите правку');
        const mode=liveMode.value, editText=liveText.value.trim();
        const groupedLine=mode==='phrase'&&liveTarget.displayLineId&&liveTarget.phraseIds?.length>1;
        const originalText=mode==='word'?liveTarget.text:groupedLine?liveTarget.text:phrase.text;
        const reviewed=document.getElementById('song-live-reviewed').checked;
        const reviewChanged=liveTarget.phraseIds.some(id=>(current.phrases.find(item=>item.phrase_id===id)?.review_state==='verified')!==reviewed);
        if(mode!=='chords' && (editText!==originalText || reviewChanged)) {
          current=await core.call(groupedLine?'song_edit_display_line':'song_edit_phrase',groupedLine
            ? {song_id:row.song_id,line_id:liveTarget.displayLineId,base_revision:current.revision,text:editText,reviewed}
            : {song_id:row.song_id,phrase_id:phrase.phrase_id,
              base_revision:current.revision,text:editText,note:phrase.note||'',reviewed,
              ...(mode==='word'?{word_start:liveTarget.charStart,word_start_ms:liveTarget.startMs,word_end_ms:liveTarget.endMs}:{})});
          accept(current);phrase=current.phrases.find(p=>p.phrase_id===liveTarget.phraseId);
          if(mode==='word') {
            const updated=phrase?.words?.find(w=>w.char_start===liveTarget.charStart);
            if(updated)liveTarget={...liveTarget,text:updated.text,charEnd:updated.char_end};
          }
          if(groupedLine){liveTarget.text=editText;livePhraseText=editText;}
          else if(phrase)livePhraseText=phrase.text;
          updateLivePreview();
        }
        const oldPhraseEvents=phraseChords(phrase,current.chords||[]);
        const newPhraseEvents=liveChordDraft.map((draft,index)=>{
          const rowElement=liveChords.querySelector(`[data-live-chord="${index}"]`);
          const name=rowElement?.querySelector('[data-live-chord-name]')?.value.trim();
          const seconds=Number(rowElement?.querySelector('[data-live-chord-time]')?.value);
          if(!name)throw Error(`Введите название аккорда ${index+1}`);
          if(!Number.isFinite(seconds)||seconds<0)throw Error(`Проверьте время аккорда ${index+1}`);
          return {...draft,name,start_ms:Math.round(seconds*1000),verified:true,method:draft.method||'manual'};
        });
        const signature=events=>JSON.stringify(events.map(({name,start_ms,anchor})=>({name,start_ms,anchor})));
        if(signature(oldPhraseEvents)!==signature(newPhraseEvents)) {
          const keep=(current.chords||[]).filter(c=>c.name==='N'||!oldPhraseEvents.includes(c));
          current=await core.call('song_edit_chords',{song_id:row.song_id,base_revision:current.revision,
            chords:[...keep,...newPhraseEvents].sort((a,b)=>a.start_ms-b.start_ms)});
          accept(current);
        }
        liveStatus.textContent=mode==='chords'
          ? 'Аккорды фразы сохранены. Воспроизведение осталось на паузе.'
          : mode==='word'
            ? 'Слово и времена слов сохранены; аккорды фразы обновлены. Воспроизведение осталось на паузе.'
            : 'Текст и аккорды сохранены. Для изменённой фразы времена слов нужно проверить заново. Воспроизведение осталось на паузе.';
      } catch(error) { liveStatus.textContent=`${error.message||'Не удалось сохранить правку'}. Исправления оставлены в редакторе.`; }
      finally { button.disabled=false; }
    });
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
  function playerHtml(row) {
    const state=row.karaoke?.state||'absent', busy=['queued','preparing','separating','checking'].includes(state);
    const status=state==='ready' ? `Минусовка готова${row.karaoke.quality_status==='acceptable'?' и проверена на слух':row.karaoke.quality_status==='defects'?' · есть замечания':' · ожидает оценки качества'}` : state==='failed' ? `Ошибка подготовки: ${row.karaoke.error||'см. состояние этапа'}` : busy ? 'Идёт отдельный этап подготовки минусовки…' : 'Минусовка ещё не подготовлена';
    return `<section class="song-instrumental-stage" aria-label="Воспроизведение и минусовка"><h3>Воспроизведение</h3><div class="song-player"><audio id="song-review-audio" controls preload="none" aria-label="Прослушивание песни"></audio><div class="song-player-actions"><label>Дорожка <select id="song-play-variant"><option value="original">Оригинал</option><option value="instrumental" ${state==='ready'?'':'disabled'}>Минусовка</option></select></label><button class="btn small" id="song-play-full">Слушать всю песню</button><label><input id="song-review-loop" type="checkbox"> Повторять фрагмент</label></div><span id="song-play-status" role="status">${esc(status)}</span></div></section>`;
  }
  return {html, playerHtml, bind, protectedView, stop, toggleFull:(follow=true)=>togglePlayback?.(follow), playVariant:value=>selectPlaybackVariant?.(value), setVariant:value=>setPlaybackVariant?.(value)};
})();
