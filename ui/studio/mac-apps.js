/* Read-only permission check. macOS grants access only through System Settings. */
(() => {
  const box = document.getElementById('mac-app-access');
  const status = document.getElementById('mac-app-status');
  async function refresh() {
    const api = window.api && window.api();
    if (!api || !api.accessibility_permission) return;
    box.style.display = 'block';
    try {
      const value = await api.accessibility_permission();
      status.textContent = !value.packaged ? 'Запуск из исходников: доступ будет относиться к Python или приложению запуска. Откройте Dewiar.app.' : value.granted ? 'Доступ разрешён. Можно переносить окна и курсор.' : 'Для переноса окон и курсора включите Dewiar в «Универсальном доступе». Запуск и обычное завершение приложений доступны без этого разрешения.';
    } catch (e) { status.textContent = 'Не удалось проверить доступ: ' + (e.message || e); }
  }
  document.getElementById('mac-app-refresh').onclick = refresh;
  document.getElementById('mac-app-settings').onclick = async () => {
    try { await window.api().open_accessibility_settings(); }
    catch (e) { status.textContent = 'Не удалось открыть настройки: ' + (e.message || e); }
  };
  window.addEventListener('pywebviewready', refresh);
  window.addEventListener('focus', refresh);
  document.querySelector('nav button[data-p="hands"]')?.addEventListener('click', refresh);
  setTimeout(refresh, 300);
})();
