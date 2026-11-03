/* Настройки записи встреч: отдельная модель распознавания, источник кадров,
   пользовательская кнопка снимка и фоновые записи по расписанию. */
(function () {
  const esc = (t) => escH(t);
  async function loadAudioApps() {
    const select = $("#pr-tap-target"), button = $("#pr-audio-apps-refresh"), out = $("#pr-audio-apps-result");
    if (!select) return;
    const selected = select.value || get("prompter.tap_target") || "all";
    button.disabled = true; out.textContent = "Проверяю приложения…";
    try {
      const result = await core.call("prompter_audio_apps");
      const apps = Array.isArray(result.apps) ? result.apps : [];
      select.replaceChildren(new Option("Весь системный звук", "all"));
      apps.forEach(app => select.add(new Option(`${app.name || app.bundle}${app.playing ? " · воспроизводит звук" : ""}`, app.bundle)));
      if (selected !== "all" && !apps.some(app => app.bundle === selected)) select.add(new Option(selected + " · сейчас не запущено", selected));
      select.value = selected;
      out.textContent = result.supported ? `Core Audio доступен · приложений: ${apps.length}` : "Core Audio не собран или недоступен. Автоматический режим использует установленный BlackHole/Loopback.";
    } catch (e) { out.textContent = e.message; }
    finally { button.disabled = false; }
  }
  $("#pr-audio-apps-refresh")?.addEventListener("click", loadAudioApps);
  let screenAccess = {}, captureAppsLoading = false;
  async function refreshCaptureAccess() {
    const native = window.api?.();
    if (!native?.screen_capture_permission) return;
    try {
      screenAccess = await native.screen_capture_permission();
      $("#pr-screen-access").hidden = !!screenAccess.granted;
      $("#pr-screen-access-status").textContent = screenAccess.pending ? "Ожидается ответ в системном окне macOS…" : "Доступ к экрану пока не подтверждён. Без него список окон и захват недоступны.";
      $("#pr-screen-allow").disabled = !!screenAccess.pending || screenAccess.request_supported === false;
      $("#pr-screen-allow").textContent = screenAccess.requested ? "Открыть настройки экрана" : "Разрешить запись экрана";
      $("#pr-capture-test").disabled = !screenAccess.granted;
      $("#pr-capture-apps-refresh").disabled = !screenAccess.granted || captureAppsLoading;
      if (screenAccess.granted && !$("#pr-capture-application").dataset.loaded) await loadCaptureApps();
    } catch (e) { $("#pr-capture-test-result").textContent = e.message; }
  }
  $("#pr-screen-allow")?.addEventListener("click", async () => {
    try { await window.api().request_screen_access(); await refreshCaptureAccess(); }
    catch (e) { $("#pr-capture-test-result").textContent = e.message; }
  });
  $("#pr-screen-settings")?.addEventListener("click", async () => {
    try { await window.api().open_screen_settings(); }
    catch (e) { $("#pr-capture-test-result").textContent = e.message; }
  });
  window.addEventListener("focus", refreshCaptureAccess);
  window.addEventListener("pywebviewready", refreshCaptureAccess);
  async function loadCaptureApps() {
    const select = $("#pr-capture-application"), button = $("#pr-capture-apps-refresh"), out = $("#pr-capture-test-result");
    if (!select || !button || captureAppsLoading) return;
    const selected = select.value || get("prompter.capture_application") || "";
    captureAppsLoading = true; button.disabled = true;
    try {
      const result = await core.call("prompter_capture_apps");
      const apps = Array.isArray(result.apps) ? result.apps : [];
      select.replaceChildren(new Option(apps.length ? "Выберите видимое окно" : "Видимые окна не найдены", ""));
      apps.forEach(app => select.add(new Option(app.title ? `${app.name} — ${app.title}` : app.name, app.key)));
      if (selected && !apps.some(app => app.key === selected)) select.add(new Option(`${selected} · сейчас не найдено`, selected));
      select.value = selected;
      select.dataset.loaded = "1";
      if (!result.supported) out.textContent = "Выбор окна приложения доступен в версии для macOS.";
      else if (!apps.length) out.textContent = "Откройте нужное окно и обновите список.";
      else if (!out.textContent || out.textContent.includes("доступен")) out.textContent = `Найдено окон: ${apps.length}`;
    } catch (e) { out.textContent = e.message; }
    finally { captureAppsLoading = false; button.disabled = false; }
  }
  $("#pr-capture-apps-refresh")?.addEventListener("click", loadCaptureApps);
  $("#pr-capture-test")?.addEventListener("click", async () => {
    const button = $("#pr-capture-test"), out = $("#pr-capture-test-result");
    button.disabled = true; out.className = "res"; out.textContent = "Проверяю разрешение и захват…";
    try {
      const result = await core.call("prompter_capture_test");
      out.className = "res ok"; out.textContent = `Захват работает · ${result.width} × ${result.height} · файл не сохранён`;
    } catch (e) { out.className = "res bad"; out.textContent = e.message; }
    finally { button.disabled = false; }
  });
  function renderCaptureFields() {
    const source = $("#pr-capture-source")?.value || "screen";
    $("#pr-region-fields")?.classList.toggle("hidden", source !== "region");
    $("#pr-app-field")?.classList.toggle("hidden", source !== "application");
  }
  $("#pr-capture-source")?.addEventListener("change", renderCaptureFields);
  function renderVideoState() {
    const enabled = !!$("#pr-frame-video")?.checked;
    const out = $("#pr-video-state");
    if (out) out.textContent = enabled
      ? "Сборка видео включена. Включите «Автоматические кадры» и запись аудио в окне встречи; MP4 появится после остановки."
      : "Сборка видео выключена. Кадры и аудио можно записывать отдельно.";
  }
  $("#pr-frame-video")?.addEventListener("change", renderVideoState);
  $("#pr-stt-download")?.addEventListener("click", async () => {
    const model = $("#pr-stt-model").value;
    const out = $("#pr-stt-result"); const b = $("#pr-stt-download");
    b.disabled = true; out.className = "res"; out.textContent = "Запускаю загрузку…";
    try { await core.call("models_get", { key: "gigaam", model }); out.className = "res ok"; out.textContent = "Загрузка началась — прогресс виден в разделе «Слух»."; }
    catch (e) { out.className = "res bad"; out.textContent = e.message; }
    finally { b.disabled = false; }
  });
  async function loadMeetingModel() {
    const out = $("#pr-stt-status"); if (!out) return;
    try { const x = await core.call("prompter_models", { model: $("#pr-stt-model").value }); out.textContent = x.installed ? `Скачана · ${x.mb} МБ` : `Не скачана · около ${x.mb} МБ`; }
    catch (_) { /* ядро могло быть ещё занято запуском */ }
  }
  function fmt(ts, local) { if (local) return local; if (!ts) return "до ручной остановки"; try { return new Date(Number(ts) * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }); } catch (_) { return "—"; } }
  async function loadSchedules() {
    const box = $("#pr-s-list"); if (!box) return;
    try {
      const rows = await core.call("prompter_schedules");
      box.innerHTML = rows.length ? rows.map(x => {
        const pf = x.preflight || {}, checks = pf.state === "ok" ? " · проверка пройдена" : pf.state === "checking" ? " · проверяю за 5 минут до начала" : "";
        const error = x.error || (pf.state === "error" ? pf.message : "");
        const canCancel = ["scheduled", "starting", "running"].includes(x.state);
        const cancelTitle = x.state === "running" ? "Остановить текущую фоновую запись и сохранить её итог" : "Отменить запланированную запись";
        return `<div class="prof" style="padding:8px 10px;margin-top:6px;display:flex;gap:8px;align-items:center"><span style="flex:1"><b>${esc(x.title || "Встреча")}</b><br><small>${fmt(x.start_at, x.start_local)} — ${fmt(x.end_at, x.end_local)} · ${esc(x.state || "")}${checks}${error ? ` · ${esc(error)}` : ""}</small></span>${canCancel ? `<button class="btn small" data-cancel-schedule="${esc(x.id)}" data-schedule-state="${esc(x.state)}" title="${cancelTitle}">Отменить</button>` : ""}${x.state === "error" ? `<button class="btn small" data-retry-schedule="${esc(x.id)}">Проверить снова</button>` : ""}</div>`;
      }).join("") : "Записей в расписании пока нет.";
      $$('[data-cancel-schedule]').forEach(b => b.onclick = async () => {
        if (b.dataset.scheduleState === "running" && !await ask("Запись уже идёт. Остановить её и сохранить текущий итог встречи?")) return;
        try { await core.call("prompter_schedule_cancel", { id: b.dataset.cancelSchedule }); loadSchedules(); }
        catch (e) { toast(e.message, 5000); }
      });
      $$('[data-retry-schedule]').forEach(b => b.onclick = async () => { await core.call("prompter_schedule_retry", { id: b.dataset.retrySchedule }); loadSchedules(); });
    } catch (e) { box.textContent = e.message; }
  }
  $("#pr-s-add")?.addEventListener("click", async () => {
    const start = $("#pr-s-start").value, end = $("#pr-s-end").value, out = $("#pr-s-result");
    if (!start) { out.className = "res bad"; out.textContent = "Укажите начало."; return; }
    if (!$("#pr-s-consent").checked) { out.className = "res bad"; out.textContent = "Подтвердите согласие участников."; return; }
    try {
      await core.call("prompter_schedule", { title: $("#pr-s-title").value || "Встреча по расписанию",
        start_at: new Date(start).toISOString(), end_at: end ? new Date(end).toISOString() : null,
        options: { record: true, deferred: true, consent: true, record_source: get("prompter.audio_source") || "system",
          auto_first_frame: !!get("prompter.auto_first_frame"),
          capture_source: get("prompter.capture_source") || "screen", capture_region: get("prompter.capture_region") || {}, capture_application: get("prompter.capture_application") || "",
          auto_frames: true, auto_interval: Number(get("prompter.frame_interval") || 1),
          frame_quality: Number(get("prompter.frame_quality") || 85), frame_video: !!get("prompter.frame_video"),
          them: true, me: false, place: "online" } });
      out.className = "res ok"; out.textContent = end ? "Запись запланирована. Ядро остановит её в указанное время." : "Запись запланирована без ограничения по времени — остановите её из верхнего меню Dewiar."; loadSchedules();
    } catch (e) { out.className = "res bad"; out.textContent = e.message; }
  });
  const oldRefresh = window.refreshStatic;
  window.refreshStatic = function () { oldRefresh(); renderCaptureFields(); renderVideoState(); };
  $("#pr-stt-model")?.addEventListener("change", loadMeetingModel);
  core.on("hello", () => { renderCaptureFields(); renderVideoState(); loadSchedules(); loadMeetingModel(); loadAudioApps(); refreshCaptureAccess(); });
  core.on("prompter", e => { if (e.kind === "schedule") loadSchedules(); });
})();
