/* Настройки записи встреч: отдельная модель распознавания, источник кадров,
   пользовательская кнопка снимка и фоновые записи по расписанию. */
(function () {
  const esc = (t) => escH(t);
  function renderCaptureFields() {
    const source = $("#pr-capture-source")?.value || "screen";
    $("#pr-region-fields")?.classList.toggle("hidden", source !== "region");
    $("#pr-app-field")?.classList.toggle("hidden", source !== "application");
  }
  $("#pr-capture-source")?.addEventListener("change", renderCaptureFields);
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
  function fmt(ts) { if (!ts) return "до ручной остановки"; try { return new Date(Number(ts) * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }); } catch (_) { return "—"; } }
  async function loadSchedules() {
    const box = $("#pr-s-list"); if (!box) return;
    try {
      const rows = await core.call("prompter_schedules");
      box.innerHTML = rows.length ? rows.map(x => `<div class="prof" style="padding:8px 10px;margin-top:6px;display:flex;gap:8px;align-items:center"><span style="flex:1"><b>${esc(x.title || "Встреча")}</b><br><small>${fmt(x.start_at)} — ${fmt(x.end_at)} · ${esc(x.state || "")}${x.error ? ` · ${esc(x.error)}` : ""}</small></span>${x.state === "scheduled" ? `<button class="btn small" data-cancel-schedule="${esc(x.id)}">Отменить</button>` : ""}</div>`).join("") : "Записей в расписании пока нет.";
      $$('[data-cancel-schedule]').forEach(b => b.onclick = async () => { await core.call("prompter_schedule_cancel", { id: b.dataset.cancelSchedule }); loadSchedules(); });
    } catch (e) { box.textContent = e.message; }
  }
  $("#pr-s-add")?.addEventListener("click", async () => {
    const start = $("#pr-s-start").value, end = $("#pr-s-end").value, out = $("#pr-s-result");
    if (!start) { out.className = "res bad"; out.textContent = "Укажите начало."; return; }
    if (!$("#pr-s-consent").checked) { out.className = "res bad"; out.textContent = "Подтвердите согласие участников."; return; }
    try {
      await core.call("prompter_schedule", { title: $("#pr-s-title").value || "Встреча по расписанию",
        start_at: new Date(start).toISOString(), end_at: end ? new Date(end).toISOString() : null,
        options: { record: true, deferred: true, consent: true, auto_first_frame: !!get("prompter.auto_first_frame"),
          capture_source: get("prompter.capture_source") || "screen", capture_region: get("prompter.capture_region") || {}, capture_application: get("prompter.capture_application") || "",
          auto_frames: true, auto_interval: Number(get("prompter.frame_interval") || 1),
          frame_quality: Number(get("prompter.frame_quality") || 85), frame_video: !!get("prompter.frame_video"),
          them: true, me: false, place: "online" } });
      out.className = "res ok"; out.textContent = end ? "Запись запланирована. Ядро остановит её в указанное время." : "Запись запланирована без ограничения по времени — остановите её из верхнего меню Dewiar."; loadSchedules();
    } catch (e) { out.className = "res bad"; out.textContent = e.message; }
  });
  const oldRefresh = window.refreshStatic;
  window.refreshStatic = function () { oldRefresh(); renderCaptureFields(); };
  $("#pr-stt-model")?.addEventListener("change", loadMeetingModel);
  core.on("hello", () => { renderCaptureFields(); loadSchedules(); loadMeetingModel(); });
  core.on("prompter", e => { if (e.kind === "schedule") loadSchedules(); });
})();
