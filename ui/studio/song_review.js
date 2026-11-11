/* Проверка текста по фразам: прослушивание, пометки, автосохранение, undo/redo.
   Время слов не выдумывается. Черновик остаётся при обновлении карточки;
   конфликт ревизий показан рядом с полем и не перезаписывает введённый текст. */
window.SongReview = (() => {
  const drafts = new Map(), timers = new Map();
  let queue = Promise.resolve(), audioRequest = 0, stopPlayback = null, togglePlayback = null, selectPlaybackVariant = null, setPlaybackVariant = null, setPitchPlayback = null;
  let spacePlaybackHandler = null, spacePlaybackBound = false;
  const speedBySong = new Map(), pitchBySong = new Map();
  window.addEventListener('beforeunload', event => {
    if (drafts.size) { event.preventDefault(); event.returnValue = ''; }
  });
  const esc = (s) => escH(s == null ? '' : s);
  const time = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
  const formatSeconds = (ms) => `${(Number(ms || 0) / 1000).toFixed(3)} с`;
  const notes = [['', 'Без пометки'], ['unclear', 'Неразборчиво'], ['sung_differently', 'Спето иначе'], ['vocalization', 'Вокализ'], ['sustain', 'Предполагаемый распев']];
  const parts = [['continue','Продолжение части'],['verse','Начало куплета'],['chorus','Начало припева'],['prechorus','Предприпев'],['bridge','Бридж'],['unknown','Новая часть']];
  const key = (song, phrase) => `${song}:${phrase}`;
  async function playAudio(audio) {
    try {
      return await audio.play();
    } catch (error) {
      // Loading the selected original/karaoke track is asynchronous.  On
      // WebKit/Chromium that can outlive the click gesture and reject an
      // otherwise valid play() with NotAllowedError.  A muted retry is the
      // standard autoplay recovery; unmute immediately after it starts so the
      // user still hears the selected track.
      if (error?.name !== 'NotAllowedError') throw error;
      const muted = audio.muted;
      audio.muted = true;
      try {
        return await audio.play();
      } finally {
        audio.muted = muted;
      }
    }
  }
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
      !!(window.songPaperEditContext?.songId===songId && document.querySelector('#song-live-editor:not([hidden])')) ||
      !!(active?.matches('input,textarea,select') && active.closest('.song-phrase-editor, .song-properties, .song-live-editor')) ||
      !!document.querySelector('#song-review-audio')?.matches(':not([data-idle]),[data-session],[data-loading],[data-sheet-hold]') ||
      !!document.querySelector('#song-live-edit-audio')?.matches('[data-phrase-id],[data-loading]');
  }
  function stop() {
    audioRequest++;
    if (stopPlayback) stopPlayback();
  }
  function bind(row, getCurrent, accept) {
    const player = document.getElementById('song-review-audio');
    if (!player) return;
    const play = document.getElementById('song-sheet-play');
    const instrumentalPlay = document.getElementById('song-sheet-play-instrumental');
    const speedDown = document.getElementById('song-speed-down');
    const speedUp = document.getElementById('song-speed-up');
    const speedLabel = document.getElementById('song-speed-label');
    const stopButton = document.getElementById('song-sheet-stop');
    const previousButton = document.getElementById('song-sheet-previous');
    const nextButton = document.getElementById('song-sheet-next');
    const full = document.getElementById('song-play-full');
    const status = document.getElementById('song-play-status');
    const sheetStatus = document.getElementById('song-sheet-play-status');
    let fullData = new Map(), followSheet = true, variant = 'original';
    let livePlay = null, livePhraseId = '', liveAudio = null, liveSaving = false;
    let liveLoopTimer = null, liveLoopEnabled = false;
    let speed = speedBySong.get(row.song_id) || 1;
    let pitch = pitchBySong.has(row.song_id) ? pitchBySong.get(row.song_id) : (SongSheet.offset(row) || 0);
    pitchBySong.set(row.song_id, pitch);
    spacePlaybackHandler = () => togglePlayback?.(true);
    if (!spacePlaybackBound && typeof document !== 'undefined') {
      document.addEventListener('keydown', event => {
        if (event.code !== 'Space' || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
        const target = event.target;
        if (target?.matches?.('input, textarea, select, [contenteditable="true"]') || target?.closest?.('button, a, [role="button"]')) return;
        const audio = document.getElementById('song-review-audio');
        if (!audio?.isConnected || !spacePlaybackHandler) return;
        event.preventDefault();
        Promise.resolve(spacePlaybackHandler()).catch(() => {});
      }, true);
      spacePlaybackBound = true;
    }
    const speedSteps = [0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.4, 1.5];
    const speedText = value => `×${Number(value).toFixed(2)}`;
    function applyAudioSettings() {
      for (const audio of [player, liveAudio]) {
        if (!audio) continue;
        audio.playbackRate = speed;
        if ('preservesPitch' in audio) audio.preservesPitch = true;
        if ('webkitPreservesPitch' in audio) audio.webkitPreservesPitch = true;
      }
      if (speedLabel) {
        speedLabel.textContent = speedText(speed);
        speedLabel.setAttribute('aria-label', `Скорость воспроизведения: ${speedText(speed)}`);
      }
      if (speedDown) speedDown.disabled = speed <= speedSteps[0];
      if (speedUp) speedUp.disabled = speed >= speedSteps.at(-1);
    }
    const message = text => { status.textContent = text; if (sheetStatus) sheetStatus.textContent = text; };
    function sync() {
      if (!player.isConnected) return;
      const loading = !!player.dataset.loading, playing = !player.paused && !player.ended;
      if (play) {
        const originalPlaying = playing && variant === 'original';
        const title = loading ? 'Подготавливаем запись для прослушивания' : !row.capabilities?.audio ? 'Для прослушивания добавьте аудиозапись' : originalPlaying ? 'Приостановить воспроизведение оригинальной записи' : player.dataset.session && !player.dataset.phraseId && variant === 'original' ? 'Продолжить воспроизведение оригинальной записи с места паузы' : 'Проиграть всю песню в исходной тональности записи';
        play.innerHTML = SongSheet.icon(originalPlaying ? 'pause' : 'play');
        play.title = title; play.setAttribute('aria-label',title);
        play.setAttribute('aria-busy',String(loading));
        play.disabled = loading || !row.capabilities?.audio;
      }
      if (instrumentalPlay) {
        const instrumentalPlaying = playing && variant === 'instrumental';
        const title = loading && variant === 'instrumental' ? 'Подготавливаем минусовку' : row.karaoke?.state !== 'ready' ? 'Минусовка ещё не готова' : instrumentalPlaying ? 'Приостановить воспроизведение минусовки' : 'Проиграть всю песню с минусовкой';
        instrumentalPlay.innerHTML = SongSheet.icon(instrumentalPlaying ? 'pause' : 'karaoke');
        instrumentalPlay.title = title; instrumentalPlay.setAttribute('aria-label', title);
        instrumentalPlay.setAttribute('aria-busy',String(loading && variant === 'instrumental'));
        instrumentalPlay.disabled = loading || row.karaoke?.state !== 'ready';
      }
      if (livePlay) {
        const livePlaying = !!livePhraseId && liveAudio && !liveAudio.paused && !liveAudio.ended;
        const liveLoading = !!liveAudio?.dataset.loading;
        const liveTitle = livePlaying ? 'Приостановить проигрывание текущей фразы' : 'Проиграть текущую фразу, не выходя из редактора';
        livePlay.innerHTML = SongSheet.icon(livePlaying ? 'pause' : 'play');
        livePlay.title = liveTitle; livePlay.setAttribute('aria-label', liveTitle);
        livePlay.disabled = liveSaving || liveLoading || !row.capabilities?.audio;
      }
      if (stopButton) stopButton.disabled = !(loading || player.dataset.session || playing || player.currentTime > 0);
      for (const [button,direction] of [[previousButton,-1],[nextButton,1]]) {
        if(button) button.disabled = loading || !player.dataset.session || !!player.dataset.phraseId ||
          SongSheet.lineTarget(SongSheet.lineStarts(row.song_id),player.currentTime*1000,direction)===null;
      }
      full.disabled = loading || !row.capabilities?.audio;
      window.onSongPlayback?.(row.song_id,{loading,playing,variant,active:loading || !!player.dataset.session || playing || player.currentTime>0});
      applyAudioSettings();
    }
    function clearHighlights() {
      document.querySelectorAll('.song-phrase.playing, .song-word.current-word').forEach(el => el.classList.remove('playing','current-word'));
    }
    stopPlayback = () => {
      player.pause();
      player.dataset.sheetStopped = '1';
      player.dataset.sheetHold = '1';
      if (player.src) player.currentTime = 0;
      delete player.dataset.loading; delete player.dataset.session; delete player.dataset.livePhraseId;
      delete player.dataset.clipStart; delete player.dataset.phraseId;
      if (liveAudio) {
        liveAudio.pause();
        liveAudio.currentTime = 0;
        if (liveLoopTimer) clearTimeout(liveLoopTimer);
        liveLoopTimer = null;
        liveLoopEnabled = false;
        delete liveAudio.dataset.loading;
        delete liveAudio.dataset.phraseId;
      }
      livePhraseId = '';
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
    player.addEventListener('ended', () => { delete player.dataset.session; delete player.dataset.phraseId; delete player.dataset.clipStart; player.dataset.idle = '1'; clearHighlights(); SongSheet.updatePlayback(row.song_id,-1,false); message(''); sync(); window.onSongPlayback?.(row.song_id,{playing:false,active:false,ended:true}); });
    player.addEventListener('error', () => { delete player.dataset.session; delete player.dataset.loading; player.dataset.idle = '1'; message('Не удалось воспроизвести запись. Попробуйте ещё раз.'); sync(); });
    document.getElementById('song-review-loop').onchange = e => { player.loop = e.target.checked; };
    async function playFull(restart=false, autoplay=true, seekTo=null) {
      const request = ++audioRequest;
      player.pause(); player.dataset.loading = '1'; sync();
      message(fullData ? '' : 'Подготавливаем запись…');
      try {
        const audioKey = `${variant}:${pitch}`;
        if (!fullData.has(audioKey)) {
          const clip = await core.call('song_full_audio',{song_id:row.song_id,variant,pitch_steps:pitch});
          if (request !== audioRequest || !player.isConnected) return;
          fullData.set(audioKey, clip.data);
        }
        if (request !== audioRequest || !player.isConnected) return;
        const source = fullData.get(audioKey);
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
        if (restart || (player.ended && seekTo === null)) player.currentTime = 0;
        player.dataset.clipStart = '0'; delete player.dataset.phraseId;
        delete player.dataset.livePhraseId; livePhraseId = '';
        applyAudioSettings();
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
        if (autoplay) await playAudio(player);
        if (request === audioRequest) message('');
      } catch (error) { if (request === audioRequest) message(error.message || 'Не удалось воспроизвести запись'); }
      finally { if (request === audioRequest && player.isConnected) { delete player.dataset.loading; sync(); } }
    }
    const variantSelect = document.getElementById('song-play-variant');
    async function switchFullVariant(next) {
      if (next === 'instrumental' && row.karaoke?.state !== 'ready') return;
      const previous = variant;
      const active = !!player.dataset.session && !player.dataset.phraseId;
      const wasPlaying = active && !player.paused && !player.ended;
      const position = active && Number.isFinite(player.currentTime) ? player.currentTime : null;
      variant = next;
      if (variantSelect) variantSelect.value = next;
      followSheet = true;
      // Clicking the already selected transport button pauses/resumes in place.
      if (previous === next && active && wasPlaying) { player.pause(); return; }
      // A direct press on either transport button is an explicit request to
      // start/resume playback.  The old code passed `wasPlaying` here, which
      // meant an idle player only loaded the source and never started it.
      return playFull(false, true, position);
    }
    full.onclick = () => switchFullVariant('original');
    if (instrumentalPlay) instrumentalPlay.onclick = () => switchFullVariant('instrumental');
    setPlaybackVariant = value => {
      if (!variantSelect || (value === 'instrumental' && row.karaoke?.state !== 'ready')) return false;
      variant=variantSelect.value=value;
      return true;
    };
    selectPlaybackVariant = (value, follow=true) => {
      if (!setPlaybackVariant(value)) return Promise.resolve();
      followSheet=follow;
      const position=player.dataset.session && !player.dataset.phraseId ? player.currentTime : null;
      const wasPlaying=!player.paused&&!player.ended;
      return playFull(false,true,position);
    };
    if (variantSelect) variantSelect.onchange = async () => {
      const wasPlaying = !player.paused && !player.ended;
      const position = player.dataset.session && !player.dataset.phraseId ? player.currentTime : null;
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
    const changeSpeed = delta => {
      const index = speedSteps.findIndex(value => Math.abs(value-speed)<0.001);
      speed = speedSteps[Math.max(0,Math.min(speedSteps.length-1,index + delta))];
      speedBySong.set(row.song_id,speed); applyAudioSettings();
    };
    speedDown?.addEventListener('click',()=>changeSpeed(-1));
    speedUp?.addEventListener('click',()=>changeSpeed(1));
    applyAudioSettings();
    setPitchPlayback = (songId, value) => {
      if (songId !== row.song_id) return;
      pitch = Math.max(-12,Math.min(12,Number(value)||0));
      pitchBySong.set(songId,pitch);
      if(liveTarget&&!liveEditor.hidden) {
        liveChordDraft=liveChordDraft.map((draft,index)=>({...readLiveChord(draft,index),original_start_ms:draft.original_start_ms}));
        liveKeyShift=pitch;renderLiveChords();
      }
      if (player.dataset.session && !player.dataset.phraseId) {
        const position=player.currentTime;
        playFull(false,!player.paused,position);
      }
    };
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
    livePlay=document.getElementById('song-live-edit-play');
    liveAudio=document.getElementById('song-live-edit-audio');
    const liveMode=document.getElementById('song-live-edit-mode');
    const liveText=document.getElementById('song-live-edit-text');
    const livePreview=document.getElementById('song-live-edit-preview');
    const livePreviewChords=document.getElementById('song-live-edit-preview-chords');
    const liveChords=document.getElementById('song-live-chords');
    const liveStatus=document.getElementById('song-live-edit-status');
    const livePrevious=document.getElementById('song-live-edit-previous');
    const liveNext=document.getElementById('song-live-edit-next');
    const liveClose=document.getElementById('song-live-edit-close');
    const liveOriginal=document.getElementById('song-live-edit-play-original');
    let liveTarget=null, livePhraseText='', liveChordDraft=[], liveSavedSnapshot='';
    let livePreviewRequest=0;
    let liveKeyShift=window.SongSheet.offset(row);
    let livePreviewText='', livePreviewSourceText='', livePreviewEvents=[];
    function liveSnapshot() {
      return JSON.stringify({text:liveText?.value,reviewed:document.getElementById('song-live-reviewed')?.checked,
        chords:liveChordDraft.map((draft,index)=>({draft,
          name:liveChords.querySelector(`[data-live-chord="${index}"] [data-live-chord-name]`)?.value,
          time:liveChords.querySelector(`[data-live-chord="${index}"] [data-live-chord-time]`)?.value}))});
    }
    function adjacentPhrase(direction) {
      const phrases=getCurrent()?.phrases||[],ids=new Set(liveTarget?.phraseIds||[]);
      const indexes=phrases.flatMap((p,i)=>ids.has(p.phrase_id)?[i]:[]);
      if(!indexes.length)return null;
      for(let i=(direction<0?Math.min(...indexes):Math.max(...indexes))+direction;
          i>=0&&i<phrases.length;i+=direction) {
        if(phrases[i].text?.trim())return phrases[i];
      }
      return null;
    }
    function syncLiveNavigation() {
      if(livePrevious)livePrevious.disabled=liveSaving||!adjacentPhrase(-1);
      if(liveNext)liveNext.disabled=liveSaving||!adjacentPhrase(1);
      if(liveClose)liveClose.disabled=liveSaving;
    }
    function stopLivePhrase() {
      ++audioRequest;
      liveAudio?.pause();
      if(liveAudio) { delete liveAudio.dataset.loading;delete liveAudio.dataset.phraseId; }
      livePhraseId='';
      clearLiveHighlights();
      sync();
    }
    function clearLiveHighlights() {
      document.querySelectorAll('.song-phrase.playing, .song-phrase .song-word.current-word').forEach(el => el.classList.remove('playing','current-word'));
      livePreviewChords?.querySelectorAll('.current-chord').forEach(el=>el.classList.remove('current-chord'));
      liveChords?.querySelectorAll('.is-playing,[data-live-chord-context] .current-word').forEach(el=>el.classList.remove('is-playing','current-word'));
      livePreview?.classList.remove('song-live-preview-word','current-word');
      livePreview?.querySelectorAll('.song-live-preview-word.current-word').forEach(el=>el.classList.remove('current-word'));
    }
    function updateLivePhraseWords(position) {
      const ids=new Set(liveTarget?.phraseIds||[]);
      document.querySelectorAll('.song-phrase[data-phrase-id]').forEach(el => {
        const active=ids.has(el.dataset.phraseId);
        const phrase=getCurrent()?.phrases?.find(item=>item.phrase_id===el.dataset.phraseId);
        el.classList.toggle('playing',active && !!phrase && phrase.start_ms<=position && position<phrase.end_ms);
        el.querySelectorAll('[data-word-start]').forEach(word=>word.classList.toggle('current-word',
          active && position>=Number(word.dataset.wordStart) && position<Number(word.dataset.wordEnd)));
      });
      // Phrase playback is local to the editor: update highlights without
      // moving the full-song sheet or switching the main player to the song.
      SongSheet.updatePlayback(row.song_id,position,false);
      updateLiveChordPlayback(position);
    }
    async function playLivePhrase() {
      if (!liveTarget || !liveAudio) return;
      if (livePhraseId && liveAudio.dataset.phraseId===livePhraseId && !liveAudio.paused && !liveAudio.ended) {
        liveLoopEnabled = false;
        liveAudio.pause();
        return;
      }
      if (liveLoopTimer) {
        clearTimeout(liveLoopTimer);
        liveLoopTimer = null;
        liveLoopEnabled = true;
        liveAudio.currentTime = 0;
        await playAudio(liveAudio).catch(()=>{});
        if (liveStatus) liveStatus.textContent='Фраза воспроизводится по кругу…';
        return;
      }
      const request=++audioRequest, phraseId=liveTarget.phraseIds?.[0]||liveTarget.phraseId;
      player.pause();
      liveAudio.pause();
      liveAudio.dataset.loading='1';
      sync();
      if (liveStatus) liveStatus.textContent='Готовим фразу…';
      try {
        const clip=await core.call('song_phrase_audio',{song_id:row.song_id,phrase_id:phraseId,pitch_steps:pitch});
        if(request!==audioRequest || !liveAudio.isConnected) return;
        liveAudio.src=clip.data; liveAudio.currentTime=0;
        applyAudioSettings();
        liveAudio.dataset.clipStart=clip.start_ms; liveAudio.dataset.phraseId=liveTarget.phraseId;
        livePhraseId=liveTarget.phraseId;
        liveLoopEnabled = true;
        liveAudio.ontimeupdate=()=>{
          const position=Number(clip.start_ms)+liveAudio.currentTime*1000;
          updateLivePhraseWords(position); sync();
        };
        liveAudio.onended=()=>{
          clearLiveHighlights();
          if (!liveLoopEnabled || request !== audioRequest || !liveAudio.isConnected) {
            delete liveAudio.dataset.phraseId; livePhraseId='';
            if (liveStatus) liveStatus.textContent='Фраза закончилась. Можно запустить её снова.';
            sync();
            return;
          }
          if (liveStatus) liveStatus.textContent='Пауза 1 секунда — повтор фразы…';
          sync();
          liveLoopTimer=setTimeout(async()=>{
            liveLoopTimer=null;
            if (!liveLoopEnabled || request !== audioRequest || !liveAudio.isConnected) return;
            liveAudio.currentTime=0;
            try { await playAudio(liveAudio); }
            catch(error) { liveLoopEnabled=false; if (liveStatus) liveStatus.textContent=error.message||'Не удалось повторить фразу'; sync(); }
          },1000);
        };
        liveAudio.onpause=()=>sync();
        await playAudio(liveAudio);
        updateLivePhraseWords(Number(clip.start_ms)+liveAudio.currentTime*1000);
        if (liveStatus) liveStatus.textContent='Фраза воспроизводится…';
      } catch(error) {
        if(request===audioRequest && liveStatus) liveStatus.textContent=error.message||'Не удалось воспроизвести фразу';
      } finally {
        if(request===audioRequest && liveAudio.isConnected) { delete liveAudio.dataset.loading; sync(); }
      }
    }
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
    function readLiveChord(chord,index) {
      const element=liveChords.querySelector(`[data-live-chord="${index}"]`);
      const displayName=element?.querySelector('[data-live-chord-name]')?.value.trim()??window.SongSheet.transpose(chord.name,liveKeyShift);
      const name=displayName===window.SongSheet.transpose(chord.name,liveKeyShift)?chord.name:window.SongSheet.transpose(displayName,-liveKeyShift);
      const timeValue=element?.querySelector('[data-live-chord-time]')?.value;
      const seconds=timeValue===''?NaN:Number(timeValue);
      const event={...chord,name,start_ms:Number.isFinite(seconds)?Math.round(seconds*1000):chord.start_ms};
      if(event.start_ms!==Number(chord.original_start_ms??chord.start_ms)) {
        // An old explicit word anchor cannot follow a newly selected time.
        delete event.anchor;
        delete event.end_ms;
      }
      delete event.original_start_ms;
      return event;
    }
    function displayedPosition(chord) {
      for(const line of getCurrent()?.guitar_sheet||[]) {
        const mark=line.chords?.find(c=>!c.continued&&c.start_ms===chord.start_ms&&c.name===chord.name);
        if(!mark)continue;
        const span=line.source_spans?.find(s=>s.display_char_start<=mark.char_index&&mark.char_index<s.display_char_end);
        if(span)return {phrase_id:span.phrase_id,char_index:span.phrase_char_start+mark.char_index-span.display_char_start,quality:mark.anchor_quality};
        return {phrase_id:line.source_phrase_ids?.[0]||line.phrase_id,char_index:mark.char_index,quality:mark.anchor_quality};
      }
      return null;
    }
    function captureLiveChords() {
      liveChordDraft=liveChordDraft.map((draft,index)=>({...readLiveChord(draft,index),original_start_ms:draft.original_start_ms}));
    }
    async function updateLivePreview() {
      if(!livePreview||!liveTarget)return;
      const request=++livePreviewRequest;
      let text=livePhraseText;
      if(liveMode.value==='phrase') text=liveText.value;
      else if(liveMode.value==='word') {
        const start=liveTarget.charStart,end=liveTarget.charEnd;
        text=livePhraseText.slice(0,start)+liveText.value+livePhraseText.slice(end);
      }
      text=(text||'').trim();
      if(!livePreviewChords)return;
      const events=liveChordDraft.map((chord,index)=>({...readLiveChord(chord,index),draft_index:index}));
      const positions=liveChordDraft.map(displayedPosition);
      renderLivePreview(text,events,positions);
      const old=phraseChords(targetPhrase()),keep=(getCurrent()?.chords||[]).filter(c=>!old.includes(c));
      try {
        await new Promise(resolve=>setTimeout(resolve,100));
        if(request!==livePreviewRequest||!liveEditor.isConnected)return;
        const result=await core.call('song_preview_chords',{song_id:row.song_id,chords:[...keep,...events],
          ...(liveTarget.phraseIds.length===1?{phrase_id:liveTarget.phraseId,text}:{})});
        if(request!==livePreviewRequest||!liveEditor.isConnected)return;
        renderLivePreview(text,events,events.map((_,index)=>result.positions?.[keep.length+index]||null));
      } catch(error) {
        if(request===livePreviewRequest)liveChords.querySelectorAll('[data-live-chord-context]').forEach(el=>{
          el.textContent='Положение не обновлено';el.title=error.message||'Не удалось получить положение аккорда';
        });
      }
    }
    function renderLivePreview(text,drafts,positions) {
      const events=drafts.map((chord,index)=>{
        const position=positions[index];
        const span=liveTarget.sourceSpans?.find(s=>s.phrase_id===position?.phrase_id&&s.phrase_char_start<=position.char_index&&position.char_index<s.phrase_char_end);
        const at=span?span.display_char_start+position.char_index-span.phrase_char_start:
          position?.phrase_id===liveTarget.phraseId?position.char_index:null;
        return {...chord,...window.SongSheet.voicingAtShift(chord,liveKeyShift),name:window.SongSheet.transpose(chord.name,liveKeyShift),char_index:at,position_quality:position?.quality};
      });
      for(const chord of events) {
        const context=liveChords.querySelector(`[data-live-chord="${chord.draft_index}"] [data-live-chord-context]`);
        if(!context)continue;
        if(chord.char_index===null) {
          context.innerHTML=`<span class="song-live-context-active"><b>${esc(chord.name)}</b><span>Без слов</span></span>`;
          context.title='В этой позиции нет привязки к словам';continue;
        }
        const start=Math.max(0,Math.min(text.length-1,chord.char_index));
        const next=events.filter(c=>c.start_ms>chord.start_ms&&c.char_index>start).sort((a,b)=>a.start_ms-b.start_ms)[0];
        const end=next?Math.min(text.length,next.char_index):text.length;
        context.innerHTML=`<span>${esc(text.slice(0,start))}</span><span class="song-live-context-active"><b>${esc(chord.name)}</b><mark>${esc(text.slice(start,end))}</mark></span><span>${esc(text.slice(end))}</span>`;
        context.title=String(chord.position_quality).startsWith('estimated')?'Приблизительное положение по времени фразы':'Положение аккорда в тексте';
        const active=context.querySelector('.song-live-context-active');
        context.scrollLeft=Math.max(0,active.offsetLeft-context.offsetLeft-context.clientWidth/3);
      }
      const width=Math.max(42,text.length+Math.max(1,...events.map(chord=>chord.name.length)));
      const layout=window.SongSheet.layoutChordLine({text,chords:events.filter(c=>c.char_index!==null)},0,width);
      livePreviewText=layout.text||'…';
      livePreviewSourceText=text;
      livePreviewEvents=events;
      livePreview.textContent=livePreviewText;
      const placed=layout.chords;
      let chordLine='',cursor=0;
      for(const chord of placed) {
        const at=Math.max(cursor,chord.at);
        const evidence={...chord,song_id:row.song_id};
        const tooltip=window.SongSheet.chordTooltip(chord.name,evidence);
        const shape=window.SongSheet.chordFingering(chord.name,evidence);
        chordLine+=esc(' '.repeat(at-cursor))+`<span class="song-chord-tip" title="${esc(tooltip)}" role="button" tabindex="0" aria-label="${esc(tooltip)}" aria-expanded="false" data-chord-name="${esc(chord.name)}" data-chord-fingering="${esc(shape)}" data-live-chord-index="${chord.draft_index}" data-start-ms="${chord.start_ms}"${Number.isFinite(Number(chord.end_ms))?` data-end-ms="${chord.end_ms}"`:''} data-song-id="${esc(row.song_id||'')}">${esc(chord.name)}</span>`;
        cursor=at+chord.name.length;
      }
      livePreviewChords.innerHTML=chordLine;
      livePreviewChords.hidden=!chordLine;
      livePreviewChords.querySelectorAll('[data-live-chord-index]').forEach(trigger=>{
        const index=Number(trigger.dataset.liveChordIndex),draft=liveChordDraft[index];
        trigger.applySongChord=async({symbol,shape})=>{
          if(liveChordDraft[index]!==draft)throw Error('Аккорды фразы изменились. Откройте нужную смену снова.');
          const input=liveChords.querySelector(`[data-live-chord="${index}"] [data-live-chord-name]`);
          if(!input)throw Error('Редактор фразы уже закрыт.');
          input.value=symbol;
          Object.assign(draft,{name:window.SongSheet.transpose(symbol,-liveKeyShift),voicing_fingering:shape,voicing_key_shift:liveKeyShift,voicing_source:'manual'});
          delete draft.voicing_probability;
          input.title=window.SongSheet.chordTooltip(symbol,draft);
          updateLivePreview();
          if(!await saveLiveEdit())throw Error(liveStatus.textContent);
        };
      });
      if(liveAudio&&!liveAudio.paused&&liveAudio.dataset.clipStart)
        updateLiveChordPlayback(Number(liveAudio.dataset.clipStart)+liveAudio.currentTime*1000);
    }
    function updateLiveChordPlayback(position) {
      const active=livePreviewEvents.filter(chord=>{
        const start=Number(chord.start_ms),end=Number(chord.end_ms);
        return Number.isFinite(start)&&start<=position&&(!Number.isFinite(end)||position<end);
      }).sort((a,b)=>Number(b.start_ms)-Number(a.start_ms))[0];
      livePreviewChords?.querySelectorAll('[data-live-chord-index]').forEach(el=>
        el.classList.toggle('current-chord',!!active&&Number(el.dataset.liveChordIndex)===active.draft_index));
      liveChords?.querySelectorAll('[data-live-chord]').forEach(rowEl=>{
        const chord=livePreviewEvents.find(item=>item.draft_index===Number(rowEl.dataset.liveChord));
        const playing=!!chord&&chord===active;
        rowEl.classList.toggle('is-playing',playing);
        rowEl.querySelector('[data-live-chord-context] mark')?.classList.toggle('current-word',playing);
      });
      const preview=livePreview;
      if(!preview)return;
      const start=active?.char_index;
      if(!Number.isInteger(start)||start<0||start>=livePreviewSourceText.length) { preview.textContent=livePreviewSourceText;return; }
      const next=livePreviewEvents.filter(chord=>chord.start_ms>active.start_ms&&Number.isInteger(chord.char_index)&&chord.char_index>start)
        .sort((a,b)=>Number(a.start_ms)-Number(b.start_ms))[0];
      const end=next?Math.min(livePreviewSourceText.length,next.char_index):livePreviewSourceText.length;
      preview.innerHTML=`${esc(livePreviewSourceText.slice(0,start))}<mark class="song-live-preview-word current-word">${esc(livePreviewSourceText.slice(start,end))}</mark>${esc(livePreviewSourceText.slice(end))}`;
    }
    function renderLiveChords() {
      liveChords.innerHTML=liveChordDraft.map((chord,index)=>{
        const rangeStart=Number(liveTarget?.startMs)||0, rangeEnd=Number(liveTarget?.endMs)||rangeStart;
        const original=Number(chord.start_ms)||0;
        const min=Math.min(rangeStart,original), max=Math.max(rangeEnd,original);
        return `<div class="song-live-chord" data-live-chord="${index}">
        <label class="f">Аккорд<input data-live-chord-name value="${esc(window.SongSheet.transpose(chord.name||'',liveKeyShift))}" maxlength="24" placeholder="Am, D7" aria-label="Аккорд ${index+1}"></label>
        <label class="f">Секунда<input data-live-chord-time type="number" min="${(min/1000).toFixed(3)}" max="${(max/1000).toFixed(3)}" step="0.001" value="${(original/1000).toFixed(3)}" aria-label="Время аккорда ${index+1}, в пределах фразы"></label>
        <span class="song-live-chord-context" data-live-chord-context aria-label="Слова аккорда ${index+1}"></span>
        <button type="button" class="btn small" data-live-chord-remove="${index}" aria-label="Убрать аккорд ${index+1}" title="Убрать аккорд">×</button>
      </div>`;
      }).join('') || '<p class="hint">В этой фразе пока нет аккордов.</p>';
      liveChords.querySelectorAll('[data-live-chord-remove]').forEach(button=>button.onclick=()=>{
        captureLiveChords();
        liveChordDraft.splice(Number(button.dataset.liveChordRemove),1);renderLiveChords();
      });
      liveChords.querySelectorAll('[data-live-chord-name]').forEach((input,index)=>{
        const chord=liveChordDraft[index]||{};
        input.title=window.SongSheet?.chordTooltip(input.value,chord)||'Название аккорда';
        input.addEventListener('input',()=>{
          for(const key of ['voicing_fingering','voicing_probability','voicing_key_shift','voicing_source','fingering','voicing'])delete chord[key];
          input.title=window.SongSheet?.chordTooltip(input.value,chord)||'Название аккорда';updateLivePreview();
        });
      });
      liveChords.querySelectorAll('[data-live-chord-time]').forEach(input=>input.addEventListener('input',updateLivePreview));
      updateLivePreview();
    }
    function openPhraseEditor(phraseId,mode='phrase',displayLineId='') {
      if(liveSaving)return;
      const current=getCurrent();
      const displayLine=mode==='phrase'&&displayLineId
        ? current?.guitar_sheet?.find(line=>line.phrase_id===displayLineId&&line.text&&line.source_spans?.length>1)
        : null;
      const sourcePhraseIds=displayLine?.source_spans?.map(span=>span.phrase_id).filter((id,index,all)=>id&&all.indexOf(id)===index)||[phraseId];
      const phrase=current?.phrases?.find(item=>item.phrase_id===sourcePhraseIds[0]);
      if(!phrase) return;
      player.pause();
      stopLivePhrase();
      const sourcePhrases=sourcePhraseIds.map(id=>current.phrases.find(item=>item.phrase_id===id)).filter(Boolean);
      liveTarget={phraseId:phrase.phrase_id,phraseIds:sourcePhraseIds,displayLineId:displayLine?.phrase_id||'',
        text:displayLine?.text||phrase.text,charStart:0,charEnd:(displayLine?.text||phrase.text).length,
        sourceSpans:displayLine?.source_spans||[],
        startMs:Math.min(...sourcePhrases.map(item=>Number(item.start_ms)||0)),
        endMs:Math.max(...sourcePhrases.map(item=>Number(item.end_ms)||0))};
      liveMode.value=mode;
      liveKeyShift=window.SongSheet.offset(current);
      livePhraseText=liveTarget.text;
      liveText.value=liveTarget.text;
      document.getElementById('song-live-edit-target').textContent=`${(liveTarget.startMs/1000).toFixed(3)}–${(liveTarget.endMs/1000).toFixed(3)} с · ${mode==='chords'?'Аккорды фразы':displayLine?'Текст строки':'Текст фразы'}`;
      liveEditor.classList.toggle('song-live-editor--chords',mode==='chords');
      document.querySelector('#song-live-editor .song-live-editor-head b').textContent=mode==='chords'?'Правка аккордов фразы':'Правка фразы';
      document.getElementById('song-live-edit-save').textContent='Сохранить';
      document.getElementById('song-live-edit-save').title='Сохранить текст и аккорды, оставаясь в редакторе фразы';
      document.getElementById('song-live-reviewed').checked=sourcePhrases.every(item=>item.review_state==='verified');
      liveChordDraft=phraseChords(phrase).map(c=>({...structuredClone(c),original_start_ms:Number(c.start_ms)||0}));
      renderLiveChords();updateLivePreview();liveStatus.textContent='Песня приостановлена. Правки сохраняются в карточке песни.';
      liveEditor.hidden=false;
      liveSavedSnapshot=liveSnapshot();
      syncLiveNavigation();
      livePhraseId=liveTarget.phraseId;
      delete player.dataset.livePhraseId;
      if (livePlay) { livePlay.innerHTML=SongSheet.icon('play'); livePlay.title='Проиграть текущую фразу, не выходя из редактора'; livePlay.setAttribute('aria-label','Проиграть текущую фразу, не выходя из редактора'); livePlay.disabled=!row.capabilities?.audio; }
      liveEditor.scrollIntoView({block:'nearest',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
      if(mode==='phrase') liveText.focus();
      else liveChords.querySelector('[data-live-chord-name]')?.focus();
    }
    async function switchPhrase(phraseId,mode='phrase',displayLineId='') {
      if(liveSaving)return;
      if(liveTarget&&!liveEditor.hidden&&liveSnapshot()!==liveSavedSnapshot&&!await saveLiveEdit())return;
      openPhraseEditor(phraseId,mode,displayLineId);
    }
    window.songPaperEditContext={songId:row.song_id,open:switchPhrase};
    if(!window.songPaperEditListener && document.addEventListener) {
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
      captureLiveChords();
      liveChordDraft.push({name:'',start_ms:liveTarget?.startMs ?? phrase.start_ms,original_start_ms:liveTarget?.startMs ?? phrase.start_ms,
        anchor:liveTarget?{kind:'manual',phrase_id:phrase.phrase_id,grapheme_index:liveTarget.charStart}:null,
        method:'manual',verified:true});renderLiveChords();
    });
    livePlay?.addEventListener('click',event=>{ event.preventDefault(); event.stopPropagation(); playLivePhrase(); });
    const playEditorVariant = async (value, button) => {
      if(liveSaving || !row.capabilities?.audio || (value==='instrumental' && row.karaoke?.state!=='ready')) return;
      button.disabled=true;
      try {
        await selectPlaybackVariant(value);
        if(liveStatus) liveStatus.textContent=value==='instrumental'
          ? 'Проигрывается минусовка. Редактор остаётся открыт.'
          : 'Проигрывается оригинальная запись. Редактор остаётся открыт.';
      } catch(error) {
        if(liveStatus) liveStatus.textContent=error.message||'Не удалось запустить дорожку';
      } finally { button.disabled=false; sync(); }
    };
    liveOriginal?.addEventListener('click',event=>{ event.preventDefault(); event.stopPropagation(); playEditorVariant('original',liveOriginal); });
    for(const [button,direction] of [[livePrevious,-1],[liveNext,1]]) {
      button?.addEventListener('click',()=>{
        const target=adjacentPhrase(direction);
        if(target)switchPhrase(target.phrase_id,liveMode.value==='chords'?'chords':'phrase');
      });
    }
    liveClose?.addEventListener('click',async()=>{
      if(liveSaving)return;
      if(liveSnapshot()!==liveSavedSnapshot&&!window.confirm('Закрыть редактор без сохранения последних изменений?'))return;
      const startMs=Number(liveTarget?.startMs);
      stopLivePhrase();liveEditor.hidden=true;
      if(row.capabilities?.audio && Number.isFinite(startMs)) {
        followSheet=true;
        await playFull(false,true,Math.max(0,Math.ceil(startMs))/1000);
      }
    });
    async function saveLiveEdit(){
      if(liveSaving)return false;
      liveSaving=true;syncLiveNavigation();sync();
      const button=document.getElementById('song-live-edit-save');button.disabled=true;liveStatus.textContent='Сохраняем…';
      const controls=[...liveEditor.querySelectorAll('input,textarea,select,[data-live-chord-remove],#song-live-chord-add')];
      controls.forEach(control=>control.disabled=true);
      stopLivePhrase();
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
          const timeValue=rowElement?.querySelector('[data-live-chord-time]')?.value;
          const seconds=timeValue===''?NaN:Number(timeValue);
          if(!name)throw Error(`Введите название аккорда ${index+1}`);
          if(!Number.isFinite(seconds)||seconds<0)throw Error(`Проверьте время аккорда ${index+1}`);
          const timeField=rowElement?.querySelector('[data-live-chord-time]');
          if(seconds<Number(timeField?.min)-0.0005||seconds>Number(timeField?.max)+0.0005)
            throw Error(`Время аккорда ${index+1} должно находиться в границах текущей фразы: ${formatSeconds(liveTarget.startMs)}–${formatSeconds(liveTarget.endMs)}`);
          const event=readLiveChord(draft,index);
          // Text edits may have invalidated an old anchor in the store.
          const latest=oldPhraseEvents.find(c=>c.start_ms===draft.original_start_ms);
          if(latest&&!latest.anchor)delete event.anchor;
          return {...event,verified:true,method:'manual'};
        });
        const signature=events=>JSON.stringify(events.map(({name,start_ms,anchor,voicing_fingering,voicing_key_shift})=>({name,start_ms,anchor,voicing_fingering,voicing_key_shift})));
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
        if(mode!=='word'&&!groupedLine&&phrase)liveText.value=phrase.text;
        liveTarget.text=mode==='word'?liveTarget.text:liveText.value.trim();
        liveChordDraft=phraseChords(phrase,current.chords||[]).map(c=>({...structuredClone(c),original_start_ms:Number(c.start_ms)||0}));
        renderLiveChords();
        liveSavedSnapshot=liveSnapshot();
        return true;
      } catch(error) { liveStatus.textContent=`${error.message||'Не удалось сохранить правку'}. Исправления оставлены в редакторе.`; return false; }
      finally { liveSaving=false;button.disabled=false;controls.forEach(control=>control.disabled=false);syncLiveNavigation();sync(); }
    }
    document.getElementById('song-live-edit-save')?.addEventListener('click',saveLiveEdit);
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
        // Phrase playback has its own local timeline.  Clear the full-song
        // session before loading it so line navigation cannot interpret the
        // clip position as a position in the complete recording.
        delete player.dataset.session;
        delete player.dataset.clipStart;
        delete player.dataset.phraseId;
        button.disabled = true; playStatus.textContent = 'Готовим фрагмент…';
        try {
          const clip = await core.call('song_phrase_audio', {song_id: row.song_id, phrase_id: pid, pitch_steps: pitch});
          if (request !== audioRequest || !player.isConnected) return;
          player.src = clip.data;
          applyAudioSettings();
          // A phrase is a new media timeline.  Safari may retain the old
          // currentTime after changing src; without an explicit reset the
          // clip starts in the middle (or immediately ends) after a full-song
          // or another phrase playback.
          player.currentTime = 0;
          if (typeof player.load === 'function') {
            await new Promise((resolve, reject) => {
              const done=()=>{cleanup();resolve();};
              const fail=()=>{cleanup();reject(new Error('Не удалось загрузить фрагмент'));};
              const cleanup=()=>{player.removeEventListener?.('loadedmetadata',done);player.removeEventListener?.('error',fail);};
              player.addEventListener?.('loadedmetadata',done,{once:true});
              player.addEventListener?.('error',fail,{once:true});
              player.load();
            });
          }
          if (request !== audioRequest || !player.isConnected) return;
          player.currentTime = 0;
          player.dataset.clipStart = clip.start_ms;
          player.dataset.phraseId = pid;
          player.ontimeupdate = () => {
            const position = clip.start_ms + player.currentTime * 1000;
            el.querySelectorAll('[data-word-start]').forEach(w => w.classList.toggle('current-word',
              position >= Number(w.dataset.wordStart) && position < Number(w.dataset.wordEnd)));
          };
          document.querySelectorAll('.song-phrase.playing').forEach(x => x.classList.remove('playing'));
          el.classList.add('playing');
          player.onended = () => {
            el.classList.remove('playing');
            delete player.dataset.session;
            delete player.dataset.phraseId;
            delete player.dataset.clipStart;
            player.dataset.idle = '1';
            sync();
          };
          await playAudio(player);
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
  return {html, playerHtml, bind, protectedView, stop, toggleFull:(follow=true)=>togglePlayback?.(follow), playVariant:(value,follow=true)=>selectPlaybackVariant?.(value,follow), setVariant:value=>setPlaybackVariant?.(value), setPitch:(songId,value)=>setPitchPlayback?.(songId,value)};
})();
