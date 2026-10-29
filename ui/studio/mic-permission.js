/* Native Mac permission state; checking does not request access or start recording. */
(() => {
  const box = document.getElementById("mac-mic-access");
  const status = document.getElementById("mac-mic-status");
  const request = document.getElementById("mac-mic-request");
  let timer = null;
  const labels = {
    not_determined: "Доступ ещё не запрашивался. Нажмите «Разрешить доступ» и подтвердите запрос macOS.",
    authorized: "Доступ к микрофону разрешён в macOS.",
    denied: "Доступ запрещён. В настройках macOS откройте «Конфиденциальность и безопасность» → «Микрофон» и включите Dewiar.",
    restricted: "Доступ ограничен системой. Проверьте ограничения устройства или обратитесь к администратору.",
    unknown: "Не удалось определить разрешение. Проверьте доступ в настройках macOS."
  };
  function render(value) {
    if (value.request_supported === false) {
      clearTimeout(timer);
      status.textContent = "Версия из исходников: разрешение macOS относится к Python или приложению запуска. Чтобы выдать доступ Dewiar, откройте Dewiar.app.";
      request.hidden = true;
      return;
    }
    status.textContent = value.pending ? "Ожидаю ваш ответ в системном запросе macOS…" : value.retry_blocked ? "Запрос уже отправлен, но macOS не подтвердила доступ. Повторно окно не открываю. Проверьте Dewiar в настройках микрофона и нажмите «Проверить»." : labels[value.state] || labels.unknown;
    request.hidden = value.state !== "not_determined" || !!value.retry_blocked;
    request.disabled = !!value.pending;
    clearTimeout(timer);
    if (value.pending) timer = setTimeout(refresh, 1000);
  }
  async function refresh() {
    const a = window.api && window.api();
    if (!a || !a.microphone_permission) return;
    box.style.display = "block";
    try { render(await a.microphone_permission()); }
    catch (error) { status.textContent = "Не удалось проверить доступ: " + (error.message || error); }
  }
  request.onclick = async () => {
    const a = window.api && window.api();
    if (!a || !a.request_microphone_access) return;
    request.disabled = true;
    try { render(await a.request_microphone_access()); }
    catch (error) { request.disabled = false; status.textContent = "Не удалось запросить доступ: " + (error.message || error); }
  };
  document.getElementById("mac-mic-refresh").onclick = refresh;
  document.getElementById("mac-mic-settings").onclick = async () => {
    try { await window.api().open_microphone_settings(); }
    catch (error) { status.textContent = "Не удалось открыть настройки: " + (error.message || error); }
  };
  window.addEventListener("pywebviewready", refresh);
  window.addEventListener("focus", refresh);
  document.querySelector('nav button[data-p="sound"]')?.addEventListener("click", refresh);
  setTimeout(refresh, 300);
})();
