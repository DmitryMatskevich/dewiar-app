/* Вкладка «Песни»: библиотека, обработка и проверка текста/аккордов.
   Правки сохраняются с контролем ревизий; рабочие настройки не изменяются. */
(function () {
  "use strict";
  const state = { rows: [], query: "", status: "", current: null };
  let cardPlayback = {}, openRequest = 0, cardRequest = 0;
  const statusText = { queued: "Ожидает", recording: "Записываем", running: "В работе", needs_input: "Нужно выбрать", review: "Нужно проверить", ready: "Готово", partial: "Частично", failed: "Ошибка", cancelled: "Отменено", interrupted: "Прервано" };
  const stages = [["identify", "Определяем"], ["sources", "Ищем источники"], ["record", "Записываем"], ["lyrics", "Распознаём слова"], ["structure", "Размечаем части"], ["chords", "Подбираем аккорды"], ["review", "Проверяем"]];
  const esc = (v) => escH(v == null ? "" : v);
  const call = (name, args) => core.call(name, args || {});
  const $s = (id) => document.getElementById(id);
  const captureButton = (id, glyph, title) => `<button type="button" class="btn song-icon-btn song-capture-btn" id="${id}" title="${title}" aria-label="${title}">${SongSheet.icon(glyph)}</button>`;

  function currentStatus(row) { return statusText[row.status] || row.status || "Ожидает"; }
  function formatDate(value) {
    if (!value) return "";
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
  }
  function formatBytes(value) {
    const bytes=Number(value)||0;
    return bytes>=1024*1024 ? `${(bytes/1024/1024).toFixed(1)} МБ` : `${Math.max(1,Math.round(bytes/1024))} КБ`;
  }
  function stageIndex(row) {
    const i = stages.findIndex((x) => x[0] === row.stage);
    return i < 0 ? 0 : i;
  }
  function card(row) {
    const artist = row.artist ? `<div class="song-artist">${esc(row.artist)}</div>` : `<div class="song-artist">Исполнитель пока не определён</div>`;
    return `<article class="song-card" data-song-id="${esc(row.song_id)}">
      <div class="song-card-main" data-song-open="${esc(row.song_id)}" tabindex="0" role="button" aria-label="Открыть ${esc(row.title)}">
        <div class="song-title">${esc(row.title)}</div>${artist}
        <div class="song-meta"><span class="song-status" data-status="${esc(row.status)}">${esc(currentStatus(row))}</span><span>${esc(row.stage_label || "Готово к запуску")}</span>${row.updated_at ? `<span>${esc(formatDate(row.updated_at))}</span>` : ""}</div>
      </div>
      <div class="song-card-actions">
        <button class="btn song-icon-btn" data-song-play="${esc(row.song_id)}" title="Проиграть песню «${esc(row.title)}»" aria-label="Проиграть ${esc(row.title)}" ${row.capabilities?.audio ? '' : 'disabled'}>${SongSheet.icon('play')}</button>
        <button class="btn song-icon-btn" data-song-karaoke="${esc(row.song_id)}" title="Проиграть песню «${esc(row.title)}» в режиме караоке (минусовка)" aria-label="Караоке: ${esc(row.title)}" ${row.karaoke?.state === 'ready' ? '' : 'disabled'}>${SongSheet.icon('karaoke')}</button>
        <button class="btn song-icon-btn" data-song-stop="${esc(row.song_id)}" title="Остановить песню «${esc(row.title)}» и вернуться к началу; отменить подготовку записи" aria-label="Остановить ${esc(row.title)}" hidden>${SongSheet.icon('stop')}</button>
        <button class="btn song-icon-btn" data-song-open="${esc(row.song_id)}" title="Редактировать текст, аккорды и свойства песни «${esc(row.title)}»" aria-label="Редактировать ${esc(row.title)}">${SongSheet.icon('edit')}</button>
        <button class="btn song-icon-btn ghost" data-song-delete="${esc(row.song_id)}" title="Удалить карточку песни «${esc(row.title)}»; перед удалением появится подтверждение" aria-label="Удалить ${esc(row.title)}">${SongSheet.icon('trash')}</button>
      </div>
    </article>`;
  }
  function renderList() {
    const list = $s("songs-list"), empty = $s("songs-empty");
    if (!list || !empty) return;
    list.innerHTML = state.rows.map(card).join("");
    updateCardPlayback();
    empty.hidden = state.rows.length > 0;
    list.hidden = state.rows.length === 0;
    list.querySelectorAll("[data-song-open]").forEach((el) => {
      el.addEventListener("click", () => openSong(el.dataset.songOpen));
      if (el.tagName !== "BUTTON") el.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openSong(el.dataset.songOpen); } });
    });
    list.querySelectorAll('[data-song-play]').forEach(el=>el.addEventListener('click',()=>playCard(el.dataset.songPlay)));
    list.querySelectorAll('[data-song-karaoke]').forEach(el=>el.addEventListener('click',()=>playCard(el.dataset.songKaraoke,'instrumental')));
    list.querySelectorAll('[data-song-stop]').forEach(el=>el.addEventListener('click',stopCard));
    list.querySelectorAll("[data-song-delete]").forEach((el) => el.addEventListener("click", async (event) => {
      event.stopPropagation();
      const row = state.rows.find((x) => x.song_id === el.dataset.songDelete);
      if (!row || !confirm(`Удалить карточку «${row.title}»? Собственный аудиофайл удалится только отдельной очисткой.`)) return;
      try { await call("song_delete", { song_id: row.song_id }); await load(); if (state.current && state.current.song_id === row.song_id) closeDetail(); }
      catch (error) { toast(error.message || "Не удалось удалить песню", 4000, "bad"); }
    }));
  }
  function guitarHtml(row) { return SongSheet.sheetHTML(row); }
  function acceptSaved(saved) {
    if (state.current?.song_id !== saved.song_id) return;
    state.current = saved;
    state.rows = state.rows.map(r => r.song_id === saved.song_id ? saved : r)
      .filter(r => !state.status || (state.status === 'running' ? ['queued','recording','running'].includes(r.status) : r.status === state.status));
    renderList();
    const hint = document.querySelector('.songs-detail-head .hint');
    if (hint) hint.textContent = `${saved.artist || 'Исполнитель определяется по источникам'} · ${currentStatus(saved)}`;
    const step = document.querySelector('.song-step.on');
    if (step) step.innerHTML = `<b>${stageIndex(saved)+1}. ${esc(stages[stageIndex(saved)][1])}</b>${esc(saved.stage_label)}`;
    const sheet = document.getElementById('song-guitar-content');
    if (sheet) sheet.innerHTML = guitarHtml(saved);
  }
  function renderDetail(row) {
    const detail = $s("songs-detail");
    if (!detail) return;
    SongReview.stop();
    state.current = row;
    const active = stageIndex(row);
    detail.hidden = false;
    const instrumentalState = row.karaoke?.state;
    const instrumentalAction = row.capabilities?.audio ? `<button class="btn" id="song-instrumental" ${['queued','preparing','separating','checking'].includes(instrumentalState) ? 'disabled' : ''}>${['queued','preparing','separating','checking'].includes(instrumentalState) ? 'Готовим минусовку…' : instrumentalState === 'ready' ? 'Пересобрать минусовку' : 'Подготовить минусовку'}</button>${['queued','preparing','separating','checking'].includes(instrumentalState) ? '<button class="btn" id="song-instrumental-cancel">Отменить</button>' : ''}<span class="hint" id="song-instrumental-status">${instrumentalState === 'failed' ? esc(row.karaoke.error || 'Не удалось подготовить минусовку') : instrumentalState === 'ready' ? 'Минусовка подготовлена; прослушивание подключается следующим шагом.' : ''}</span>` : '';
    const captureAction = row.status === "running" && row.stage === 'sources' ? '' : row.status === "running" ? `<button class="btn" id="song-cancel-analysis">Отменить обработку</button>` : row.status === "recording" ? captureButton("song-stop-capture", "stop", "Остановить запись") : row.capabilities?.audio ? `<button class="btn primary" id="song-start-full">Полный разбор песни</button><button class="btn" id="song-start-transcribe">Распознать текст</button>` : `${captureButton("song-start-mic-capture", "mic", "Запись с микрофона")}${captureButton("song-start-system-capture", "system", "Запись системного звука")}`;
    const guitar = guitarHtml(row);
    const sourceOptions=(row.source_candidates||[]).filter(c=>c.kind==='youtube').sort((a,b)=>(b.match_score||0)-(a.match_score||0)).slice(0,5);
    const sourceStage=(row.stage==='sources'||row.status==='needs_input')&&!row.audio?.path ? `<section class="song-source-stage" aria-label="Выбор записи"><div class="song-source-stage-head"><div><h3>${row.status==='running'?'Ищем запись':'Выберите исполнение'}</h3><p class="hint">${row.status==='running'?'Поиск может занять до минуты. Можно отменить и попробовать ещё раз.':'Выберите точную версию песни; после выбора запись загрузится и станет доступна для распознавания.'}</p></div><div class="actions">${row.status==='running'?'<button class="btn" id="song-cancel-source">Отменить поиск</button>':''}<button class="btn" id="song-retry-source" ${row.status==='running'?'disabled':''}>Искать ещё раз</button></div></div>${sourceOptions.length?`<div class="song-source-options">${sourceOptions.map(c=>`<article class="song-source-option"><div><b>${esc(c.title)}</b><p>${esc(c.artist||'Исполнитель не указан')}${c.duration?` · ${Math.round(c.duration)} с`:''}</p></div><button class="btn primary small" data-song-source="${esc(c.candidate_id)}" ${row.status==='running'?'disabled':''}>Выбрать запись</button></article>`).join('')}</div>`:row.status==='running'?'<p class="hint" role="status">Поиск запущен. Результаты появятся здесь.</p>':'<p class="hint">Подходящие записи пока не найдены. Запустите поиск ещё раз или добавьте ссылку ниже.</p>'}</section>`:'';
    const sources = (row.source_candidates || []).map(c => `<div class="song-source"><b>${esc(c.title)}</b><p>${esc(c.artist)}${c.duration ? ` · ${Math.round(c.duration)} с` : ''}</p>${/^(https?:\/\/|yandexmusic:\/\/)/i.test(c.url || '') ? `<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${String(c.url).startsWith('yandexmusic:') ? 'Открыть поиск в приложении' : 'Открыть источник'}</a>` : ''}${c.note ? `<p class="song-note">${esc(c.note)}</p>` : ''}${c.kind === 'youtube' ? (!sourceStage ? `<button class="btn small" data-song-source="${esc(c.candidate_id)}" ${row.audio?.path || row.phrases?.length || row.status === 'running' ? 'disabled title="Для другой записи создайте отдельную карточку"' : ''}>Выбрать эту запись</button>` : '') : c.plain_lyrics || c.synced_lyrics ? `<details><summary>Текст для сверки</summary><p class="song-source-text">${esc(c.plain_lyrics || c.synced_lyrics)}</p></details>` : ""}</div>`).join('');
    const errors = (row.analysis_errors || []).map(e => `<p class="song-errors">${esc(e.step)}: ${esc(e.error)}</p>`).join('');
    detail.innerHTML = `<div class="songs-detail-head"><div><h2>${esc(row.title)}</h2><p class="hint">${esc(row.artist || "Исполнитель определяется по источникам")} · ${esc(currentStatus(row))}</p></div><button class="btn songs-detail-close" id="songs-detail-close">Закрыть</button></div>
      <div class="song-steps" aria-label="Этапы разбора">${stages.map((s, i) => `<div class="song-step ${i < active ? "done" : ""} ${i === active ? "on" : ""}"><b>${i + 1}. ${esc(s[1])}</b>${i < active ? "" : i === active ? esc(row.stage_label || "Выполняется") : "Ожидает"}</div>`).join("")}</div>
      ${sourceStage}
      ${row.capabilities?.audio ? `<section class="song-instrumental-stage" aria-label="Отдельный этап: минусовка"><div class="song-stage-head"><div><h3>Минусовка</h3><p class="hint">${instrumentalState==='ready' ? 'Подготовлена и доступна для отдельного воспроизведения.' : instrumentalState==='failed' ? esc(row.karaoke?.error||'Ошибка подготовки') : ['queued','preparing','separating','checking'].includes(instrumentalState) ? 'Идёт подготовка отдельной дорожки…' : 'Отдельно подготовьте версию без вокала.'}</p></div><div class="actions">${instrumentalAction}</div></div>${instrumentalState==='ready'&&row.karaoke?.artifact ? `<div class="song-instrumental-file"><span><b>Файл минусовки:</b> ${esc(row.karaoke.artifact.name)} · ${formatBytes(row.karaoke.artifact.bytes)}${row.karaoke.artifact.seconds?` · ${Math.round(row.karaoke.artifact.seconds)} с`:''}</span><button type="button" class="btn small" id="song-download-instrumental" title="Скачать подготовленную минусовку отдельным аудиофайлом">Скачать файл</button></div>` : ''}${SongReview.playerHtml(row)}</section>` : ''}
      <div class="song-detail-grid"><div class="song-preview">${guitar ? `<h3>Текст с аккордами</h3>${SongSheet.toolbar(row)}<p class="song-note">Предварительный подбор по звуку. B — си, Bb — си-бемоль. Аккорды над словами: точные привязки сохранены, остальные расположены приблизительно по времени фразы. Позицию можно поправить вручную.</p><div id="song-guitar-content">${guitar}</div>` : ""}<details><summary>Проверить и исправить распознавание</summary>${SongReview.html(row)}</details></div>
      <details class="song-properties" ${row.status === 'needs_input' ? 'open' : ''}><summary>Источники, свойства и правка аккордов</summary>${row.recording_version ? `<p class="song-note"><b>Выбранная запись:</b> ${esc(row.recording_version)}</p>` : ""}${errors}<label class="f">Название<input id="song-edit-title" value="${esc(row.title)}"></label><label class="f">Исполнитель<input id="song-edit-artist" value="${esc(row.artist || "")}"></label><p class="hint">Источник: ${esc(row.source || "поиск")}. Правки сохраняются отдельной ревизией.</p><div class="actions"><button class="btn" id="song-save-meta">Сохранить название</button><button class="btn" id="song-refresh">Обновить карточку</button><button class="btn" id="song-find-sources" ${row.status === "running" || row.status === "recording" ? "disabled" : ""}>Найти записи и текст</button>${captureButton("song-attach-audio", "upload", "Добавить аудиофайл")}${captureAction}</div><input id="song-audio-input" type="file" accept="audio/*,video/*" hidden><p class="hint" id="song-audio-hint">${row.audio?.path ? `Файл: ${esc(row.audio.original_name || row.audio.format || "аудио")}${row.audio.duration_ms ? ` · ${Math.round(row.audio.duration_ms / 1000)} с` : ""}` : "Можно импортировать локальную запись, если системный захват недоступен."}</p><h3>Добавить свой источник</h3><label class="f">Ссылка на запись или публикацию<input id="song-manual-url" type="url" maxlength="2048" placeholder="https://…"></label><label class="f">Текст песни для сверки (необязательно)<textarea id="song-manual-lyrics" maxlength="100000" rows="4"></textarea></label><p class="hint">YouTube-запись можно выбрать после добавления. Другие ссылки сохраняются для проверки; для автоматической сверки вставьте текст. Существующая запись и правки сохраняются.</p><button class="btn" id="song-add-manual-source" ${row.status === 'running' || row.status === 'recording' ? 'disabled' : ''}>Добавить источник</button><p class="hint" id="song-manual-source-result" role="status"></p>${SongSheet.html(row)}${sources ? `<h3>Источники</h3>${sources}` : ""}</details></div>`;
    SongSheet.bind(row, () => state.current, acceptSaved);
    SongSheet.bindToolbar(row, () => state.current, () => { document.getElementById("song-guitar-content").innerHTML = guitarHtml(state.current); });
    SongReview.bind(row, () => state.current, acceptSaved);
    $s("song-instrumental-play")?.addEventListener("click", () => SongReview.playVariant('instrumental'));
    $s("song-original-play")?.addEventListener("click", () => SongReview.playVariant('original'));
    $s("song-add-manual-source").onclick = async () => {
      const button = $s("song-add-manual-source"), result = $s("song-manual-source-result");
      button.disabled = true;
      try {
        const saved = await call("song_source_add", {song_id:row.song_id, base_revision:state.current.revision,
          url:$s("song-manual-url").value, lyrics:$s("song-manual-lyrics").value});
        state.current = saved; renderDetail(saved);
        detail.querySelector('.song-properties').open = true;
        $s("song-manual-source-result").textContent = saved.audio?.path
          ? 'Источник добавлен. При необходимости нажмите «Разобрать текст и аккорды»; текст доступен для сверки.'
          : 'Источник добавлен. Для YouTube выберите запись ниже или импортируйте аудиофайл, затем запустите разбор.';
      } catch (error) { result.textContent = error.message || 'Не удалось добавить источник'; button.disabled = false; }
    };
    $s("song-find-sources").onclick = async () => {
      try { renderDetail(await call("song_find_sources", {song_id: row.song_id})); await load(); }
      catch (error) { toast(error.message, 5000, "bad"); }
    };
    detail.querySelectorAll('[data-song-source]').forEach(button => button.onclick = async () => {
      button.disabled=true;button.textContent='Загружаем запись…';
      try { renderDetail(await call("song_download_source", {song_id: row.song_id, candidate_id: button.dataset.songSource})); await load(); }
      catch (error) { toast(error.message, 5000, "bad"); }
    });
    $s("song-retry-source")?.addEventListener("click",async()=>{try{renderDetail(await call("song_find_sources",{song_id:row.song_id}));await load();}catch(error){toast(error.message||"Не удалось начать поиск",5000,"bad");}});
    $s("song-cancel-source")?.addEventListener("click",async()=>{try{renderDetail(await call("song_cancel",{song_id:row.song_id}));await load();}catch(error){toast(error.message||"Не удалось отменить поиск",5000,"bad");}});
    $s("song-start-full")?.addEventListener("click", async () => {
      try { renderDetail(await call("song_transcribe", {song_id: row.song_id, full: true})); await load(); }
      catch (error) { toast(error.message, 5000, "bad"); }
    });
    $s("song-instrumental")?.addEventListener("click", async () => {
      try { await call("song_instrumental_prepare", {song_id: row.song_id, force: true}); renderDetail(await call("song_get", {song_id: row.song_id})); await load(); }
      catch (error) { toast(error.message || "Не удалось запустить подготовку минусовки", 5000, "bad"); }
    });
    $s("song-instrumental-cancel")?.addEventListener("click", async () => {
      try { await call("song_instrumental_cancel", {song_id: row.song_id}); renderDetail(await call("song_get", {song_id: row.song_id})); await load(); }
      catch (error) { toast(error.message || "Не удалось отменить подготовку", 5000, "bad"); }
    });
    $s("song-download-instrumental")?.addEventListener("click", async event => {
      const button=event.currentTarget;button.disabled=true;button.textContent='Готовим файл…';
      try {
        const clip=await call('song_full_audio',{song_id:row.song_id,variant:'instrumental'});
        const blob=await (await fetch(clip.data)).blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
        const safe=(row.title||'Минусовка').replace(/[\\/:*?"<>|]+/g,' ').trim()||'Минусовка';
        link.href=url;link.download=`${safe} — минусовка.mp3`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(error) { toast(error.message||'Не удалось получить файл минусовки',5000,'bad'); }
      finally { button.disabled=false;button.textContent='Скачать файл'; }
    });
    $s("song-refresh").onclick = () => openSong(row.song_id,true,true);
    $s("song-cancel-analysis")?.addEventListener("click", async () => {
      try { renderDetail(await call("song_cancel", {song_id: row.song_id})); }
      catch (error) { toast(error.message, 5000, "bad"); }
    });
    $s("songs-detail-close").onclick = closeDetail;
    $s("song-save-meta").onclick = async () => {
      try { const saved = await call("song_patch", { song_id: row.song_id, base_revision: state.current.revision, changes: { title: $s("song-edit-title").value, artist: $s("song-edit-artist").value } }); state.current = saved; await load(); renderDetail(saved); toast("Карточка сохранена"); }
      catch (error) { toast(error.message || "Не удалось сохранить", 4000, "bad"); }
    };
    $s("song-start-system-capture")?.addEventListener("click", async () => {
      try { await call("song_capture", { song_id: row.song_id, action: "start", source: "system", target: "all", backend: "auto" }); await load(); toast("Запись системного звука началась."); }
      catch (error) { toast(error.message || "Не удалось начать запись", 5000, "bad"); }
    });
    $s("song-start-mic-capture")?.addEventListener("click", async () => {
      try { await call("song_capture", { song_id: row.song_id, action: "start", source: "microphone" }); await load(); toast("Запись началась. Голосовой ввод ассистента выключен."); }
      catch (error) { toast(error.message || "Не удалось начать запись", 5000, "bad"); }
    });
    $s("song-stop-capture")?.addEventListener("click", async () => {
      try { await call("song_capture", { song_id: row.song_id, action: "stop" }); await load(); toast("Запись сохранена · распознавание запущено."); }
      catch (error) { toast(error.message || "Не удалось завершить запись", 5000, "bad"); }
    });
    $s("song-start-transcribe")?.addEventListener("click", async () => {
      try { await call("song_transcribe", { song_id: row.song_id }); await load(); toast("Распознавание началось. Полный текст останется доступен для сверки."); }
      catch (error) { toast(error.message || "Не удалось запустить распознавание", 5000, "bad"); }
    });
    $s("song-attach-audio")?.addEventListener("click", () => $s("song-audio-input")?.click());
    $s("song-audio-input")?.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      if (file.size > 256 * 1024 * 1024) { toast("Файл больше 256 МБ", 5000, "bad"); return; }
      try {
        const upload = await call("song_upload_begin", {song_id: row.song_id, name: file.name, size: file.size});
        try {
          for (let offset = 0; offset < file.size; offset += 1024 * 1024) {
            const piece = file.slice(offset, offset + 1024 * 1024);
            const data = await new Promise((resolve,reject) => {const reader = new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(piece);});
            await call("song_upload_chunk", {upload_id:upload.upload_id,offset,data});
            const hint = $s("song-audio-hint"); if (hint) hint.textContent = `Импорт записи: ${Math.min(100,Math.round((offset+piece.size)/file.size*100))}%`;
          }
          await call("song_upload_finish", {upload_id:upload.upload_id});
        } catch (error) { await call("song_upload_cancel", {upload_id:upload.upload_id}).catch(()=>{}); throw error; }
        await load();
        toast("Аудиофайл добавлен — можно распознавать");
      } catch (error) { toast(error.message || "Не удалось добавить аудиофайл", 5000, "bad"); }
    });
  }
  function updateCardPlayback() {
    const list=$s('songs-list'); if(!list) return;
    list.querySelectorAll('[data-song-play]').forEach(button=>{
      const row=state.rows.find(r=>r.song_id===button.dataset.songPlay);if(!row)return;
      const current=cardPlayback.songId===row.song_id ? cardPlayback : {};
      const isCurrent=current.variant!=='instrumental';
      const playing=current.playing && isCurrent, active=current.active && isCurrent;
      const label=current.loading && isCurrent ? 'Подготавливаем запись' : playing ? 'Приостановить' : active ? 'Продолжить' : 'Проиграть';
      button.innerHTML=SongSheet.icon(playing ? 'pause' : 'play');
      button.title=row.capabilities?.audio ? `${label} песню «${row.title}»` : 'Для прослушивания добавьте аудиозапись';
      button.setAttribute('aria-label',`${label} ${row.title}`);
      button.setAttribute('aria-busy',String(!!current.loading && isCurrent));
      button.disabled=!!(current.loading && isCurrent) || !row.capabilities?.audio;
    });
    list.querySelectorAll('[data-song-karaoke]').forEach(button=>{
      const row=state.rows.find(r=>r.song_id===button.dataset.songKaraoke);if(!row)return;
      const current=cardPlayback.songId===row.song_id?cardPlayback:{};
      const isCurrent=current.variant==='instrumental', playing=isCurrent&&current.playing, active=isCurrent&&current.active;
      const label=current.loading&&isCurrent?'Подготавливаем минусовку':playing?'Приостановить караоке':active?'Продолжить караоке':'Проиграть караоке';
      button.innerHTML=SongSheet.icon(playing?'pause':'karaoke');button.title=`${label}: «${row.title}»`;
      button.setAttribute('aria-label',`${label}: ${row.title}`);button.setAttribute('aria-busy',String(!!(current.loading&&isCurrent)));
      button.disabled=!!(current.loading&&isCurrent)||row.karaoke?.state!=='ready';
    });
    list.querySelectorAll('[data-song-stop]').forEach(button=>{button.hidden=!(cardPlayback.songId===button.dataset.songStop && cardPlayback.active);});
  }
  window.onSongPlayback=(songId,status)=>{cardPlayback={songId,...status};updateCardPlayback();};
  function stopCard() {
    cardRequest++;openRequest++;SongReview.stop();cardPlayback={};updateCardPlayback();
  }
  async function playCard(id,variant='original') {
    const request=++cardRequest;
    if(state.current?.song_id!==id) {
      const opened=await openSong(id,false);
      if(request!==cardRequest)return;
      if(!opened){cardPlayback={};updateCardPlayback();return;}
    }
    if(request===cardRequest) {
      const same=cardPlayback.songId===id && cardPlayback.variant===variant && cardPlayback.active;
      if(same) await SongReview.toggleFull(false);
      else await SongReview.playVariant(variant);
    }
  }
  async function openSong(id,scroll=true,force=false) {
    const request=++openRequest;
    try {
      if(state.current?.song_id!==id || force) {
        SongReview.stop();
        if(!scroll){cardPlayback={songId:id,loading:true,active:true};updateCardPlayback();}
        const row=await call('song_get',{song_id:id});
        if(request!==openRequest)return false;
        renderDetail(row);
      }
      if(scroll)$s('songs-detail')?.scrollIntoView({behavior:'smooth',block:'start'});
      return true;
    } catch(error) { if(request===openRequest)toast(error.message || 'Песня не найдена',4000,'bad');return false; }
  }
  function closeDetail() { stopCard(); state.current = null; const detail = $s("songs-detail"); if (detail) { detail.hidden = true; detail.innerHTML = ""; } }
  async function load() { try { state.rows = await call("songs_list", { q: state.query, status: state.status, limit: 100 }) || []; renderList(); if (state.current) { const id = state.current.song_id; if (!SongReview.protectedView(id)) { const fresh = await call("song_get", {song_id:id}); if (state.current?.song_id === id) renderDetail(fresh); } } } catch (error) { toast(error.message || "Не удалось загрузить песни", 4000, "bad"); } }
  async function create(source, title, artist) { try { const row = await call("song_create", { source, title, artist }); await load(); renderDetail(row); if (source === "query") renderDetail(await call("song_find_sources", {song_id: row.song_id})); } catch (error) { toast(error.message || "Не удалось создать карточку", 4000, "bad"); } }
  async function createMicSong() {
    const title=`Запись с микрофона · ${new Date().toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}`;
    try {
      const row=await call('song_create',{source:'current',title,artist:''});
      await call('song_capture',{song_id:row.song_id,action:'start',source:'microphone'});
      await load();renderDetail(await call('song_get',{song_id:row.song_id}));
      toast('Запись с микрофона началась. После остановки начнётся распознавание текста.');
    } catch(error) { toast(error.message||'Не удалось начать запись песни',5000,'bad'); }
  }
  let createSource = "query", createFocus = null;
  function closeCreateDialog() { const box = $s("song-create-dialog"); if (box) box.hidden = true; createFocus?.focus(); }
  function queryCreate(source) {
    createSource = source; createFocus = document.activeElement;
    const box = $s("song-create-dialog"), input = $s("song-create-input"), help = $s("song-create-help"), title = $s("song-create-title");
    if (!box || !input) return;
    title.textContent = source === "current" ? "Разобрать текущую песню" : "Найти песню";
    help.textContent = source === "current" ? "Проверьте название текущего трека. Его можно уточнить перед запуском." : "Введите название и исполнителя через тире.";
    input.value = "";
    if (source === "current") {
      help.textContent = "Считываю название и исполнителя из открытого плеера. Если приложение не отдаёт метаданные, введите их вручную.";
      call("song_current_track").then(track => {
        if (box.hidden || createSource !== "current") return;
        if (track?.title) input.value = track.artist ? `${track.title} — ${track.artist}` : track.title;
        else help.textContent = "Плеер не передал метаданные. Введите название и исполнителя вручную.";
      }).catch(() => { if (!box.hidden) help.textContent = "Не удалось прочитать трек плеера. Введите название и исполнителя вручную."; });
    }
    box.hidden = false;
    input.focus();
  }
  function bind() {
    $s("song-create-dialog")?.addEventListener("keydown", event => {
      if (event.key === "Escape") { event.preventDefault(); closeCreateDialog(); return; }
      if (event.key !== "Tab") return;
      const fields = [...$s("song-create-dialog").querySelectorAll("input,button")].filter(x => !x.disabled);
      const first = fields[0], last = fields[fields.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    ["songs-new-current", "songs-empty-current"].forEach((id) => $s(id)?.addEventListener("click", () => queryCreate("current")));
    ["songs-new-query", "songs-empty-query"].forEach((id) => $s(id)?.addEventListener("click", () => queryCreate("query")));
    ["songs-new-mic","songs-empty-mic"].forEach(id=>$s(id)?.addEventListener('click',createMicSong));
    $s("song-create-cancel")?.addEventListener("click", closeCreateDialog);
    $s("song-create-submit")?.addEventListener("click", async () => {
      const input = $s("song-create-input"), value = input?.value.trim();
      if (!value) { input?.focus(); return; }
      const parts = value.split(/\s+[—-]\s+|\s+\/\s+/);
      closeCreateDialog();
      await create(createSource, parts[0].trim(), parts.slice(1).join(" — ").trim());
    });
    $s("song-create-input")?.addEventListener("keydown", (event) => { if (event.key === "Enter") $s("song-create-submit")?.click(); if (event.key === "Escape") closeCreateDialog(); });
    $s("songs-query")?.addEventListener("input", (event) => { state.query = event.target.value; clearTimeout(state.timer); state.timer = setTimeout(load, 220); });
    document.querySelectorAll("[data-song-status]").forEach((el) => el.addEventListener("click", () => { document.querySelectorAll("[data-song-status]").forEach((x) => (x.classList.toggle("on", x === el), x.setAttribute("aria-pressed", String(x === el)))); state.status = el.dataset.songStatus; load(); }));
    function taskTab(songs) {
      $s('p-songs').hidden = !songs; $s('tasks-work-content').hidden = songs;
      $s('tasks-songs-tab').setAttribute('aria-pressed', String(songs));
      $s('tasks-work-tab').setAttribute('aria-pressed', String(!songs));
      if (songs) load(); else SongReview.stop();
    }
    $s('tasks-songs-tab')?.addEventListener('click', () => taskTab(true));
    $s('tasks-work-tab')?.addEventListener('click', () => taskTab(false));
    document.addEventListener('DOMContentLoaded', () => { if (location.hash === '#songs') { document.querySelector('nav button[data-p="tasks"]')?.click(); taskTab(true); } });
  }
  bind();
  window.loadSongs = load;
  core.on("hello", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_capture", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_analysis", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_instrumental", event => {
    if (state.current?.song_id === event.song_id) call("song_get", {song_id:event.song_id}).then(renderDetail).catch(()=>{});
    if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load();
  });
  core.on("settings", () => { const on = !S || !S.features || S.features.songs !== false; const b = document.querySelector('nav button[data-p="songs"]'); if (b) b.hidden = !on; if (!on && document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) document.querySelector('nav button[data-p="home"]')?.click(); });
})();
