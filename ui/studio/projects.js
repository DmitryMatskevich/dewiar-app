/* Project backup profiles use the same saved settings for a single run and a batch. */
(() => {
  const get = id => document.getElementById(id);
  const tab = get('tasks-projects-tab');
  const panel = get('p-projects');
  if (!tab || !panel) return;
  const backup = get('projects-backup');
  const backupTab = get('projects-backup-tab');
  const form = get('projects-form');
  const list = get('projects-list');
  const status = get('projects-status');
  const destinations = get('projects-destinations');
  const selection = new Set();
  let catalog = {projects: [], providers: {}};
  let active = '';
  let dirty = false;
  let busy = false;

  const otherTabs = ['work', 'songs', 'sites', 'articles', 'video'];
  function selectTab(open) {
    panel.hidden = !open;
    tab.setAttribute('aria-pressed', String(open));
  }
  for (const name of otherTabs) {
    get('tasks-' + name + '-tab')?.addEventListener('click', () => selectTab(false));
  }
  tab.addEventListener('click', () => {
    get('tasks-work-tab')?.click();
    get('tasks-work-content').hidden = true;
    get('tasks-work-tab').setAttribute('aria-pressed', 'false');
    selectTab(true);
  });

  function buttonState() {
    get('projects-run-batch').disabled = busy || !selection.size;
    get('projects-run').disabled = busy || dirty || !active;
    get('projects-save').disabled = busy;
    get('projects-reset').disabled = busy;
  }

  function renderList() {
    list.replaceChildren();
    for (const project of catalog.projects) {
      const row = document.createElement('div');
      row.className = 'actions';
      const batch = document.createElement('input');
      batch.type = 'checkbox';
      batch.checked = selection.has(project.id);
      batch.setAttribute('aria-label', 'Выбрать для пакета: ' + project.title);
      batch.onchange = () => {
        if (batch.checked) selection.add(project.id);
        else selection.delete(project.id);
        buttonState();
      };
      const edit = document.createElement('button');
      edit.type = 'button';
      edit.className = 'btn' + (active === project.id ? ' primary' : '');
      edit.textContent = project.title;
      edit.onclick = () => choose(project);
      row.append(batch, edit);
      list.append(row);
    }
    buttonState();
  }

  function addDestination(row = {provider: 'local', target: ''}) {
    const group = document.createElement('div');
    group.className = 'row';
    group.dataset.backupDestination = '';
    const serviceLabel = document.createElement('label');
    serviceLabel.className = 'f';
    serviceLabel.textContent = 'Назначение';
    const service = document.createElement('select');
    for (const [key, label] of Object.entries(catalog.providers)) service.add(new Option(label, key));
    service.value = row.provider;
    serviceLabel.append(service);
    const targetLabel = document.createElement('label');
    targetLabel.className = 'f';
    targetLabel.textContent = 'Папка или ссылка';
    const target = document.createElement('input');
    target.required = true;
    target.spellcheck = false;
    target.value = row.target;
    function placeholder() {
      target.placeholder = {local: '/Volumes/Backup/SaveDevAll', yandex: 'SaveDevAll или ссылка на папку',
        google: 'Ссылка на папку Google Диска или её ID'}[service.value];
    }
    service.onchange = placeholder;
    placeholder();
    targetLabel.append(target);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'btn';
    remove.textContent = 'Убрать';
    remove.onclick = () => { group.remove(); dirty = true; buttonState(); };
    group.append(serviceLabel, targetLabel, remove);
    destinations.append(group);
  }

  function choose(project) {
    active = project.id || '';
    dirty = false;
    get('projects-title').textContent = project.title || 'Новый проект';
    for (const field of ['title', 'root', 'output_dir', 'include', 'exclude', 'aliases']) {
      const value = project[field];
      form.elements[field].value = Array.isArray(value) ? value.join('\n') : value || '';
    }
    destinations.replaceChildren();
    for (const row of project.destinations || []) addDestination(row);
    get('projects-reset').hidden = !project.preset;
    form.hidden = false;
    renderList();
  }

  async function fetchProfiles() {
    try {
      catalog = await core.call('project_backups_get');
      const project = catalog.projects.find(item => item.id === active) || catalog.projects.find(item => item.id === 'site-dcnt') || catalog.projects[0];
      if (project && !dirty) choose(project);
      else renderList();
      status.textContent = '';
    } catch (error) {
      status.textContent = error.message;
    }
  }

  backupTab.onclick = () => {
    backup.hidden = !backup.hidden;
    backupTab.setAttribute('aria-expanded', String(!backup.hidden));
    if (!backup.hidden) fetchProfiles();
  };
  get('projects-add').onclick = () => choose({include: ['.'], exclude: ['**/node_modules', '**/.venv', '**/__pycache__'],
    output_dir: catalog.projects.find(p => p.id === 'site-dcnt')?.output_dir});
  get('projects-dest-add').onclick = () => { addDestination(); dirty = true; buttonState(); };
  form.addEventListener('input', () => { dirty = true; buttonState(); });
  async function save(reset) {
    if (busy) return;
    const lines = name => form.elements[name].value.split('\n').map(line => line.trim()).filter(Boolean);
    const data = reset ? null : {
      title: form.elements.title.value, root: form.elements.root.value, output_dir: form.elements.output_dir.value,
      aliases: lines('aliases'), include: lines('include'), exclude: lines('exclude'),
      destinations: [...destinations.querySelectorAll('[data-backup-destination]')].map(row => ({
        provider: row.querySelector('select').value, target: row.querySelector('input').value,
      })),
    };
    busy = true;
    buttonState();
    try {
      catalog = await core.call('project_backups_save', {id: active, profile: data});
      choose(catalog.projects.find(project => project.id === catalog.selected));
      status.textContent = reset ? 'Настройки по умолчанию восстановлены.' : 'Настройки сохранены.';
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      buttonState();
    }
  }
  form.onsubmit = event => { event.preventDefault(); save(false); };
  get('projects-reset').onclick = () => save(true);
  async function run(ids) {
    if (busy || !ids.length) return;
    if (dirty && ids.includes(active)) {
      status.textContent = 'Сначала сохраните изменения проекта.';
      return;
    }
    busy = true;
    buttonState();
    try {
      const task = await core.call('project_backups_run', {projects: ids});
      status.textContent = 'Задача запущена: ' + task.title;
      get('tasks-work-tab')?.click();
      loadTasks();
      await openTask(task.id);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      buttonState();
    }
  }
  get('projects-run').onclick = () => run([active]);
  get('projects-run-batch').onclick = () => run([...selection]);
})();
