/* Permission-dependent HUD actions stay clickable to request access. */
window.DewiarHudAccess = {
  create({api, notify = () => {}}) {
    const screenButtons = ['a-shot', 'a-meet'], fileButtons = ['b-cal', 'b-notes'];
    const titles = new Map([...screenButtons, ...fileButtons].map(id => [id, document.getElementById(id).title]));
    let screen = null, files = null, screenRequest = null, filesRequest = null, fileAction = false, timer = 0;
    const fileGranted = () => files == null || files.request_supported === false ||
      (!files.pending && ['documents', 'workspace'].every(key => files.folders?.[key]?.state === 'authorized' && files.folders[key].readable));
    function render() {
      for (const id of [...screenButtons, ...fileButtons]) {
        const button = document.getElementById(id), isScreen = screenButtons.includes(id);
        const unavailable = isScreen ? screen != null && !screen.granted : !fileGranted();
        button.classList.toggle('access-unavailable', unavailable);
        button.dataset.access = unavailable ? 'unavailable' : 'available';
        const pending = isScreen ? screen?.pending : files?.pending;
        button.title = unavailable ? (pending ? 'Ожидается разрешение macOS…' : isScreen
          ? 'Нет доступа к записи экрана. Нажмите, чтобы разрешить доступ Dewiar в macOS.'
          : 'Нет подтверждённого доступа к Документам и рабочей папке. Нажмите, чтобы разрешить доступ к файлам.') : titles.get(id);
        button.setAttribute('aria-label', button.title);
        if (isScreen && !unavailable) button.setAttribute('aria-haspopup', 'dialog');
        else button.removeAttribute('aria-haspopup');
      }
    }
    function pollPending() {
      clearTimeout(timer);
      if (screen?.pending || files?.pending) timer = setTimeout(() => refresh(false), 700);
    }
    async function refreshScreen() {
      if (screenRequest) return screenRequest;
      screenRequest = (async () => {
        const native = api();
        try { screen = native?.screen_capture_permission ? (await native.screen_capture_permission() || {granted:false}) : null; }
        catch (_) { screen = {granted:false}; }
        render(); pollPending(); return screen == null || !!screen.granted;
      })();
      try { return await screenRequest; } finally { screenRequest = null; }
    }
    async function refreshFiles(recheck = false) {
      if (filesRequest) return filesRequest;
      filesRequest = (async () => {
        const native = api();
        try { files = native?.files_access_state ? (await native.files_access_state(recheck) || {folders:{}}) : null; }
        catch (_) { files = {folders:{}, error:'Не удалось проверить доступ к папкам'}; }
        render(); pollPending(); return files;
      })();
      try { return await filesRequest; } finally { filesRequest = null; }
    }
    async function refresh(recheck = true) { await Promise.all([refreshScreen(), refreshFiles(recheck)]); }
    async function ensureScreen() {
      if (await refreshScreen()) return true;
      const native = api();
      if (native?.request_screen_access) await native.request_screen_access();
      else await native?.open_screen_settings?.();
      await refreshScreen(); return false;
    }
    async function waitForFiles() {
      const deadline = Date.now() + 190000;
      while (files?.pending && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 400));
        await refreshFiles();
      }
      if (files?.pending) throw Error('Ответ macOS ещё не получен. Завершите запрос разрешения и нажмите кнопку ещё раз.');
    }
    async function ensureFiles() {
      const native = api();
      if (!native?.files_access_state) return true;
      if (fileAction) return false;
      fileAction = true;
      try {
        await refreshFiles();
        if (files?.request_supported === false) return true;
        await waitForFiles();
        notify('Проверяю доступ к Документам и рабочей папке. Если появится запрос macOS, нажмите «Разрешить».');
        if (!native.request_files_access) { await native.open_files_settings?.(); return false; }
        files = await native.request_files_access('features') || {folders:{}}; render(); pollPending();
        await waitForFiles();
        if (fileGranted()) { notify(''); return true; }
        const problem = ['documents', 'workspace'].map(key => files?.folders?.[key]).find(value => value?.state !== 'authorized');
        notify(problem?.error || 'Разрешите Dewiar доступ к Документам и рабочей папке, затем нажмите кнопку ещё раз.');
        return false;
      } finally { fileAction = false; }
    }
    function setTitle(id, title) { titles.set(id, title); render(); }
    function start() {
      const update = () => refresh(true);
      window.addEventListener('focus', update);
      window.addEventListener('pywebviewready', update);
      document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
      update();
      // Native startup requests may begin just after the webview-ready event.
      setTimeout(() => refresh(false), 1500);
    }
    return {start, refresh, refreshScreen, ensureScreen, ensureFiles, setTitle};
  }
};
