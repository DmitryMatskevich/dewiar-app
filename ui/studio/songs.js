/* Вкладка «Песни»: библиотека, обработка и проверка текста/аккордов.
   Правки сохраняются с контролем ревизий; рабочие настройки не изменяются. */
(function () {
  "use strict";
  const state = { rows: [], query: "", lyricsQuery: "", status: "", tags: [], tagOptions: [], rating: "", current: null, transcribeSongId: "" };
  let cardPlayback = {}, openRequest = 0, cardRequest = 0, assistantSongQueue = [], assistantQueueRunning = false;
  const processingSongs = new Set();
  const stoppingCaptures = new Set();
  function liveCapture(row) {
    const capture=row.processing?.capture||{};
    const waiting=capture.phase==='waiting'||(!capture.phase&&String(row.stage_label||'').startsWith('Ожидаем'));
    const seconds=Math.floor((capture.duration_ms||0)/1000);
    return `<div class="song-capture-live" role="status"><b>${waiting?'Ожидаем звук — включите песню':`● Запись идёт · ${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`}</b><p>${esc(capture.draft_status||'Черновой текст появится после начала пения')}</p>${capture.draft?`<p class="song-capture-draft">${esc(capture.draft)}</p><small>Черновик. После остановки записи начнётся полный разбор.</small>`:''}</div>`;
  }
  const restartBatchButton=row=>row.processing?.can_restart?`<button type="button" class="btn song-icon-btn" aria-label="Перезапустить пакетную обработку" data-song-restart-batch="${esc(row.song_id)}" title="Заново выполнить пакетный разбор сохранённого аудио. Прежние результаты и ручные правки сохраняются; новая запись не требуется." ${processingSongs.has(row.song_id)?'disabled':''}>${SongSheet.icon('refresh')}</button>`:'';
  const stopCaptureButton = (id) => `<button type="button" class="btn primary song-record-stop" data-song-stop-capture="${esc(id)}" title="Остановить запись, сохранить звук и запустить пакетную обработку" ${stoppingCaptures.has(id)?'disabled aria-busy="true"':''}>${SongSheet.icon('stop')}${stoppingCaptures.has(id)?'Сохраняем запись…':'Остановить запись'}</button>`;
  const statusText = { queued: "Ожидает", recording: "Записываем", running: "В работе", needs_input: "Нужно выбрать", review: "Нужно проверить", ready: "Готово", partial: "Частично", failed: "Ошибка", cancelled: "Отменено", interrupted: "Прервано" };
  const TAG_OPTIONS = ["Компания", "Мужская", "Любовь", "Шансон", "Бард", "Грусть", "Юмор", "Дворовые", "Народные", "Рок"];
  function tagAddMenu(row) {
    const selected = new Set(row.tags || []);
    const options = [...new Set([...TAG_OPTIONS, ...state.tagOptions, ...(row.tags || [])])].filter(tag => !selected.has(tag));
    return `<div class="song-tag-add-menu" data-song-tag-menu hidden aria-label="Добавить тег к песне"><div class="song-tag-options">${options.map(tag => `<button type="button" class="song-tag-option" data-song-tag-option="${esc(tag)}" data-song-tag-song="${esc(row.song_id)}">${esc(tag)}</button>`).join("")}</div><form class="song-tag-custom-form" data-song-tag-custom-form="${esc(row.song_id)}"><input type="text" maxlength="40" placeholder="Новый тег" aria-label="Новый тег для песни"><button type="submit" class="btn small">Добавить</button></form></div>`;
  }
  function ratingMarkup(row) {
    const rating = Math.max(0, Math.min(5, Number(row.rating) || 0));
    return `<div class="song-rating" data-song-rating-song="${esc(row.song_id)}" role="group" aria-label="Оценка песни: ${rating ? `${rating} из 5 звёзд` : 'не оценена'}">${[1,2,3,4,5].map(value => `<button type="button" class="song-rating-star ${value <= rating ? 'on' : ''}" data-song-rating-value="${value}" aria-label="${value} ${value === 1 ? 'звезда' : value < 5 ? 'звезды' : 'звёзд'}" aria-pressed="${value === rating}">${value <= rating ? '★' : '☆'}</button>`).join('')}</div>`;
  }
  function renderTagFilters() {
    const container = $s("songs-tag-filters");
    if (!container) return;
    const tags = [...new Set([...TAG_OPTIONS, ...state.tagOptions])].sort((a, b) => {
      const ai = TAG_OPTIONS.indexOf(a), bi = TAG_OPTIONS.indexOf(b);
      return ai >= 0 || bi >= 0 ? (ai < 0 ? 1 : bi < 0 ? -1 : ai - bi) : a.localeCompare(b, "ru");
    });
    container.innerHTML = `<button class="chip ${state.tags.length ? "" : "on"}" aria-pressed="${!state.tags.length}" data-song-tag="">Все</button>${tags.map(tag => `<button class="chip ${state.tags.includes(tag) ? "on" : ""}" aria-pressed="${state.tags.includes(tag)}" data-song-tag="${esc(tag)}">${esc(tag)}</button>`).join("")}`;
  }
  const allStages = [["identify", "Определяем"], ["sources", "Ищем источники"], ["record", "Записываем"], ["lyrics", "Распознавание слов с аудио"], ["text_reference", "Сверка с источником текста"], ["structure", "Размечаем части"], ["chords", "Подбираем аккорды"], ["karaoke", "Минусовка"], ["review", "Проверяем"]];
  const stagesFor=()=>allStages;
  function textReferenceStatus(row) {
    const report=row.text_reference;
    if(row.stage==='text_reference'&&row.status==='running')return row.stage_label||'Выполняется';
    return {insufficient_consensus:'Недостаточно согласованных фраз',not_found:'Источник не найден',search_error:'Поиск нужно повторить',
      needs_confirmation:'Выберите источник текста',source_unavailable:'Нужно добавить текст страницы',compared:`Исправлено: ${report?.applied||0}; осталось проверить: ${(report?.candidates||[]).filter(c=>!c.auto_apply&&c.status!=='user_applied').length}`}[report?.state]||'Ещё не выполнялась';
  }
  function confirmedSources(row) {
    const groups=[['lyrics','Источник текста',row.text_reference?.references||[]],['chords','Источник аккордов',row.text_reference?.chord_sources||[]]];
    return `<div class="song-confirmed-sources">${groups.map(([kind,label,refs])=>`<div class="song-source" data-confirmed-source="${kind}"><h4>${label}</h4>${refs.length?refs.map(ref=>`<p><a href="${esc(ref.url)}" target="_blank" rel="noopener noreferrer">${esc(ref.title||label)}</a><br><span class="hint">${ref.user_confirmed?'Источник подтверждён вами':`Подтверждено по ${(ref.text_confirmation?.hits||[]).length} фразам`}</span></p>`).join(''):`<p class="hint">${row.text_reference?'Подтверждённая страница пока не найдена.':'Запустите пятый этап «Сверка с источником текста».'}</p>`}</div>`).join('')}</div>`;
  }
  function textCandidatePreview(row, candidate) {
    const phrases = row.phrases || [];
    const index = id => phrases.findIndex(item => item.phrase_id === id);
    // A correction across a line boundary: the cut-off piece in the
    // neighbouring line is cleared, the publication's words go here.
    const parts = [{...(candidate.current || {}), replacement: candidate.alternative?.text || ""},
      ...((candidate.current?.also) || []).map(part => ({...part, replacement: ""}))]
      .sort((a, b) => index(a.phrase_id) - index(b.phrase_id));
    const line = (part, corrected) => {
      const source = phrases[index(part.phrase_id)]?.text || part.text || "";
      const start = Math.max(0, Math.min(source.length, Number(part.char_start) || 0));
      const end = Math.max(start, Math.min(source.length, Number(part.char_end) || start));
      const middle = corrected ? (part.replacement ? `<mark>${esc(part.replacement)}</mark>` : "") : `<mark>${esc(source.slice(start, end))}</mark>`;
      return `${esc(source.slice(0, start))}${middle}${esc(source.slice(end))}`;
    };
    const original = parts.map(part => line(part, false)).join(" <span class=\"hint\">/</span> ");
    const corrected = parts.map(part => line(part, true)).join(" <span class=\"hint\">/</span> ");
    return `<div class="song-reference-candidate"><div class="song-reference-line"><span class="hint">Исходная фраза</span><p>${original}</p></div><div class="song-reference-line song-reference-line--alternative"><span class="hint">Фраза после подтверждения</span><p>${corrected}</p></div></div>`;
  }
  // Time span of a correction in the recording: its words with one word of
  // context on each side; without word times, by the character position in
  // the line.
  function textCandidateRange(row, candidate) {
    const parts = [candidate.current || {}, ...(candidate.current?.also || [])];
    let lo = Infinity, hi = -Infinity;
    for (const part of parts) {
      const phrase = (row.phrases || []).find(item => item.phrase_id === part.phrase_id);
      if (!phrase) continue;
      const a = Number(part.char_start) || 0, b = Math.max(a, Number(part.char_end) || a);
      const words = (phrase.words || []).filter(w => Number.isFinite(w.start_ms) && Number.isFinite(w.end_ms) && w.end_ms > w.start_ms)
        .sort((x, y) => x.char_start - y.char_start);
      const inside = words.filter(w => w.char_end > a && w.char_start < b);
      let from, to;
      if (inside.length) { from = inside[0].start_ms; to = inside.at(-1).end_ms; }
      else {
        const length = Math.max(1, (phrase.text || "").length), ps = Number(phrase.start_ms) || 0, pe = Math.max(ps, Number(phrase.end_ms) || ps);
        from = ps + (pe - ps) * a / length; to = ps + (pe - ps) * b / length;
      }
      const before = words.filter(w => w.char_end <= a).at(-1), after = words.find(w => w.char_start >= b);
      if (before && from - before.start_ms < 2500) from = Math.min(from, before.start_ms);
      if (after && after.end_ms - to < 2500) to = Math.max(to, after.end_ms);
      lo = Math.min(lo, from); hi = Math.max(hi, to);
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const start = Math.max(0, Math.round(lo - 400));
    return {start_ms: start, end_ms: Math.min(start + 28000, Math.round(hi + 400))};
  }
  let correctionAudio = null, correctionButton = null;
  function stopCorrectionAudio() {
    correctionAudio?.pause();
    if (correctionButton) { correctionButton.textContent = "▶"; correctionButton.setAttribute("aria-pressed", "false"); }
    correctionAudio = null; correctionButton = null;
  }
  async function playCorrection(button, row, candidate) {
    if (correctionButton === button) { stopCorrectionAudio(); return; }
    stopCorrectionAudio();
    const range = textCandidateRange(row, candidate);
    if (!range) { toast("Для этого места нет времени в записи"); return; }
    window.SongReview?.stop?.();
    correctionButton = button; button.disabled = true; button.textContent = "…";
    try {
      const clip = await call("song_phrase_audio", {song_id: row.song_id, phrase_id: candidate.current.phrase_id, ...range});
      if (correctionButton !== button) return;
      const audio = new Audio(clip.data);
      correctionAudio = audio;
      audio.onended = () => { if (correctionAudio === audio) stopCorrectionAudio(); };
      button.disabled = false; button.textContent = "■"; button.setAttribute("aria-pressed", "true");
      await audio.play();
    } catch (error) {
      button.disabled = false; stopCorrectionAudio();
      toast(error.message || "Не удалось проиграть фрагмент");
    }
  }
  function textReferencePanel(row) {
    const report=row.text_reference, refs=report?.references||[];
    if(!report&&!row.phrases?.length&&row.stage!=='text_reference')return '';
    const busy=row.status==='running'||row.processing?.busy;
    const messages={needs_confirmation:'Текст получен. Проверьте публикацию и подтвердите источник для сверки.',source_unavailable:'Страница не отдала текст. Можно открыть её и вставить текст ниже.',insufficient_consensus:'Пока нет трёх разных фраз с полным согласием распознавателей.',not_found:'Источник с тремя совпадающими фразами не найден.',search_error:'Поиск источника не завершён: можно повторить.',compared:`Источников подтверждено: ${refs.length}. Исправлений сохранено: ${report?.applied||0}. Из них по источнику и одному распознавателю при близком разделении слов: ${(report?.candidates||[]).filter(c=>c.status==='confirmed_by_source_and_audio').length}. Осталось проверить: ${(report?.candidates||[]).filter(c=>!c.auto_apply&&c.status!=='user_applied').length}.`};
    const status=row.stage==='text_reference'&&busy?row.stage_label:report?.user_applied?'Выбранные исправления сохранены. Времена фраз сохранены; изменённые слова требуют проверки по записи.':messages[report?.state]||'Можно найти текст по согласованным фразам и сверить спорные места.';
    const pending = (report?.candidates || []).some(candidate => !candidate.auto_apply && candidate.status !== 'user_applied');
    return `<section class="song-text-reference" aria-label="Сверка с источником текста"><div class="song-stage-head"><div><h3>Источники текста</h3><p class="hint" role="status">${esc(status)}</p></div><div class="actions">${row.audio?.path?`<button class="btn small" data-song-rerun-step="text_reference" ${busy?'disabled':''} title="Найти стихотворение или текст песни по согласованным фразам, подтвердить по трём совпадениям и сверить с записью">${report?'Сверить текст':'Найти и сверить текст'}</button>`:''}${pending?`<button class="btn small primary" data-text-apply-all ${busy?'disabled':''} title="Подтвердить и применить все найденные исправления текста">Подтвердить всё</button>`:''}</div></div>
      ${(report?.errors||[]).length?`<details class="song-search-diagnostics"><summary>Почему поиск не завершён</summary><ul>${[...new Set(report.errors.map(e=>e.error).filter(Boolean))].map(message=>`<li>${esc(message)}</li>`).join('')}</ul></details>`:''}
      <div class="song-text-source-form"><label class="f">Ссылка на источник текста<input id="song-text-source-url" type="url" value="${esc(report?.state==='source_unavailable'?report.suggested_sources?.at(-1)?.url:'')}" maxlength="2048" placeholder="https://genius.com/…"></label><details><summary>Вставить текст песни вручную</summary><label class="f">Текст источника<textarea id="song-text-source-lyrics" rows="6" maxlength="50000"></textarea></label></details><button type="button" class="btn" id="song-text-source-add" ${busy?'disabled':''}>Загрузить текст и сверить</button><p id="song-text-source-result" class="hint" role="status"></p></div>
      ${refs.map(ref=>`<details class="song-reference-diff"><summary>${esc(ref.title)} · ${ref.user_confirmed?'подтверждено вами':`подтверждено по ${ref.text_confirmation?.hits?.length||0} фразам`}</summary>${ref.url?`<a href="${esc(ref.url)}" target="_blank" rel="noopener noreferrer">Открыть источник текста</a>`:''}<ul>${(ref.text_confirmation?.hits||[]).map(hit=>`<li>${esc(hit.anchor)}</li>`).join('')}</ul><pre style="white-space:pre-wrap;max-height:240px;overflow:auto">${esc(ref.plain_lyrics)}</pre><button class="btn small" data-text-source-confirm="${esc(ref.candidate_id)}" ${busy?'disabled':''}>Сверить с этим источником</button></details>`).join('')}
      ${(report?.suggested_sources||[]).filter(ref=>!refs.some(r=>r.candidate_id===ref.candidate_id)).map(ref=>`<div class="song-reference-diff"><h4>${esc(ref.title)}</h4>${ref.url?`<a href="${esc(ref.url)}" target="_blank" rel="noopener noreferrer">Открыть источник текста</a>`:''}${ref.plain_lyrics?`<details><summary>Посмотреть полученный текст · совпадений: ${ref.text_confirmation?.hits?.length||0}</summary><pre style="white-space:pre-wrap;max-height:240px;overflow:auto">${esc(ref.plain_lyrics)}</pre></details><button class="btn" data-text-source-confirm="${esc(ref.candidate_id)}" ${busy?'disabled':''}>Подтвердить источник и сверить</button>`:`<p class="hint">${esc(ref.source_check?.error||'Не удалось извлечь текст. Вставьте его вручную.')}</p>`}</div>`).join('')}
      ${(report?.candidates||[]).some(c=>!c.auto_apply&&c.status!=='user_applied')?`<details id="song-text-corrections" open><summary>Исправления по источнику — выберите подходящие</summary><div class="actions song-reference-bulk"><button type="button" class="btn small" data-text-select-all>Выбрать все</button><span class="hint">Выделенный фрагмент показан в исходной и подтверждаемой фразе. Любую правку можно изменить в любой момент.</span></div>${report.candidates.map((c,i)=>c.auto_apply||c.status==='user_applied'?'':`<div class="song-reference-candidate-choice"><div class="song-reference-candidate-head"><label><input type="checkbox" data-text-correction="${i}"><span>Применить это исправление</span></label><button type="button" class="btn small song-reference-play" data-text-correction-play="${i}" aria-pressed="false" title="Прослушать это место записи">▶</button></div>${textCandidatePreview(row,c)}</div>`).join('')}<button class="btn primary" id="song-text-source-apply" ${busy?'disabled':''}>Применить выбранные исправления</button><p class="hint">Повторы и времена фраз сохраняются. Различия исполнения проверяйте по записи.</p></details>`:''}
      ${(report?.chord_sources||[]).length?`<p>Источники аккордов: ${report.chord_sources.map(ref=>`<a href="${esc(ref.url)}" target="_blank" rel="noopener noreferrer">${esc(ref.title)}</a>`).join(' · ')}</p>`:''}</section>`;
  }
  const esc = (v) => escH(v == null ? "" : v);
  function safeSourceUrl(value) {
    const raw = String(value || '').trim();
    if (!/^(https?:\/\/|yandexmusic:\/\/)/i.test(raw)) return '';
    try {
      const parsed = new URL(raw);
      return ['http:', 'https:', 'yandexmusic:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (_) {
      return '';
    }
  }
  function sourceTitleMarkup(candidate) {
    const title = esc(candidate.title || 'Источник записи');
    const href = safeSourceUrl(candidate.url);
    return href
      ? `<a class="song-source-title-link" href="${esc(href)}" target="_blank" rel="noopener noreferrer" title="Открыть источник, посмотреть и прослушать запись">${title}</a>`
      : `<b>${title}</b>`;
  }
  const call = (name, args) => core.call(name, args || {});
  const $s = (id) => document.getElementById(id);
  const captureButton = (id, glyph, title) => `<button type="button" class="btn song-icon-btn song-capture-btn" id="${id}" title="${title}" aria-label="${title}">${SongSheet.icon(glyph)}</button>`;
  function videoKey(candidate) {
    try {
      const url = new URL(candidate.url);
      return url.hostname.endsWith('youtu.be') ? url.pathname.slice(1).split('/')[0] : url.searchParams.get('v') || '';
    } catch (_) { return ''; }
  }

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
  function instrumentalPanel(row, state) {
    const busy = ["queued", "preparing", "separating", "checking"].includes(state);
    const hasAudio = !!(row.capabilities?.audio || row.audio?.path);
    const message = state === "ready" ? "Минусовка создана." : state === "failed" ? `Не удалось создать минусовку: ${row.karaoke?.error || "ошибка подготовки"}` : busy ? "Идёт подготовка минусовки независимо от проверки текста…" : hasAudio ? "Минусовка будет подготовлена автоматически в пакетной обработке." : "Добавьте аудиозапись, чтобы создать минусовку.";
    const action = hasAudio
      ? `<button type="button" class="btn primary" id="song-instrumental" ${busy ? "disabled" : ""}>${busy ? "Готовится…" : state === "ready" ? "Пересоздать минусовку" : state === "failed" || state === "interrupted" ? "Повторить" : "Создать минусовку"}</button>${busy ? '<button type="button" class="btn" id="song-instrumental-cancel">Отменить</button>' : ""}`
      : `<button type="button" class="btn" id="song-instrumental-add-audio">Добавить аудиозапись</button>`;
    const artifact = state === "ready" && row.karaoke?.artifact ? `<div class="song-instrumental-file"><span><b>Минусовка:</b> ${esc(row.karaoke.artifact.name)} · ${formatBytes(row.karaoke.artifact.bytes)}${row.karaoke.artifact.seconds ? ` · ${Math.round(row.karaoke.artifact.seconds)} с` : ""}</span><button type="button" class="btn small" id="song-download-instrumental" title="Скачать подготовленную минусовку отдельным аудиофайлом">Скачать файл</button></div>` : "";
    return `<section class="song-instrumental-stage" aria-label="Этап создания минусовки"><div class="song-stage-head"><div><h3>Минусовка</h3><p class="hint" id="song-instrumental-status">${esc(message)}</p></div><div class="actions">${action}</div></div>${artifact}${hasAudio ? SongReview.playerHtml(row) : ""}</section>`;
  }
  function stageIndex(row) {
    const stages=stagesFor(row);
    const instrumental = row.karaoke?.state;
    const karaokeIndex = stages.findIndex((x) => x[0] === "karaoke");
    const reviewIndex = stages.findIndex((x) => x[0] === "review");
    // Минусовка запускается независимо, но в карточке показывается как
    // текущий этап только после завершения распознавания и аккордов.
    if (["review", "ready"].includes(row.stage) || row.status === "review") {
      return instrumental === "ready" ? reviewIndex : karaokeIndex;
    }
    const i = stages.findIndex((x) => x[0] === row.stage);
    return i < 0 ? 0 : i;
  }
  function stageRestart(row, key) {
    if(key==='text_reference') {
      const disabled=row.processing?.busy||['recording','running'].includes(row.status)||!row.audio?.path||!row.phrases?.length;
      return `<button type="button" class="btn small song-step-retry" data-song-rerun-step="text_reference" ${disabled?'disabled':''} title="Найти источник по трём согласованным фразам и сверить текст с записью">${row.text_reference?'Повторить этап':'Запустить этап'}</button>`;
    }
    if (row.processing?.busy || ['recording'].includes(row.status)) return '';
    if (['identify', 'sources'].includes(key) && row.status !== 'recording') {
      return `<button type="button" class="btn small song-step-retry" data-song-restart-sources="${esc(row.song_id)}" title="Повторить поиск источников и продолжить обработку песни">Повторить</button>`;
    }
    if (['lyrics', 'text_reference', 'structure', 'chords'].includes(key) && row.audio?.path && (row.analysis_runs?.length || row.capabilities?.lyrics)) {
      const labels = {lyrics: 'распознавание слов с аудио', text_reference:'сверку с источником текста', structure: 'разметку куплетов и припевов', chords: 'проверку аккордов'};
      return `<button type="button" class="btn small song-step-retry" data-song-rerun-step="${key}" data-song-id="${esc(row.song_id)}" title="Перезапустить ${labels[key]}; результаты остальных этапов и пользовательские правки сохранятся">Повторить этап</button>`;
    }
    if (key === 'karaoke' && row.audio?.path && !['queued', 'preparing', 'separating', 'checking'].includes(row.karaoke?.state)) {
      return `<button type="button" class="btn small song-step-retry" data-song-rerun-karaoke="${esc(row.song_id)}" title="Запустить или повторить подготовку минусовки независимо от проверки текста">Повторить этап</button>`;
    }
    return '';
  }
  function textReferenceStageSource(row) {
    const reference = row.text_reference?.references?.find(ref => safeSourceUrl(ref.url));
    const href = safeSourceUrl(reference?.url);
    return href ? `<a class="song-step-source" data-song-reference-source href="${esc(href)}" target="_blank" rel="noopener noreferrer" title="Открыть источник, по которому выполнена сверка текста">${esc(reference.title || 'Открыть подтверждённый источник')}</a>` : '';
  }
  function stageActions(row, key) {
    if (key === 'karaoke' && row.karaoke?.state === 'ready' && row.karaoke?.artifact) {
      return `<button type="button" class="btn small" data-song-download-instrumental-stage title="Скачать готовую минусовку отдельным аудиофайлом">Скачать минусовку</button>`;
    }
    const reviewReady = ['review', 'ready'].includes(row.status) || row.stage === 'review' || row.stage === 'ready';
    if (key === 'review' && reviewReady && row.phrases?.length && (row.chords || []).some(chord => chord.name && chord.name !== 'N')) {
      return `<button type="button" class="btn small primary" data-song-confirm-review title="Подтвердить проверенные текст и аккорды и завершить обработку">Подтвердить</button><button type="button" class="btn small" data-song-download-text title="Скачать текст с аккордами и источниками">Скачать текст</button>`;
    }
    return '';
  }
  function card(row) {
    const artist = row.artist ? `<span class="song-artist">${esc(row.artist)}</span>` : "";
    const ks = row.karaoke?.state;
    const canProcessSelected=!!row.processing?.can_resume;
    const processing=processingSongs.has(row.song_id);
    const tags=(row.tags||[]).map(tag=>`<button type="button" class="song-tag" data-song-card-tag="${esc(tag)}" title="Убрать тег «${esc(tag)}» у этой песни" aria-label="Убрать тег «${esc(tag)}»">${esc(tag)}</button>`).join("");
    const karaokeBadge = ["queued", "preparing", "separating", "checking"].includes(ks) ? '<span class="song-karaoke-status" data-state="busy">Минусовка готовится</span>' : ks === "failed" || ks === "interrupted" ? '<span class="song-karaoke-status" data-state="failed">Минусовка: повторить</span>' : "";
    return `<article class="song-card" data-song-id="${esc(row.song_id)}">
      <div class="song-card-main" data-song-open="${esc(row.song_id)}" tabindex="0" role="button" aria-label="Открыть ${esc(row.title)}">
        <div class="song-heading"><button type="button" class="song-title song-title-edit" data-song-title-edit="${esc(row.song_id)}" aria-label="Изменить название песни «${esc(row.title)}»">${esc(row.title)}</button>${artist}</div>
        ${ratingMarkup(row)}
        <div class="song-meta"><span class="song-status" data-status="${esc(row.status)}">${esc(currentStatus(row))}</span>${karaokeBadge}</div><div class="song-tags" aria-label="Теги песни"><button type="button" class="song-tag-add" data-song-tag-add="${esc(row.song_id)}" aria-label="Добавить тег к песне «${esc(row.title)}»" title="Добавить тег к этой песне">+</button>${tags}</div>${tagAddMenu(row)}
      </div>
      ${row.status==='recording'?liveCapture(row):''}
      <div class="song-card-actions">
        ${restartBatchButton(row)}
        ${row.status === 'recording' ? stopCaptureButton(row.song_id) : ''}
        ${canProcessSelected?`<button class="btn primary song-card-process" data-song-process-selected="${esc(row.song_id)}" title="Продолжить пакетный разбор сохранённой или выбранной записи. Пользовательские правки сохраняются; готовый новый вариант доступен для проверки" aria-label="${esc(row.processing?.label || 'Обработать пакетно')}: ${esc(row.title)}" ${processing?'disabled aria-busy="true"':''}>${processing?'Запускаем…':esc(row.processing?.label || 'Обработать пакетно')}</button>`:`<button class="btn song-icon-btn" data-song-play="${esc(row.song_id)}" title="Проиграть песню «${esc(row.title)}»" aria-label="Проиграть ${esc(row.title)}" ${row.capabilities?.audio ? '' : 'disabled'}>${SongSheet.icon('play')}</button>`}
        ${row.status !== 'recording' ? `<button class="btn song-icon-btn" data-song-copy="${esc(row.song_id)}" title="Скопировать тексты и аккорды песни" aria-label="Скопировать тексты и аккорды песни">${SongSheet.icon('copy')}</button>` : ''}
        ${canProcessSelected&&row.capabilities?.audio?`<button class="btn song-icon-btn" data-song-play="${esc(row.song_id)}" title="Проиграть сохранённую запись" aria-label="Проиграть ${esc(row.title)}">${SongSheet.icon('play')}</button>`:''}
        <button class="btn song-icon-btn" data-song-karaoke="${esc(row.song_id)}" title="Проиграть песню «${esc(row.title)}» в режиме караоке (минусовка)" aria-label="Караоке: ${esc(row.title)}" ${row.karaoke?.state === 'ready' ? '' : 'disabled'}>${SongSheet.icon('karaoke')}</button>
        <button class="btn song-icon-btn" data-song-stop="${esc(row.song_id)}" title="Остановить песню «${esc(row.title)}» и вернуться к началу; отменить подготовку записи" aria-label="Остановить ${esc(row.title)}" hidden>${SongSheet.icon('stop')}</button>
        <button class="btn song-icon-btn" data-song-open="${esc(row.song_id)}" title="Редактировать текст, аккорды и свойства песни «${esc(row.title)}»" aria-label="Редактировать ${esc(row.title)}">${SongSheet.icon('edit')}</button>
        <button class="btn song-icon-btn ghost" data-song-delete="${esc(row.song_id)}" title="Удалить карточку песни «${esc(row.title)}»; перед удалением появится подтверждение" aria-label="Удалить ${esc(row.title)}">${SongSheet.icon('trash')}</button>
      </div>
    </article>`;
  }
  async function setRating(button) {
    const songId = button.closest(".song-rating")?.dataset.songRatingSong;
    const rating = Number(button.dataset.songRatingValue);
    const row = state.rows.find(item => item.song_id === songId);
    if (!row || !Number.isInteger(rating) || rating < 1 || rating > 5) return;
    const group = button.closest(".song-rating");
    const siblings = [...(group?.querySelectorAll("button") || [])];
    siblings.forEach(item => item.disabled = true);
    try {
      let saved;
      try {
        saved = await call('song_patch', {song_id: songId, base_revision: row.revision, changes: {rating}});
      } catch (error) {
        if (!/уже изменилась/i.test(error.message || "")) throw error;
        const fresh = await call("song_get", {song_id: songId});
        saved = await call('song_patch', {song_id: songId, base_revision: fresh.revision, changes: {rating}});
      }
      const index = state.rows.findIndex(item => item.song_id === songId);
      if (index >= 0) state.rows[index] = {...state.rows[index], ...saved};
      if (state.current?.song_id === songId) state.current = {...state.current, ...saved};
      renderList();
      const detailRating = $s("songs-detail")?.querySelector(".song-rating");
      if (detailRating && state.current?.song_id === songId) detailRating.outerHTML = ratingMarkup(saved);
      const detail = $s("songs-detail");
      detail?.querySelectorAll(".song-rating button").forEach(item => item.addEventListener("click", event => {
        event.stopPropagation();
        setRating(item);
      }));
      toast(`Оценка сохранена: ${rating} из 5`);
    } catch (error) {
      siblings.forEach(item => item.disabled = false);
      toast(error.message || 'Не удалось сохранить оценку', 4000, 'bad');
    }
  }
  async function addSongTag(button, customTag) {
    const songId = customTag ? button.dataset.songTagCustomForm : button.dataset.songTagSong;
    const tag = String(customTag || button.dataset.songTagOption || "").trim().replace(/\s+/g, " ").slice(0, 40);
    const row = state.rows.find(item => item.song_id === songId);
    if (!row || !tag || (row.tags || []).some(existing => existing.toLocaleLowerCase() === tag.toLocaleLowerCase())) return;
    button.disabled = true;
    try {
      const tags = [...new Set([...(row.tags || []), tag])];
      const saved = await call('song_patch', {song_id: songId, base_revision: row.revision, changes: {tags}});
      const index = state.rows.findIndex(item => item.song_id === songId);
      if (index >= 0) state.rows[index] = {...state.rows[index], ...saved};
      if (!state.tagOptions.some(existing => existing.toLocaleLowerCase() === tag.toLocaleLowerCase())) state.tagOptions.push(tag);
      renderTagFilters();
      const customInput = customTag ? button.closest("form")?.querySelector("input") : null;
      if (customInput) customInput.value = "";
      renderList();
      toast(`Тег добавлен: ${tag}`);
    } catch (error) {
      button.disabled = false;
      toast(error.message || 'Не удалось добавить тег', 4000, 'bad');
    }
  }
  async function removeSongTag(button) {
    const songId = button.closest('[data-song-id]')?.dataset.songId;
    const tag = button.dataset.songCardTag;
    const row = state.rows.find(item => item.song_id === songId);
    if (!row || !tag) return;
    button.disabled = true;
    try {
      const tags = (row.tags || []).filter(item => item !== tag);
      const saved = await call('song_patch', {song_id: songId, base_revision: row.revision, changes: {tags}});
      const index = state.rows.findIndex(item => item.song_id === songId);
      if (index >= 0) state.rows[index] = {...state.rows[index], ...saved};
      renderList();
      toast(`Тег убран: ${tag}`);
    } catch (error) {
      button.disabled = false;
      toast(error.message || 'Не удалось убрать тег', 4000, 'bad');
    }
  }
  function renderList() {
    const list = $s("songs-list"), empty = $s("songs-empty");
    if (!list || !empty) return;
    list.innerHTML = state.rows.map(card).join("");
    updateCardPlayback();
    bindCaptureStops(list);
    bindBatchRestarts(list);
    empty.hidden = state.rows.length > 0;
    list.hidden = state.rows.length === 0;
    list.querySelectorAll("[data-song-open]").forEach((el) => {
      el.addEventListener("click", event => {
        if (el.tagName !== "BUTTON" && event.target.closest('button,input,textarea,select,a,form')) return;
        openSong(el.dataset.songOpen);
      });
      if (el.tagName !== "BUTTON") el.addEventListener("keydown", (event) => { if (event.target===el && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openSong(el.dataset.songOpen); } });
    });
    list.querySelectorAll("[data-song-title-edit]").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      openTitleEditor(button.dataset.songTitleEdit);
    }));
    list.querySelectorAll('[data-song-play]').forEach(el=>el.addEventListener('click',()=>playCard(el.dataset.songPlay)));
    list.querySelectorAll('[data-song-process-selected]').forEach(el=>el.addEventListener('click',()=>processSelectedSource(el)));
    list.querySelectorAll('[data-song-karaoke]').forEach(el=>el.addEventListener('click',()=>playCard(el.dataset.songKaraoke,'instrumental')));
    list.querySelectorAll('[data-song-stop]').forEach(el=>el.addEventListener('click',stopCard));
    list.querySelectorAll('[data-song-tag-add]').forEach(button=>button.addEventListener('click', event=>{
      event.stopPropagation();
      const menu = button.closest('.song-card')?.querySelector('[data-song-tag-menu]');
      if (menu) menu.hidden = !menu.hidden;
    }));
    list.querySelectorAll('[data-song-tag-option]').forEach(button=>button.addEventListener('click', event=>{
      event.stopPropagation();
      addSongTag(button);
    }));
    list.querySelectorAll('[data-song-tag-custom-form]').forEach(form=>form.addEventListener('submit', event=>{
      event.preventDefault();
      event.stopPropagation();
      const input = form.querySelector("input");
      const button = form.querySelector("button[type=submit]");
      if (button) button.dataset.songTagCustomForm = form.dataset.songTagCustomForm;
      if (input?.value.trim() && button) addSongTag(button, input.value);
    }));
    list.querySelectorAll('[data-song-card-tag]').forEach(el=>el.addEventListener('click', event=>{
      event.stopPropagation();
      removeSongTag(el);
    }));
    list.querySelectorAll('[data-song-rating-value]').forEach(el=>el.addEventListener('click', event=>{
      event.stopPropagation();
      setRating(el);
    }));
    list.querySelectorAll('[data-song-copy]').forEach(el=>el.addEventListener('click', async event=>{
      event.stopPropagation();
      const row=state.rows.find(item=>item.song_id===el.dataset.songCopy);
      if(!row) return;
      const source=row.guitar_sheet?.length||row.phrases?.length ? row : await call('song_get',{song_id:row.song_id});
      const text=SongSheet.plainText(source);
      try {
        if(navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
        else { const area=document.createElement('textarea'); area.value=text; area.style.position='fixed'; area.style.opacity='0'; document.body.append(area); area.select(); document.execCommand('copy'); area.remove(); }
        el.title='Тексты и аккорды скопированы';
        toast('Тексты и аккорды скопированы');
      } catch(error) { toast(error.message||'Не удалось скопировать тексты и аккорды',4000,'bad'); }
    }));
    list.querySelectorAll("[data-song-delete]").forEach((el) => el.addEventListener("click", async (event) => {
      event.stopPropagation();
      const row = state.rows.find((x) => x.song_id === el.dataset.songDelete);
      if (!row || !confirm(`Удалить песню «${row.title}» вместе с аудиозаписью, минусовкой и результатами обработки?`)) return;
      try { await call("song_delete", { song_id: row.song_id }); await load(); if (state.current && state.current.song_id === row.song_id) closeDetail(); }
      catch (error) { toast(error.message || "Не удалось удалить песню", 4000, "bad"); }
    }));
  }
  function bindCaptureStops(container) {
    container.querySelectorAll('[data-song-stop-capture]').forEach(button => button.addEventListener('click', async event => {
      event.stopPropagation();
      const id = button.dataset.songStopCapture;
      if (stoppingCaptures.has(id)) return;
      stoppingCaptures.add(id);
      document.querySelectorAll('[data-song-stop-capture]').forEach(el => {
        if (el.dataset.songStopCapture === id) { el.disabled = true; el.setAttribute('aria-busy','true'); el.textContent = 'Сохраняем запись…'; }
      });
      try {
        const saved = await call('song_capture', {song_id:id, action:'stop'});
        toast(saved.status === 'running' ? 'Запись сохранена · пакетная обработка запущена.' : saved.stage_label || 'Запись завершена.');
      } catch (error) { toast(error.message || 'Не удалось завершить запись', 5000, 'bad'); }
      finally { stoppingCaptures.delete(id); await load(); }
    }));
  }
  function bindBatchRestarts(container) {
    container.querySelectorAll('[data-song-restart-batch]').forEach(button=>button.addEventListener('click',async event=>{
      event.stopPropagation();const id=button.dataset.songRestartBatch;
      if(processingSongs.has(id))return;
      processingSongs.add(id);button.disabled=true;
      try { await call('song_restart_batch',{song_id:id}); processingSongs.delete(id); await load(); toast('Пакетный разбор запущен заново. Предыдущие результаты и ваши правки сохранены.'); }
      catch(error) { toast(error.message||'Не удалось перезапустить обработку',5000,'bad'); }
      finally { processingSongs.delete(id);button.disabled=false; }
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
    if (hint) hint.textContent = `${saved.artist ? `${saved.artist} · ` : ""}${currentStatus(saved)}`;
    const step = document.querySelector('.song-step.on');
    if (step) step.innerHTML = `<b>${stageIndex(saved)+1}. ${esc(stagesFor(saved)[stageIndex(saved)][1])}</b>${esc(saved.stage_label)}`;
    const sheet = document.getElementById('song-guitar-content');
    if (sheet) {
      sheet.innerHTML = guitarHtml(saved);
      SongSheet.bindToolbar(saved, () => state.current, () => {
        sheet.innerHTML=guitarHtml(state.current);
        const player=document.getElementById('song-review-audio');
        if(player)SongSheet.updatePlayback(saved.song_id,Number(player.dataset.clipStart||0)+player.currentTime*1000,false);
      });
      const player=document.getElementById('song-review-audio');
      if(player)SongSheet.updatePlayback(saved.song_id,Number(player.dataset.clipStart||0)+player.currentTime*1000,false);
    }
  }
  function renderDetail(row) {
    const detail = $s("songs-detail");
    if (!detail) return;
    SongReview.stop();
    state.current = row;
    const active = stageIndex(row);
    const stages = stagesFor(row);
    detail.hidden = false;
    const instrumentalState = row.karaoke?.state || "absent";
    const captureAction = row.status === "running" && row.stage === 'sources' ? '' : row.status === "running" ? `<button class="btn" id="song-cancel-analysis">Отменить обработку</button>` : row.status === "recording" ? "" : row.capabilities?.audio ? `<button class="btn primary" id="song-start-full">Полный разбор песни</button><button class="btn" id="song-start-transcribe">Распознать текст</button>` : `${captureButton("song-start-mic-capture", "mic", "Запись с микрофона")}${captureButton("song-start-system-capture", "system", "Запись системного звука")}`;
    const guitar = guitarHtml(row);
    const sourceOptions=(row.source_candidates||[]).filter(c=>c.kind==='youtube')
      .sort((a,b)=>Number(String(b.candidate_id).startsWith('reference:'))-Number(String(a.candidate_id).startsWith('reference:'))||(b.match_score||0)-(a.match_score||0))
      .filter((c,i,all)=>{const key=videoKey(c);return !key||all.findIndex(x=>videoKey(x)===key)===i;});
    const sourceStage=(row.stage==='sources'||row.status==='needs_input')&&!row.audio?.path ? `<section class="song-source-stage" aria-label="Выбор записи"><div class="song-source-stage-head"><div><h3>${row.status==='running'?'Ищем запись':'Выберите исполнение'}</h3><p class="hint">${row.status==='running'?'Поиск или загрузка уже запущены. По завершении начнётся пакетный разбор.':'У каждой найденной записи есть отдельный выбор. Нажмите «Обработать пакетно» у нужного исполнения: загрузка и разбор начнутся автоматически.'}</p></div><div class="actions">${row.status==='running'?'<button class="btn" id="song-cancel-source">Отменить обработку</button>':''}<button class="btn" id="song-retry-source" ${row.status==='running'?'disabled':''}>Искать ещё раз</button></div></div>${sourceOptions.length?`<div class="song-source-options">${sourceOptions.map(c=>`<article class="song-source-option"><div><div class="song-source-title">${sourceTitleMarkup(c)}</div><p>${esc(c.artist||'Исполнитель не указан')}${c.duration?` · ${Math.round(c.duration)} с`:''}</p></div><div class="actions"><button class="btn small" data-song-source-select="${esc(c.candidate_id)}" ${row.status==='running'?'disabled':''}>${c.candidate_id===row.selected_source_id?'Выбрано':'Выбрать'}</button><button class="btn primary small" data-song-source="${esc(c.candidate_id)}" ${row.status==='running'?'disabled':''}>Обработать пакетно</button></div></article>`).join('')}</div>`:row.status==='running'?'<p class="hint" role="status">Поиск запущен. Результаты появятся здесь.</p>':'<p class="hint">Подходящие записи пока не найдены. Запустите поиск ещё раз или добавьте ссылку ниже.</p>'}</section>`:'';
    const sources = (row.source_candidates || []).filter(c => c.source_check?.status !== 'unavailable' && !String(c.candidate_id||'').startsWith('text-reference:') && !String(c.candidate_id||'').startsWith('chord-reference:')).map(c => {
      const yandex=/^yandexmusic:\/\//i.test(c.url||'')||/^https?:\/\/music\.yandex\./i.test(c.url||'');
      const search=c.kind==='search'||String(c.candidate_id||'').startsWith('web-search:');
      const direct=c.kind==='reference'||c.kind==='lyrics'||c.kind==='chords';
      const link=/^(https?:\/\/|yandexmusic:\/\/)/i.test(c.url||'')?`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${search?'Открыть поисковую выдачу':yandex?'Открыть поиск в Яндекс Музыке':'Открыть источник'}</a>`:'';
      const yandexAction=yandex&&!row.audio?.path?`<button class="btn small" data-song-yandex-capture="${esc(c.url)}" ${['running','recording'].includes(row.status)?'disabled':''}>Открыть Яндекс Музыку и начать запись</button><p class="song-note">Выберите там нужное исполнение и включите песню; после остановки записи полный разбор начнётся автоматически.</p>`:'';
      const selected=c.candidate_id===row.selected_source_id;
      const canProcess=c.kind==='youtube'&&!sourceStage;
      const processAction=canProcess?`<button class="btn small primary" data-song-source="${esc(c.candidate_id)}" ${row.audio?.path||row.phrases?.length||['running','recording'].includes(row.status)?'disabled title="Для другой записи создайте отдельную карточку"':''}>${selected?'Запустить обработку':'Обработать пакетно'}</button>`:'';
      const check=c.source_check||{};
      const selectedRecording=c.kind==='youtube'&&c.url===row.source_ref&&row.audio?.path;
      const currentRecording=!!(row.audio?.path||row.phrases?.length);
      const selectAction=direct?`<button class="btn small" data-text-source-existing="${esc(c.candidate_id)}" ${['running','recording'].includes(row.status)?'disabled':''}>Сверить текст</button>`:c.kind==='youtube'?`<button class="btn small" data-song-source-select="${esc(c.candidate_id)}" ${['running','recording'].includes(row.status)?'disabled':''}>${selected?(direct||currentRecording?'Выбран для сверки':'Запись выбрана'):(direct||currentRecording?'Выбрать для сверки':'Выбрать запись')}</button>`:'';
      const checkAction=direct?`${check.status==='available'?`<span class="song-source-verified" role="status">Страница открывается${check.chords_extractable?` · аккорды считываются (${(check.chord_names||[]).slice(0,8).map(esc).join(', ')})`:''}</span>`:check.status==='checking'?`<span class="song-note" role="status">Проверяем страницу…</span>`:`<button class="btn small" data-song-source-check="${esc(c.candidate_id)}">Проверить страницу</button>`}${selectAction}`:selectAction;
      const text=c.plain_lyrics||c.synced_lyrics?`<details><summary>Текст для сверки</summary><p class="song-source-text">${esc(c.plain_lyrics||c.synced_lyrics)}</p></details>`:'';
      const note=search?c.note||'Поисковая выдача: выберите конкретную страницу, затем проверьте её доступность и содержание.':c.note;
      return `<div class="song-source"><div class="song-source-title">${sourceTitleMarkup(c)}</div><p>${esc(c.artist||'')}${c.duration?` · ${Math.round(c.duration)} с`:''}</p>${selectedRecording?'<span class="song-source-verified">Исполнение, по которому сделан разбор</span>':''}${link}${processAction}${checkAction}${yandexAction}${note?`<p class="song-note">${esc(note)}</p>`:''}${text}</div>`;
    }).join('');
    const errors = (row.analysis_errors || []).map(e => `<p class="song-errors">${esc(e.step)}: ${esc(e.error)}</p>`).join('');
    detail.innerHTML = `<div class="songs-detail-head"><div><h2><button type="button" class="song-title song-title-edit" data-song-title-edit="${esc(row.song_id)}" aria-label="Изменить название песни">${esc(row.title)}</button></h2><p class="hint">${row.artist ? `${esc(row.artist)} · ` : ""}${esc(currentStatus(row))}</p>${ratingMarkup(row)}</div><div class="songs-detail-head-actions">${restartBatchButton(row)}<button class="btn songs-detail-close" id="songs-detail-close">Закрыть</button></div></div>
      ${row.status === 'recording' ? `<div class="song-recording-banner">${liveCapture(row)}<span>${row.audio?.capture_source==='microphone'?'Автостоп после 5 секунд тишины, когда запись уже началась.':'Записывается системный звук.'}</span>${stopCaptureButton(row.song_id)}</div>` : ''}
      <div class="song-steps" aria-label="Этапы разбора">${stages.map((s, i) => `<div ${s[0]==="text_reference"?'data-song-reference-stage role="button" tabindex="0" aria-label="Открыть источник сверки или запустить сверку текста"':''} class="song-step ${i < active && (s[0]!=="text_reference" || row.text_reference?.references?.length) ? "done" : ""} ${s[0]==="text_reference" && !row.text_reference?.references?.length && row.stage!=="text_reference" ? "song-step-unused" : ""} ${i === active ? "on" : ""}"><div><b>${i + 1}. ${esc(s[1])}</b>${s[0] === "text_reference" ? esc(textReferenceStatus(row)) : i < active ? "" : i === active ? esc(row.stage_label || "Выполняется") : "Ожидает"}</div>${s[0]==="text_reference"?textReferenceStageSource(row):''}<div class="song-step-actions">${stageActions(row, s[0])}${stageRestart(row, s[0])}</div></div>`).join("")}</div>
      ${row.processing?.can_resume?`<div class="actions"><button class="btn primary" id="song-resume" data-song-process-selected="${esc(row.song_id)}" title="Продолжить пакетный разбор записи; сохранить пользовательские правки">${esc(row.processing.label)}</button></div>`:''}
      ${row.processing?.busy&&row.status!=='recording'?'<div class="actions"><button class="btn" id="song-stop-processing" title="Отменить текущий разбор. После остановки можно продолжить обработку; запись и пользовательские правки сохранятся">Остановить обработку</button></div>':''}
      ${sourceStage}
      ${textReferencePanel(row)}
      ${instrumentalPanel(row, instrumentalState)}
      <div class="song-detail-grid"><div class="song-preview">${guitar ? `<h3>Текст с аккордами</h3>${SongSheet.toolbar(row)}<div id="song-guitar-content">${guitar}</div>` : ""}<details><summary>Проверить и исправить распознавание</summary>${SongReview.html(row)}</details></div>
      <details class="song-properties" ${row.status === 'needs_input' ? 'open' : ''}><summary>Источники, свойства и правка аккордов</summary>${row.recording_version ? `<p class="song-note"><b>Выбранная запись:</b> ${esc(row.recording_version)}</p>` : ""}${errors}<label class="f">Название песни<input id="song-edit-title" value="${esc(row.title)}"></label><label class="f">Поисковый запрос<input id="song-edit-search-query" value="${esc(row.search_query || (row.source === 'query' ? row.title : ''))}" placeholder="Название и исполнитель для поиска"></label><label class="f">Исполнитель<input id="song-edit-artist" value="${esc(row.artist || "")}"></label><p class="hint">Поисковый запрос хранится отдельно: его можно уточнить, не меняя название песни. Источник: ${esc(row.source || "поиск")}.</p><div class="actions"><button class="btn" id="song-save-meta">Сохранить название</button><button class="btn" id="song-refresh">Обновить карточку</button><button class="btn" id="song-find-sources" ${row.status === "running" || row.status === "recording" ? "disabled" : ""}>Найти записи и текст</button>${captureButton("song-attach-audio", "upload", "Добавить аудиофайл")}${captureAction}</div><input id="song-audio-input" type="file" accept="audio/*,video/*" hidden><p class="hint" id="song-audio-hint">${row.audio?.path ? `Файл: ${esc(row.audio.original_name || row.audio.format || "аудио")}${row.audio.duration_ms ? ` · ${Math.round(row.audio.duration_ms / 1000)} с` : ""}` : "Можно импортировать локальную запись, если системный захват недоступен."}</p><h3>Добавить свой источник</h3><label class="f">Ссылка на запись или публикацию<input id="song-manual-url" type="url" maxlength="2048" placeholder="https://…"></label><label class="f">Текст песни для сверки (необязательно)<textarea id="song-manual-lyrics" maxlength="100000" rows="4"></textarea></label><p class="hint">Прямая ссылка YouTube запускает загрузку и пакетный разбор автоматически. Ссылки Яндекс Музыки можно открыть и записать системный звук. Остальные ссылки сохраняются как справочные источники.</p><button class="btn" id="song-add-manual-source" ${row.status === 'running' || row.status === 'recording' ? 'disabled' : ''}>Добавить источник</button><p class="hint" id="song-manual-source-result" role="status"></p>${SongSheet.html(row)}<h3>Источники</h3>${confirmedSources(row)}${sources ? `<h4>Другие записи и поиск</h4>${sources}` : ""}</details></div>`;
    bindCaptureStops(detail);
    bindBatchRestarts(detail);
    async function textSourceAction(button,action,args) {
      button.disabled=true;
      const message=$s('song-text-source-result');
      if(message)message.textContent=action==='add'?'Читаем страницу и сравниваем текст…':'Сохраняем…';
      try {
        const saved=await call('song_text_source_'+action,{song_id:row.song_id,base_revision:state.current.revision,...args});
        renderDetail(saved);await load();
        if(action==='confirm' || action==='add') {
          const corrections=$s('song-text-corrections');
          if(corrections) { corrections.open=true; corrections.scrollIntoView({block:'nearest'}); }
        }
        if(action==='apply')toast('Выбранные исправления сохранены');
        if(action==='apply_all')toast('Все найденные исправления сохранены');
      } catch(error) {if(message)message.textContent=error.message||'Не удалось выполнить сверку';button.disabled=false;}
    }
    $s('song-text-source-add')?.addEventListener('click',event=>textSourceAction(event.currentTarget,'add',{
      url:$s('song-text-source-url').value,lyrics:$s('song-text-source-lyrics').value}));
    detail.querySelectorAll('[data-text-source-existing]').forEach(button=>button.addEventListener('click',()=>{
      const ref=row.source_candidates.find(c=>c.candidate_id===button.dataset.textSourceExisting);
      if(ref)textSourceAction(button,'add',{url:ref.url,lyrics:ref.plain_lyrics||''});
    }));
    detail.querySelectorAll('[data-text-source-confirm]').forEach(button=>button.addEventListener('click',()=>
      textSourceAction(button,'confirm',{candidate_id:button.dataset.textSourceConfirm})));
    detail.querySelectorAll('[data-text-apply-all]').forEach(button=>button.addEventListener('click',()=>
      textSourceAction(button,'apply_all',{})));
    detail.querySelectorAll('[data-text-select-all]').forEach(button=>button.addEventListener('click',()=>{
      const boxes=[...detail.querySelectorAll('[data-text-correction]:not(:disabled)')], select=boxes.some(box=>!box.checked);
      boxes.forEach(box=>{box.checked=select;});
      button.textContent=select?'Снять выделение':'Выбрать все';
    }));
    detail.querySelectorAll('[data-text-correction-play]').forEach(button=>button.addEventListener('click',()=>
      playCorrection(button,row,(row.text_reference?.candidates||[])[Number(button.dataset.textCorrectionPlay)]||{})));
    $s('song-text-source-apply')?.addEventListener('click',event=>textSourceAction(event.currentTarget,'apply',{
      indexes:[...detail.querySelectorAll('[data-text-correction]:checked')].map(el=>Number(el.dataset.textCorrection))}));
    const referenceStage=detail.querySelector('[data-song-reference-stage]');
    if(referenceStage) {
      const sourceLink=referenceStage.querySelector('[data-song-reference-source]');
      const button=referenceStage.querySelector('button');
      referenceStage.setAttribute('aria-disabled',String(!sourceLink&&(!button||button.disabled)));
      const activate=event=>{
        if(event.target.closest('button,a')||(!sourceLink&&!button)||(!sourceLink&&button.disabled))return;
        if(event.type==='keydown'&&!['Enter',' '].includes(event.key))return;
        event.preventDefault();(sourceLink||button).click();
      };
      referenceStage.addEventListener('click',activate);
      referenceStage.addEventListener('keydown',activate);
    }
    $s("song-stop-processing")?.addEventListener("click", async event => {
      event.currentTarget.disabled=true;
      try { await call('song_cancel',{song_id:row.song_id}); await load(); }
      catch(error) { toast(error.message||'Не удалось остановить обработку',5000,'bad'); event.currentTarget.disabled=false; }
    });
    detail.querySelectorAll('[data-song-rerun-step]').forEach(button => button.addEventListener('click', async () => {
      const step = button.dataset.songRerunStep;
      button.disabled = true;
      try {
        await call('song_rerun_step', {song_id: row.song_id, step});
        renderDetail(await call('song_get', {song_id: row.song_id}));
        await load();
      } catch (error) {
        toast(error.message || 'Не удалось перезапустить этап', 5000, 'bad');
        button.disabled = false;
      }
    }));
    detail.querySelectorAll('[data-song-rerun-karaoke]').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await call('song_instrumental_prepare', {song_id: row.song_id, force: true});
        renderDetail(await call('song_get', {song_id: row.song_id}));
        await load();
      } catch (error) {
        toast(error.message || 'Не удалось перезапустить минусовку', 5000, 'bad');
        button.disabled = false;
      }
    }));
    detail.querySelectorAll('[data-song-restart-sources]').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await call('song_find_sources', {song_id: row.song_id});
        renderDetail(await call('song_get', {song_id: row.song_id}));
        await load();
      } catch (error) {
        toast(error.message || 'Не удалось повторить поиск источников', 5000, 'bad');
        button.disabled = false;
      }
    }));
    $s("song-resume")?.addEventListener("click", event => processSelectedSource(event.currentTarget));
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
        $s("song-manual-source-result").textContent = saved.status === 'running'
          ? 'Ссылка добавлена. Загружаем запись и автоматически запускаем пакетный разбор.'
          : saved.audio?.path
            ? 'Источник добавлен. Запись уже есть; нажмите «Полный разбор песни», чтобы продолжить.'
            : 'Источник сохранён. Если это Яндекс Музыка, откройте её кнопкой ниже и запишите системный звук; для YouTube выберите «Обработать пакетно». Текст по ссылке сам по себе не заменяет распознавание записи.';
      } catch (error) { result.textContent = error.message || 'Не удалось добавить источник'; button.disabled = false; }
    };
    $s("song-find-sources").onclick = async () => {
      try { renderDetail(await call("song_find_sources", {song_id: row.song_id})); await load(); }
      catch (error) { toast(error.message, 5000, "bad"); }
    };
    detail.querySelectorAll('[data-song-source]').forEach(button => button.onclick = async () => {
      button.disabled=true;button.textContent='Запускаем разбор…';
      try { renderDetail(await call("song_process_source", {song_id: row.song_id, candidate_id: button.dataset.songSource})); await load(); }
      catch (error) { toast(error.message, 5000, "bad"); }
    });
    detail.querySelectorAll('[data-song-source-check]').forEach(button => button.onclick = async () => {
      button.disabled=true;button.textContent='Проверяем страницу…';
      try { renderDetail(await call('song_source_check',{song_id:row.song_id,candidate_id:button.dataset.songSourceCheck})); await load(); }
      catch(error) { toast(error.message||'Не удалось проверить страницу',5000,'bad');button.disabled=false;button.textContent='Проверить страницу'; }
    });
    detail.querySelectorAll('[data-song-source-select]').forEach(button => button.onclick = async () => {
      button.disabled=true;
      try { renderDetail(await call('song_source_select',{song_id:row.song_id,candidate_id:button.dataset.songSourceSelect})); await load(); }
      catch(error) { toast(error.message||'Не удалось выбрать источник',5000,'bad');button.disabled=false; }
    });
    detail.querySelectorAll('[data-song-yandex-capture]').forEach(button => button.onclick = async () => {
      button.disabled=true;
      try {
        await call('song_capture',{song_id:row.song_id,action:'start',source:'system',target:'all',backend:'auto'});
        await load();
        const native=window.api?.();
        if(native?.open_url) await native.open_url(button.dataset.songYandexCapture);
        else window.open(button.dataset.songYandexCapture,'_blank','noopener');
        toast('Запись системного звука идёт. Выберите исполнение в Яндекс Музыке; после остановки начнётся пакетный разбор.',7000);
      } catch(error) { toast(error.message||'Не удалось открыть Яндекс Музыку или начать запись',6000,'bad');button.disabled=false; }
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
    $s("song-instrumental-add-audio")?.addEventListener("click", () => $s("song-audio-input")?.click());
    async function downloadInstrumental(button) {
      const defaultLabel=button.dataset.defaultLabel || button.textContent;
      button.dataset.defaultLabel=defaultLabel;button.disabled=true;button.textContent='Готовим файл…';
      try {
        const clip=await call('song_full_audio',{song_id:row.song_id,variant:'instrumental'});
        const blob=await (await fetch(clip.data)).blob(), url=URL.createObjectURL(blob), link=document.createElement('a');
        const safe=(row.title||'Минусовка').replace(/[\\/:*?"<>|]+/g,' ').trim()||'Минусовка';
        link.href=url;link.download=`${safe} — минусовка.mp3`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(error) { toast(error.message||'Не удалось получить файл минусовки',5000,'bad'); }
      finally { button.disabled=false;button.textContent=defaultLabel; }
    }
    detail.querySelectorAll('#song-download-instrumental,[data-song-download-instrumental-stage]').forEach(button=>button.addEventListener('click',()=>downloadInstrumental(button)));
    detail.querySelector('[data-song-confirm-review]')?.addEventListener('click', async event => {
      const button=event.currentTarget;button.disabled=true;
      try {
        const saved=await call('song_finish_review',{song_id:row.song_id,base_revision:state.current.revision,lyrics_checked:true,chords_checked:true});
        state.current=saved;renderDetail(saved);await load();toast('Текст и аккорды подтверждены');
      } catch(error) { toast(error.message||'Не удалось подтвердить текст и аккорды',5000,'bad');button.disabled=false; }
    });
    detail.querySelector('[data-song-download-text]')?.addEventListener('click',()=>{
      const text=SongSheet.plainText(state.current||row), blob=new Blob([text],{type:'text/plain;charset=utf-8'}), url=URL.createObjectURL(blob), link=document.createElement('a');
      const safe=(row.title||'Песня').replace(/[\\/:*?"<>|]+/g,' ').trim()||'Песня';
      link.href=url;link.download=`${safe}.txt`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    $s("song-refresh").onclick = () => openSong(row.song_id,true,true);
    $s("song-cancel-analysis")?.addEventListener("click", async () => {
      try { renderDetail(await call("song_cancel", {song_id: row.song_id})); }
      catch (error) { toast(error.message, 5000, "bad"); }
    });
    $s("songs-detail-close").onclick = closeDetail;
    detail.querySelector("[data-song-title-edit]")?.addEventListener("click", () => {
      const properties = detail.querySelector(".song-properties");
      const title = $s("song-edit-title");
      if (properties) properties.open = true;
      title?.focus();
      title?.select();
    });
    detail.querySelectorAll(".song-rating button").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      setRating(button);
    }));
    $s("song-save-meta").onclick = async () => {
      try { const saved = await call("song_patch", { song_id: row.song_id, base_revision: state.current.revision, changes: { title: $s("song-edit-title").value, search_query: $s("song-edit-search-query").value, artist: $s("song-edit-artist").value } }); state.current = saved; await load(); renderDetail(saved); toast("Карточка сохранена"); }
      catch (error) { toast(error.message || "Не удалось сохранить", 4000, "bad"); }
    };
    $s("song-start-system-capture")?.addEventListener("click", async () => {
      try { await call("song_capture", { song_id: row.song_id, action: "start", source: "system", target: "all", backend: "auto" }); await load(); toast("Ожидаем звук компьютера. Включите песню."); }
      catch (error) { toast(error.message || "Не удалось начать запись", 5000, "bad"); }
    });
    $s("song-start-mic-capture")?.addEventListener("click", async () => {
      try { await call("song_capture", { song_id: row.song_id, action: "start", source: "microphone" }); await load(); toast("Микрофон подключён. Запись начнётся, когда появится звук."); }
      catch (error) { toast(error.message || "Не удалось начать запись", 5000, "bad"); }
    });
    $s("song-start-transcribe")?.addEventListener("click", async () => {
      const dialog=$s('song-transcribe-dialog');
      if (!dialog) return;
      state.transcribeSongId=row.song_id;
      dialog.querySelectorAll('[data-song-model]').forEach(input=>{
        input.checked=['whisper','qwen_large'].includes(input.dataset.songModel);
      });
      dialog.hidden=false;
      dialog.querySelector('[data-song-model]')?.focus();
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
  window.onSongPlayback=(songId,status)=>{
    cardPlayback={songId,...status};updateCardPlayback();
    if(status.ended&&assistantQueueRunning)playAssistantQueueNext();
  };
  async function playAssistantQueueNext() {
    if(!assistantSongQueue.length){assistantQueueRunning=false;return;}
    await playCard(assistantSongQueue.shift());
  }
  async function runAssistantSongs(event) {
    document.querySelector('nav button[data-p="tasks"]')?.click();
    document.getElementById('tasks-songs-tab')?.click();
    await load();
    if(event.action==='open')return openSong(event.songs?.[0]?.song_id,true);
    assistantSongQueue=(event.songs||[]).map(song=>song.song_id);
    assistantQueueRunning=assistantSongQueue.length>0;
    if(assistantQueueRunning)await playAssistantQueueNext();
  }
  function stopCard() {
    assistantSongQueue=[];assistantQueueRunning=false;
    cardRequest++;openRequest++;SongReview.stop();cardPlayback={};updateCardPlayback();
  }
  async function processSelectedSource(button) {
    const songId=button.dataset.songProcessSelected;
    if(!songId||processingSongs.has(songId))return;
    const row=state.rows.find(item=>item.song_id===songId);
    if(!row)return;
    processingSongs.add(songId);button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='Запускаем…';
    try {
      await call('song_resume',{song_id:songId});
      await load();
    } catch(error) { toast(error.message||'Не удалось запустить пакетную обработку',5000,'bad'); }
    finally {
      processingSongs.delete(songId);
      if(state.rows.some(item=>item.song_id===songId))renderList();
    }
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
      else await SongReview.playVariant(variant,false);
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
      else if(typeof window !== 'undefined') window.scrollTo({top:0,left:0,behavior:'auto'});
      return true;
    } catch(error) { if(request===openRequest)toast(error.message || 'Песня не найдена',4000,'bad');return false; }
  }
  async function openTitleEditor(id) {
    if (!await openSong(id, true)) return;
    const properties = $s("songs-detail")?.querySelector(".song-properties");
    const title = $s("song-edit-title");
    if (properties) properties.open = true;
    title?.focus();
    title?.select();
    title?.scrollIntoView({behavior:"smooth",block:"center"});
  }
  function closeDetail() { stopCard(); state.current = null; const detail = $s("songs-detail"); if (detail) { detail.hidden = true; detail.innerHTML = ""; } }
  async function load() { try { if (!state.tagOptions.length) { state.tagOptions = await call("songs_tags_list") || []; renderTagFilters(); } state.rows = await call("songs_list", { q: state.query, lyrics_q: state.lyricsQuery, status: state.status, tags: state.tags, rating: state.rating, limit: 100 }) || []; renderList(); if (state.current) { const id = state.current.song_id; if (!SongReview.protectedView(id)) { const fresh = await call("song_get", {song_id:id}); if (state.current?.song_id === id && !SongReview.protectedView(id)) renderDetail(fresh); } } } catch (error) { toast(error.message || "Не удалось загрузить песни", 4000, "bad"); } }
  async function create(source, title, artist) { try { const row = await call("song_create", { source, title, artist }); await load(); renderDetail(row); if (source === "query") renderDetail(await call("song_find_sources", {song_id: row.song_id})); } catch (error) { toast(error.message || "Не удалось создать карточку", 4000, "bad"); } }
  async function createMicSong() {
    const title=`Запись с микрофона · ${new Date().toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}`;
    try {
      const row=await call('song_create',{source:'current',title,artist:''});
      await call('song_capture',{song_id:row.song_id,action:'start',source:'microphone'});
      await load();renderDetail(await call('song_get',{song_id:row.song_id}));
      toast('Микрофон подключён — ожидаем звук. После остановки начнётся распознавание текста.');
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
    ["songs-new-query", "songs-new-create", "songs-empty-query"].forEach((id) => $s(id)?.addEventListener("click", () => queryCreate("query")));
    ["songs-new-mic","songs-empty-mic"].forEach(id=>$s(id)?.addEventListener('click',createMicSong));
    $s("songs-instrumental-all")?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        const result = await call("songs_instrumental_prepare_all");
        const started = result.started?.length || 0;
        const ready = result.ready?.length || 0;
        const active = result.already_running?.length || 0;
        const missing = result.missing_audio?.length || 0;
        const errors = result.errors?.length || 0;
        toast(`В очереди: ${started} · готовы: ${ready} · уже готовятся: ${active}${missing ? ` · без аудио: ${missing}` : ""}${errors ? ` · ошибки: ${errors}` : ""}`, 6000);
        await load();
      } catch (error) { toast(error.message || "Не удалось запустить минусовки", 5000, "bad"); }
      finally { button.disabled = false; }
    });
    $s("song-create-cancel")?.addEventListener("click", closeCreateDialog);
    $s('song-transcribe-dialog')?.querySelectorAll('[data-song-model]').forEach(input=>{
      input.addEventListener('change',()=>{
        if(input.checked && ['qwen','qwen_large'].includes(input.dataset.songModel)){
          const other=input.dataset.songModel==='qwen'?'qwen_large':'qwen';
          $s('song-transcribe-dialog').querySelector(`[data-song-model="${other}"]`).checked=false;
        }
      });
    });
    $s('song-transcribe-cancel')?.addEventListener('click',()=>{
      const dialog=$s('song-transcribe-dialog'); if(dialog) dialog.hidden=true;
      state.transcribeSongId='';
    });
    $s('song-transcribe-submit')?.addEventListener('click',async event=>{
      const dialog=$s('song-transcribe-dialog'), songId=state.transcribeSongId;
      if(!dialog||!songId)return;
      const models=[...dialog.querySelectorAll('[data-song-model]:checked')].map(input=>input.dataset.songModel);
      const status=dialog.querySelector('[data-song-transcribe-status]'), button=event.currentTarget;
      if(!models.length){if(status)status.textContent='Выберите хотя бы один распознаватель';return;}
      button.disabled=true;
      if(status)status.textContent='Запускаем выбранные модели…';
      try {
        const saved=await call('song_transcribe',{song_id:songId,full:true,models});
        dialog.hidden=true;state.transcribeSongId='';
        if(state.current?.song_id===songId)renderDetail(saved);
        await load();
        toast(`Распознавание запущено: ${models.join(', ')}. Сырые варианты сохранятся для сравнения.`,6000);
      } catch(error) {
        if(status)status.textContent=error.message||'Не удалось запустить распознавание';
        else toast(error.message||'Не удалось запустить распознавание',5000,'bad');
      } finally { button.disabled=false; }
    });
    $s("song-create-submit")?.addEventListener("click", async () => {
      const input = $s("song-create-input"), value = input?.value.trim();
      if (!value) { input?.focus(); return; }
      const parts = value.split(/\s+[—-]\s+|\s+\/\s+/);
      closeCreateDialog();
      await create(createSource, parts[0].trim(), parts.slice(1).join(" — ").trim());
    });
    $s("song-create-input")?.addEventListener("keydown", (event) => { if (event.key === "Enter") $s("song-create-submit")?.click(); if (event.key === "Escape") closeCreateDialog(); });
    $s("songs-query")?.addEventListener("input", (event) => { state.query = event.target.value; clearTimeout(state.timer); state.timer = setTimeout(load, 220); });
    $s("songs-lyrics-query")?.addEventListener("input", (event) => { state.lyricsQuery = event.target.value; clearTimeout(state.timer); state.timer = setTimeout(load, 220); });
    document.querySelectorAll("[data-song-status]").forEach((el) => el.addEventListener("click", () => { document.querySelectorAll("[data-song-status]").forEach((x) => (x.classList.toggle("on", x === el), x.setAttribute("aria-pressed", String(x === el)))); state.status = el.dataset.songStatus; load(); }));
    $s("songs-tag-filters")?.addEventListener("click", event => {
      const el = event.target.closest("[data-song-tag]");
      if (!el) return;
      const requested = el.dataset.songTag || '';
      if (!requested) state.tags = [];
      else state.tags = state.tags.includes(requested) ? state.tags.filter(tag => tag !== requested) : [...state.tags, requested];
      renderTagFilters();
      load();
    });
    document.querySelectorAll("[data-song-rating]").forEach((el) => el.addEventListener("click", () => { document.querySelectorAll("[data-song-rating]").forEach((x) => (x.classList.toggle("on", x === el), x.setAttribute("aria-pressed", String(x === el)))); state.rating = el.dataset.songRating; load(); }));
    function taskTab(songs) {
      $s('p-songs').hidden = !songs; $s('tasks-work-content').hidden = songs;
      $s('tasks-songs-tab').setAttribute('aria-pressed', String(songs));
      $s('tasks-work-tab').setAttribute('aria-pressed', String(!songs));
      $s('tasks-articles-tab')?.setAttribute('aria-pressed','false'); $s('tasks-video-tab')?.setAttribute('aria-pressed','false');
      $s('p-articles') && ($s('p-articles').hidden=true); $s('p-video') && ($s('p-video').hidden=true);
      if (songs) load(); else SongReview.stop();
    }
    $s('tasks-songs-tab')?.addEventListener('click', () => taskTab(true));
    $s('tasks-work-tab')?.addEventListener('click', () => taskTab(false));
    document.addEventListener('DOMContentLoaded', () => { if (location.hash === '#songs') { document.querySelector('nav button[data-p="tasks"]')?.click(); taskTab(true); } });
  }
  bind();
  window.loadSongs = load;
  // Recover missed completion events, including workers draining after cancellation.
  setInterval(() => {
    if (!document.hidden && document.querySelector('#p-tasks.cur') && !$s('p-songs')?.hidden && state.rows.some(row=>row.processing?.busy || row.status === 'recording')) load();
  }, 5000);
  core.on("hello", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_capture", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_library_action", event => { runAssistantSongs(event).catch(error=>toast(error.message||'Не удалось выполнить действие с песнями',5000,'bad')); });
  core.on("song_library_updated", event => {
    if (state.current?.song_id === event.song_id) call('song_get',{song_id:event.song_id}).then(renderDetail).catch(()=>{});
    if (document.querySelector('#p-tasks.cur') && !$s('p-songs')?.hidden) load();
  });
  core.on("song_analysis", () => { if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load(); });
  core.on("song_instrumental", event => {
    if (state.current?.song_id === event.song_id && !SongReview.protectedView(event.song_id)) call("song_get", {song_id:event.song_id}).then(fresh=>{
      if(state.current?.song_id===event.song_id&&!SongReview.protectedView(event.song_id))renderDetail(fresh);
    }).catch(()=>{});
    if (document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) load();
  });
  core.on("settings", () => { const on = !S || !S.features || S.features.songs !== false; const b = document.querySelector('nav button[data-p="songs"]'); if (b) b.hidden = !on; if (!on && document.querySelector("#p-tasks.cur") && !$s("p-songs")?.hidden) document.querySelector('nav button[data-p="home"]')?.click(); });
})();
