/* Subscription credentials stay in the official runtime, never in settings. */
(() => {
  const box = document.querySelector('#subscription-cards');
  const networkControls = [];
  let enablingNetwork = false;
  function syncNetworkPermission(settings = S) {
    if (!settings) return;
    const blocked = !!settings.privacy?.local_only;
    for (const {button, note} of networkControls) {
      button.hidden = !blocked; button.style.display = blocked ? "" : "none"; button.disabled = enablingNetwork;
      note.hidden = !blocked; note.style.display = blocked ? "" : "none";
    }
  }
  window.subscriptionNetworkSync = () => syncNetworkPermission();
  core.on('hello', d => syncNetworkPermission(d.settings));
  core.on('settings', e => syncNetworkPermission(e.settings));
  for (const [provider, title] of [['codex', 'Codex'], ['claude', 'Claude Code']]) {
    const card = document.createElement('div'); card.style.margin = '16px 0';
    card.innerHTML = `<b>${title}</b><div class="actions" style="flex-wrap:wrap"><button class="btn" data-a="refresh">Обновить модели и вход</button><button class="btn" data-a="login">Войти</button><button class="btn primary" data-a="network" hidden style="display:none">Разрешить внешние подключения</button><select style="max-width:340px" aria-label="Модель ${title}" data-a="model" disabled></select><select style="max-width:140px" aria-label="Уровень рассуждений ${title}" data-a="effort" hidden></select><button class="btn primary" data-a="use" disabled>Использовать</button></div><p class="hint" data-network-note hidden style="display:none">Выключит режим «только локально» для всего Dewiar. Подключения к внешним сервисам и приостановленные сетевые функции будут разрешены.</p><p class="res" aria-live="polite"></p>`;
    box.append(card);
    const get = s => card.querySelector(s), res = get('.res'), model = get('[data-a="model"]'), effort = get('[data-a="effort"]'), use = get('[data-a="use"]');
    let info = null, loginPending = false, refreshing = false, loginTimer = null, loginDeadline = 0;
    function stopLoginWatch() { clearTimeout(loginTimer); loginTimer = null; }
    function watchLogin() {
      stopLoginWatch();
      loginTimer = setTimeout(async () => {
        loginTimer = null;
        if (!loginPending) return;
        if (!S || S.privacy?.local_only) {
          loginPending = false;
          fail(Error('Вход приостановлен: включён режим «только локально».'));
          return;
        }
        if (Date.now() >= loginDeadline) {
          loginPending = false;
          fail(Error('Не удалось подтвердить вход за 10 минут. Завершите вход и нажмите «Обновить модели и вход».'));
          return;
        }
        await refresh();
        if (loginPending) watchLogin();
      }, 3000);
    }
    window.addEventListener('pagehide', stopLoginWatch);
    const fail = e => { res.className = 'res bad'; res.textContent = e.message || String(e); };
    const network = get('[data-a="network"]');
    networkControls.push({button: network, note: get('[data-network-note]')});
    network.onclick = async () => {
      if (enablingNetwork) return;
      enablingNetwork = true; syncNetworkPermission();
      res.className = 'res'; res.textContent = 'Разрешаю внешние подключения…';
      try {
        const result = await core.call('security_set', {local_only: false});
        if (result.state?.local_only !== false) throw Error('Режим «только локально» остался включён');
        const snapshot = await core.call('hello');
        S = snapshot.settings;
        refreshStatic(); profilesSync(); syncNetworkPermission();
        await get('[data-a="refresh"]').onclick({target: get('[data-a="refresh"]')});
      } catch(e) { fail(e); }
      finally { enablingNetwork = false; syncNetworkPermission(); }
    };
    model.onchange = () => {
      const m = info?.models.find(m => m.id === model.value);
      effort.innerHTML = '';
      for (const value of (m?.efforts || [])) effort.add(new Option(value, value));
      if ((m?.efforts || []).includes('medium')) effort.value = 'medium';
      effort.hidden = !m?.efforts?.length;
    };
    get('[data-a="refresh"]').onclick = async ev => {
      if (refreshing) return;
      refreshing = true;
      ev.target.disabled = true; use.disabled = true; model.disabled = true; info = null;
      res.className = 'res'; res.textContent = 'Проверяю официальный CLI…';
      try {
        info = await core.call('subscription_status', {provider});
        window.loadModelRoutingStatus?.();
        model.innerHTML = '';
        for (const m of info.models) model.add(new Option(m.name + ' · ' + m.id, m.id));
        const activeProfile = S.llm.profiles?.[S.llm.active];
        const existing = activeProfile?.kind === provider + '_subscription' ? activeProfile : Object.values(S.llm.profiles || {}).find(p => p.kind === provider + '_subscription');
        if (info.models.some(m => m.id === existing?.model)) model.value = existing.model;
        model.onchange();
        if (existing?.reasoning_effort && [...effort.options].some(o => o.value === existing.reasoning_effort)) effort.value = existing.reasoning_effort;
        model.disabled = !info.models.length; use.disabled = !info.authenticated || !info.models.length;
        if (info.authenticated) { loginPending = false; stopLoginWatch(); }
        res.className = 'res ' + (info.authenticated ? 'ok' : 'bad');
        res.textContent = info.authenticated ? `Вход выполнен · ${info.plan}. Выберите модель и нажмите «Использовать», затем «Проверить» в профиле.` : 'Требуется вход по подписке. Нажмите «Войти», затем «Обновить модели и вход».';
      } catch(e) { fail(e); }
      finally { refreshing = false; ev.target.disabled = false; }
    };
    const refresh = () => get('[data-a="refresh"]').onclick({target: get('[data-a="refresh"]')});
    core.on('subscription_login_completed', e => {
      if (e.provider !== provider) return;
      loginPending = false; stopLoginWatch();
      if (e.success) refresh();
      else fail(Error(e.error || 'Вход не завершён. Повторите авторизацию.'));
    });
    const refreshOnReturn = () => {
      if (S && !S.privacy?.local_only && (loginPending || document.querySelector('#p-brain.cur'))) refresh();
    };
    window.addEventListener('focus', refreshOnReturn);
    document.querySelector('nav button[data-p="brain"]')?.addEventListener('click', refreshOnReturn);
    core.on('hello', () => { if (document.querySelector('#p-brain.cur')) refreshOnReturn(); });
    get('[data-a="login"]').onclick = async ev => {
      ev.target.disabled = true;
      try {
        if (provider === 'claude') {
          await core.call('subscription_login', {provider});
          loginPending = true; loginDeadline = Date.now() + 600000;
          if (window.pywebview?.api?.subscription_claude_login) {
            await window.pywebview.api.subscription_claude_login();
            res.textContent = 'В Terminal открыт официальный Claude Code. Завершите вход — Dewiar автоматически обновит подключение.';
          } else res.textContent = 'Откройте Terminal → claude → /login. После входа подключение обновится автоматически.';
        } else {
          const r = await core.call('subscription_login', {provider});
          loginPending = true; loginDeadline = Date.now() + 600000;
          if (window.pywebview?.api?.open_url) await window.pywebview.api.open_url(r.url);
          else window.open(r.url, '_blank', 'noopener');
          res.textContent = 'Завершите вход в браузере. Dewiar автоматически обновит подключение и покажет модели. Ссылка действует 10 минут.';
        }
        watchLogin();
      } catch(e) { loginPending = false; stopLoginWatch(); fail(e); }
      finally { ev.target.disabled = false; }
    };
    use.onclick = async () => {
      if (!info?.authenticated || !info.models.some(m => m.id === model.value)) return;
      use.disabled = true;
      try {
        const profiles = {...S.llm.profiles};
        const id = Object.keys(profiles).find(id => profiles[id].kind === provider + '_subscription' && profiles[id].model === model.value) || uid();
        profiles[id] = {...profiles[id], name: profiles[id]?.name || title + ' · ' + model.value, kind: provider + '_subscription', model: model.value,
                        base_url: provider === 'codex' ? 'https://chatgpt.com' : 'https://claude.ai',
                        reasoning_effort: effort.hidden ? '' : effort.value, tools_mode: 'prompt'};
        S = await core.call("settings_save", {patch: {llm: {profiles, active: id}}});
        refreshStatic(); openProf = id; renderProfiles();
        res.className = 'res ok'; res.textContent = 'Модель выбрана. Кнопка «Проверить» ниже отправит короткий запрос.';
      } catch(e) { fail(e); }
      finally { use.disabled = false; }
    };
  }
  syncNetworkPermission();
})();
