/* Context help is read-only and fully local. No model calls, external links or form changes. */
let helpReturnFocus=null, helpArticleKey='';
const HELP_SEARCH_LIMIT=80;
function helpText(value){return String(value??'').replace(/\s+/g,' ').trim();}
function helpCurrentSection(){return document.querySelector('main section.cur[id^="p-"]')?.id.slice(2)||'home';}
function helpLayout(){
  const panel=document.getElementById('help'),modal=!panel.hidden&&matchMedia('(max-width:600px)').matches;
  panel.setAttribute('role',modal?'dialog':'complementary');
  if(modal)panel.setAttribute('aria-modal','true');else panel.removeAttribute('aria-modal');
}
function helpClose(){
  const panel=document.getElementById('help');panel.classList.remove('open');panel.hidden=true;
  document.body.classList.remove('context-help-open');helpLayout();
  document.querySelectorAll('[data-section-help]').forEach(b=>b.setAttribute('aria-expanded','false'));
  const target=helpReturnFocus?.isConnected&&helpReturnFocus.getClientRects().length?helpReturnFocus:document.querySelector('main section.cur [data-section-help]');
  target?.focus();
}
function helpRender(k){
  const h=window.DEWIAR_HELP[k];if(!h)return;helpArticleKey=k;
  const list=a=>(a||[]).map(x=>`<li>${escH(x)}</li>`).join('');
  const examples=(h.try||[]).map(x=>typeof x==='string'?{say:x}:x).filter(x=>x&&x.say);
  document.getElementById('help-body').innerHTML=`<h2 id="help-heading">${escH(h.title)}</h2><h3>Для чего этот раздел</h3><p>${escH(h.what)}</p>`+
    (h.how?.length?`<h3>Как пользоваться</h3><ol>${list(h.how)}</ol>`:'')+
    (examples.length?`<h3>Примеры запросов</h3><p class="hint">Примеры только для чтения: ничего не отправляется агенту.</p>${examples.map(x=>`<div class="help-example"><b>«${escH(x.say)}»</b>${x.get?`<p>${escH(x.get)}</p>`:''}</div>`).join('')}`:'')+
    (h.say?.length?`<h3>Что можно сказать</h3><ul>${list(h.say)}</ul>`:'')+
    (h.tips?.length?`<h3>Что важно знать</h3><ul>${list(h.tips)}</ul>`:'')+
    (h.troubleshoot?.length?`<h3>Если не работает</h3><ul>${list(h.troubleshoot)}</ul>`:'')+
    (h.video_refs?.length?`<details class="help-video-source"><summary title="Показать главы ранее разобранного видеоруководства и отличия Mac-версии. Это локальные заметки, видеоплеер и внешние сайты не загружаются.">По демонстрации автора</summary><p class="hint">Сценарии адаптированы по сохранённым субтитрам. Демонстрация не заменяет проверку на вашем Mac.</p><ul>${h.video_refs.map(v=>`<li><b>${Math.floor(v.start/60)}:${String(v.start%60).padStart(2,'0')} · ${escH(v.title)}</b><br>${escH(v.note)}</li>`).join('')}</ul></details>`:'');
  document.getElementById('help-results').hidden=true;
  document.getElementById('help-body').hidden=false;
  document.querySelectorAll('[data-section-help]').forEach(b=>b.setAttribute('aria-expanded',String(b.dataset.sectionHelp===k)));
  document.getElementById('help').scrollTop=0;
}
function showHelp(k){
  if(!window.DEWIAR_HELP?.[k])return;
  const panel=document.getElementById('help');
  if(panel.hidden||document.activeElement?.matches('[data-section-help]'))helpReturnFocus=document.activeElement;
  document.getElementById('help-search').value='';helpRender(k);
  panel.hidden=false;panel.classList.add('open');document.body.classList.add('context-help-open');helpLayout();
  document.getElementById('help-x').focus();
}
function helpSearch(){
  const query=helpText(document.getElementById('help-search').value).toLocaleLowerCase('ru').replace(/ё/g,'е');
  const results=document.getElementById('help-results');
  if(!query){helpRender(helpArticleKey||helpCurrentSection());return;}
  const terms=query.split(' ');
  const matches=Object.entries(window.DEWIAR_HELP).filter(([,h])=>{const corpus=JSON.stringify(h).toLocaleLowerCase('ru').replace(/ё/g,'е');return terms.every(t=>corpus.includes(t));}).slice(0,HELP_SEARCH_LIMIT);
  results.replaceChildren();document.getElementById('help-body').hidden=true;results.hidden=false;
  const summary=document.createElement('p');summary.className='hint';summary.setAttribute('role','status');summary.textContent=matches.length?`Найдено тем: ${matches.length}`:'Ничего не найдено. Попробуйте другое слово: «микрофон», «архив», «приоритет».';results.append(summary);
  for(const [key,h] of matches){const button=document.createElement('button');button.type='button';button.className='btn help-result';button.textContent=h.title;button.title=`Открыть локальную статью «${h.title}». Текущие настройки и введённые данные останутся на месте.`;button.onclick=()=>{document.getElementById('help-search').value='';helpRender(key);document.getElementById('help-x').focus();};results.append(button);}
}
window.showHelp=showHelp;
(() => {
  const panel=document.getElementById('help');panel.hidden=true;panel.setAttribute('role','complementary');panel.setAttribute('aria-label','Помощь по интерфейсу Dewiar');
  document.getElementById('help-x').onclick=helpClose;
  document.getElementById('help-search').addEventListener('input',helpSearch);
  document.getElementById('help-current').onclick=()=>{document.getElementById('help-search').value='';helpRender(helpCurrentSection());};
  document.getElementById('help-all').onclick=()=>{
    const results=document.getElementById('help-results');results.replaceChildren();results.hidden=false;document.getElementById('help-body').hidden=true;document.getElementById('help-search').value='';
    for(const [key,h] of Object.entries(window.DEWIAR_HELP)){const b=document.createElement('button');b.type='button';b.className='btn help-result';b.textContent=h.title;b.title=`Прочитать инструкцию «${h.title}» без изменения настроек.`;b.onclick=()=>{helpRender(key);document.getElementById('help-x').focus();};results.append(b);}
  };
  addEventListener('resize',helpLayout);
  document.addEventListener('keydown',e=>{
    if(panel.hidden)return;
    if(e.key==='Escape'){e.preventDefault();helpClose();return;}
    if(e.key==='Tab'&&panel.getAttribute('aria-modal')==='true'){
      const controls=[...panel.querySelectorAll('button,input,a[href],select,textarea')].filter(n=>!n.disabled&&n.getClientRects().length);
      const first=controls[0],last=controls.at(-1);
      if(e.shiftKey&&(document.activeElement===first||!panel.contains(document.activeElement))){e.preventDefault();last?.focus();}
      else if(!e.shiftKey&&(document.activeElement===last||!panel.contains(document.activeElement))){e.preventDefault();first?.focus();}
    }
  });
  document.querySelectorAll('nav button[data-p]').forEach(b=>b.addEventListener('click',()=>{
    if(!panel.hidden)setTimeout(()=>{document.getElementById('help-search').value='';helpRender(helpCurrentSection());},0);
  }));
  function sectionHelp(section){
    const key=section.id.slice(2),home=key==='home',h=section.querySelector('h1');
    // На «Главной» справка относится ко всей странице: держим её в правом блоке
    // действий последней кнопкой, рядом со «Слепком знаний», а не после приветствия.
    const host=home?document.getElementById('home-help-slot'):h;
    if(!host||!window.DEWIAR_HELP[key]||host.querySelector('[data-section-help]'))return;
    if(!home&&h.id){const span=document.createElement('span');span.id=h.id;h.removeAttribute('id');span.append(...h.childNodes);h.append(span);}
    const b=document.createElement('button');b.type='button';b.className='btn section-help';b.dataset.sectionHelp=key;b.setAttribute('aria-controls','help');b.setAttribute('aria-expanded','false');
    b.title=`Открыть подробную справку раздела «${window.DEWIAR_HELP[key].title}»: назначение, шаги настройки, примеры и устранение ошибок. Кнопка находится у правого края окна; справка работает без интернета и не запускает команды.`;
    b.innerHTML='<span class="help-symbol" aria-hidden="true">?</span><span>Помощь</span>';
    b.onclick=e=>{e.stopPropagation();showHelp(key);};host.append(b);
  }
  document.querySelectorAll('main section[id^="p-"]').forEach(sectionHelp);
  // Existing controls receive detailed native title attributes, never extra '?' buttons.
  const fieldDescriptions={
    name:'Название профиля для вашего удобства. Оно помогает отличать подключения и не меняет идентификатор модели на сервере.',
    priority:'Приоритет автоматического выбора: целое число от 1 до 9. 1 — наивысший. Модель должна также участвовать в автовыборе, быть доступной и находиться в своём расписании.',
    routing_enabled:'Включить этот профиль в автоматическую очередь моделей. Отключённый профиль сохраняется, но пропускается при автоматическом выборе.',
    schedule_enabled:'Ограничить участие профиля указанными часами по местному времени Mac. Без ограничения модель доступна круглосуточно.',
    schedule_start:'Начало интервала работы по местному времени Mac, включительно. Поддерживается ночной интервал, например 22:00–06:00.',
    schedule_end:'Конец интервала работы по местному времени Mac, не включительно. Формат ЧЧ:ММ; 24:00 означает полночь. За пределами интервала автовыбор пропускает профиль.',
    timeout:'Максимальное ожидание ответа этой модели, в секундах. Допустимо 5–1800. Малое значение может прерывать медленный запуск локальной модели.',
    api_key:'Секретный ключ поставщика API. Для локального сервера обычно не нужен. Ключ нельзя публиковать или пересылать в переписку; подписки используют собственную авторизацию.',
    model:'Точный идентификатор модели на сервере. Название должно совпадать с каталогом поставщика. Изменение идентификатора не скачивает модель.',
    base_url:'Адрес API выбранного сервера. Локальные Ollama, LM Studio и Strata должны быть запущены. Адрес сайта поставщика и адрес его API могут различаться.',
    kind:'Протокол подключения. Выберите тип, который поддерживает сервер; Codex и Claude Code используют авторизацию официального CLI.',
    temperature:'Вариативность генерации. Более высокое значение делает ответы разнообразнее; сервер может не поддерживать этот параметр.',
    max_tokens:'Верхняя граница длины ответа этого профиля в токенах. В реальном запросе дополнительно действует меньший из лимитов: профиля и выбранной очереди «Общение», «Планирование» или «Программирование». Это не остаток средств на аккаунте.',
    disable_thinking:'Попросить совместимую модель отключить отдельный режим рассуждений. Поддержка зависит от модели и сервера; это может влиять на скорость и качество ответа.'
  };
  const ids={
    'model-role-max-tokens':'Верхний предел длины ответа для текущей очереди моделей. По умолчанию: «Общение» — 1024, «Планирование» — 16384, «Программирование» — 8192; вручную можно указать от 128 до 32768. Если у профиля задан меньший предел, применяется меньшее значение; это не остаток средств и не размер контекста.',
    'ct-permit':'Запросить доступ Dewiar к Контактам macOS. Подтвердите в системном окне. Повторные нажатия во время ожидания не создают новые запросы.',
    'ct-permission-refresh':'Прочитать только системный статус доступа к Контактам. Проверка не читает адресную книгу и не запрашивает разрешение.',
    'ct-native-settings':'Открыть раздел доступа к Контактам в системных настройках macOS. Разрешение изменяет сам пользователь.',
    'ct-read':'Прочитать доступные системные карточки для предварительного просмотра. Ничего не импортируется до выбора и подтверждения.',
    'ct-select-all':'Выбрать все карточки текущего доступного списка, включая скрытые фильтром. При ограниченном доступе macOS это только разрешённые контакты.',
    'ct-select-none':'Снять весь выбор в списке импорта. Это не удаляет контакты.',
    'ct-import':'Импортировать только отмеченные карточки в Dewiar после подтверждения. Системные карточки не изменяются. Если список или доступ устарели, импорт остановится.',
    'ct-update':'Разрешить замену ранее импортированных локальных полей актуальными из macOS. По умолчанию существующие копии сохраняются без перезаписи.',
    'ct-new':'Открыть форму нового контакта. По умолчанию после подтверждения он будет создан в Dewiar и Контактах macOS.',
    'ct-to-mac':'Дополнительно создать новую карточку в системных Контактах. Требует разрешения. Она может синхронизироваться через учётные записи macOS. Отключите для явного сохранения только в Dewiar.',
    'ct-suggest':'Предлагать создание или копирование карточки при упоминании ФИО. Это не разрешает автоматическое сохранение или отправку контактов модели.',
    'ct-local-search':'Найти локальные копии по имени, телефону или почте. Этот поиск не обращается к модели.',
    'ct-source-search':'Отфильтровать уже прочитанные карточки macOS. Фильтр не изменяет выбор: перед импортом проверьте общее число отмеченных карточек.',
    'm-find':'Найти слово или имя в сохранённых записях памяти. Для проверки вывода откройте найденную запись и прочитайте источник; сама связь на карте не доказывает факт.',
    'm-fit':'Вернуть обзор всей карты по центру. Это меняет только масштаб и положение, записи памяти не удаляются.',
    'm-reload':'Повторно прочитать записи и связи карты. Это обновление отображения, а не обучение модели и не очистка памяти.',
    'm-motion':'Включить или выключить анимацию карты. Отключите при замедлении интерфейса; записи и связи останутся сохранены.',
    'm-full':'Развернуть карту для просмотра. Escape позволяет выйти из полноэкранного представления; содержимое памяти не изменяется.',
    'mp-left':'Показать или скрыть список записей слева. Скрытие панели не удаляет заметки.',
    'mp-right':'Показать или скрыть выбранную запись справа. Для проверки найденного факта откройте саму запись.',
    'mn-new':'Открыть форму новой заметки в памяти. Заполните и сохраните её; само открытие ещё не создаёт содержательную запись.',
    'mn-filter':'Отфильтровать список заметок по названию. Очистите строку, чтобы снова видеть все записи списка.',
    'd-now':'Попросить модель подготовить запись о сегодняшнем дне сейчас. Использует доступную историю и расходует лимиты; это не просто открытие уже готового дневника.',
    'dins-all':'Убрать выводы дневника, которые подмешиваются в разговор. Это не удаление всех дневников, заметок или бесед.',
    's-upd':'Попросить модель обновить автопортрет выбранного персонажа по ночным записям дневника. Расходует лимиты. Обновляется текст души, а не веса модели.',
    's-edit':'Открыть текст автопортрета выбранного персонажа для ручной правки. После изменений сохраните их либо отмените редактирование.',
    's-save':'Сохранить ручную правку души выбранного персонажа. Проверьте, что открыт нужный персонаж и текст не содержит лишних личных сведений.',
    's-cancel':'Выйти из редактирования души без сохранения текущего черновика. Ранее сохранённая версия остаётся.',
    'c-edit':'Открыть пользовательскую конституцию для редактирования. Это инструкция поведения модели; технические ограничения сети и файлов настраиваются отдельно.',
    'c-reset':'Заменить пользовательскую конституцию исходными принципами. Предварительно сохраните нужные правки в отдельную копию.',
    'c-save':'Сохранить пользовательские принципы. После сохранения проверьте поведение в разговоре; текст правил не гарантирует невозможность ошибки модели.',
    'c-cancel':'Отменить текущую правку конституции и вернуться к сохранённому тексту.',
    'help-search':'Найти локальные инструкции по словам в заголовках и содержимом. Поиск не обращается к модели, не читает личные чаты и не отправляет запросы в интернет.',
    'help-x':'Закрыть справку и вернуться к настройкам. Можно также нажать Escape; введённые значения останутся на месте.',
    'help-all':'Показать все локальные статьи о разделах приложения. Чтение статей не запускает примеры команд.',
    'help-current':'Вернуться к инструкции текущего открытого раздела, сохранив настройки и введённые значения.',
    'model-limits-refresh':'Обновить доступные сведения о лимитах и балансе у подключённых поставщиков. Числа могут быть общими для нескольких моделей одной подписки. Недоступные данные не заменяются нулём.',
    't-llm':'Выбрать модель для фоновых задач. Эта настройка позволяет использовать для задач другую модель, чем в обычном разговоре. Назначения команды конкретной задачи проверяйте отдельно.',
    't-new':'Открыть форму новой задачи: цель, папка проекта и при необходимости команда моделей. Задача ещё не запускается.',
    't-go':'Создать и начать задачу с указанной целью, проектом и командой. Модели и инструменты могут расходовать лимиты; перед запуском проверьте поля.',
    't-pick':'Выбрать папку исходного проекта для разработки. Агент будет готовить изменения в рабочей копии; принятие изменений — отдельное действие.',
    't-cancel':'Закрыть форму создания новой задачи без запуска.',
    'task-team-save':'Сохранить назначения моделей этой задачи. Для изменения выполняющейся задачи сначала остановите её.',
    'task-team-restart':'Перезапустить обсуждение плана выбранной командой. Новый круг расходует лимиты участников; предыдущий итог требует повторного согласования.',
    'mac-mic-request':'Запросить доступ Dewiar к микрофону. Ответьте в системном окне macOS. Если запрос уже ожидает ответа, повторные окна не создаются.',
    'mac-mic-refresh':'Прочитать текущее разрешение микрофона в macOS. Проверка не начинает запись и не выдаёт разрешение автоматически.',
    'mac-mic-settings':'Открыть системный раздел доступа к микрофону. Включите Dewiar вручную, затем вернитесь и проверьте доступ.',
    'mac-app-settings':'Открыть «Универсальный доступ» macOS для управления окнами и курсором. Доступ включает пользователь в системных настройках.',
    'mac-app-refresh':'Прочитать системное разрешение управления окнами и курсором. Проверка не перемещает окна и не меняет разрешения.',
    'strata-start':'Запустить выбранный уже установленный пакет Strata без открытия её окна. Перед запуском проверяется свободная память; большие модели могут не поместиться.',
    'strata-stop':'Остановить процесс Strata, который запустил Dewiar, и освободить его ресурсы. Чужие процессы и другие серверы не завершаются.',
    'strata-probe':'Проверить указанный API Strata и получить доступный каталог моделей. Проверка каталога сама по себе не загружает тяжёлые веса.',
    'strata-use':'Сохранить выбранную модель Strata как профиль подключения. Установка модели на диск этим действием не выполняется.',
    'strata-use-owned':'Создать или обновить профиль соединения с запущенным Dewiar движком Strata. Существующие приоритеты профиля сохраняются.',
    'ch-search':'Поиск текста в выбранной области переписки. Для поиска по архивным беседам переключите область на архив.',
    'ch-scope':'Выбрать область списка и поиска: активные беседы или архив. Переключение области ничего не удаляет.',
    'bug-send':'Отправить подготовленный отчёт и выбранные вложения. Используется собственный сервер, затем настроенная SMTP-почта. При неудаче отчёт остаётся в локальной очереди.',
    'sn-chip':'Открыть меню слепка знаний: резервная копия, восстановление и справка. Само открытие меню не создаёт и не восстанавливает файлы.'
  };
  const settings={
    'prompter.system_audio':'Способ захвата звука звонка: автоматически используется Core Audio, при ошибке — установленный BlackHole/Loopback. Явно выбранный Core Audio не подменяется другим способом.',
    'prompter.tap_target':'Какое приложение слышать через Core Audio. Весь системный звук включает и другие воспроизводящие приложения; выбор одного приложения ограничивает аудио. На BlackHole выбор приложения не применяется.',
    'prompter.record_me':'Писать выбранный микрофон отдельной дорожкой вместе со звуком приложений. Получаются Вы.wav, Собеседник.wav и общий WAV. Выключите, если ваш голос уже включён в общий микс.',
    'diary.night':'Разрешить подготовку дневника ночью: по умолчанию после 03:00 или после пробуждения. Нужны работающее ядро и доступная модель; сон и отсутствие лимита могут отложить запись.',
    'soul.on':'Добавлять автопортрет выбранного персонажа в контекст разговора. Выключение не удаляет сам текст души.',
    'soul.auto':'Разрешить автоматическое обновление души по дневнику. В текущем коде оно выполняется раз в неделю ночью; ручная кнопка обновления доступна отдельно.',
    'soul.constitution':'Включать пользовательские принципы в контекст разговора. Это инструкция модели, а не замена техническим ограничениям и подтверждениям действий.',
    'llm.auto_select':'Автоматически выбирать доступный профиль по приоритету и расписанию. Приоритет 1 — наивысший. При ручном выборе используется активный профиль.',
    'privacy.local_only':'Блокировать обращения к внешним сервисам. Codex, Claude Code и внешние API будут недоступны; локальные модели могут продолжать работу.',
    'tasks.auto_run':'Разрешить выполнение команд проекта без отдельного вопроса в рабочей копии. Проверьте доверие к проекту и ограничения инструментов.',
    'tasks.self_check':'Проверять итог задачи дополнительным проходом модели. Это расходует её лимиты и не заменяет запуск настоящих тестов.'
  };
  const team={
    enabled:'Включить совет планирования и назначения моделей команды. Обсуждение требует минимум двух разных моделей и расходует лимиты участников.',
    judge:'Модель, которая проверяет закрытие замечаний после единогласия участников. По умолчанию активная модель при запуске обсуждения; выбор закрепляется до перезапуска.',
    developer:'Модель, которая пишет и исправляет код после согласования плана. Пустое значение использует автовыбор или модель задач согласно настройкам.',
    tester:'Модель анализа покрытия и результатов тестов. Сами тесты выполняет ядро в окружении проекта. Пустое значение отключает отдельную модель анализа.',
    reviewer:'Модель независимого ревью кода и результата. Пустое значение оставляет самопроверку разработчиком.',
    max_rounds:'Максимальное число кругов передачи плана участникам. Если согласие не достигнуто, задача приостанавливается.',
    budget_minutes:'Общий предел времени обсуждения в минутах. Достижение предела не означает согласование плана.'
  };
  const actionHelp={
    login:'Начать вход в официальном приложении или CLI поставщика. Завершите авторизацию в открывшемся окне; пароль в чат не отправляйте. После входа проверьте статус подключения.',
    refresh:'Обновить статус авторизации и каталог моделей поставщика. Дождитесь результата; повторный вход требуется только при отсутствии действующей авторизации.',
    network:'Разрешить внешние подключения для всего Dewiar, выключив режим «только локально». Это разрешит также другие подключённые сетевые функции; проверьте, что согласны с этим.',
    effort:'Выбрать поддерживаемый уровень рассуждений модели. Более высокий уровень может увеличивать время ответа и расход лимитов.',

    test:'Отправить короткий проверочный запрос выбранной модели. Проверка может расходовать токены; результат покажет модель, задержку или ошибку.',
    models:'Получить и обновить каталог моделей на указанном сервере. Модели этим действием не скачиваются.',
    del:'Удалить выбранный профиль или объект. Перед действием проверьте, что выбран правильный элемент.',
    use:'Сохранить выбранную модель как профиль подключения. Проверьте её название и доступность.',
    task_accept:'Принять подготовленные изменения и перенести их из рабочей копии в исходный проект. Перед этим проверьте diff и результаты тестов.',
    task_stop:'Остановить выполнение задачи. Просмотрите её состояние перед возобновлением.',
    task_resume:'Возобновить задачу с сохранённым состоянием. Модели и инструменты продолжат расходовать доступные лимиты.',
    task_discard:'Отказаться от подготовленных изменений задачи. Перед действием проверьте содержимое рабочей копии.'
  };
  const selector='button,input,select,textarea,summary,[role="button"],a[data-go],[data-quota],[data-routing-summary]';
  const cache=new WeakMap();
  function describe(el){
    if(el.matches('[data-section-help]'))return;
    const prior=cache.get(el),current=el.getAttribute('title')||'';
    const original=prior&&current===prior.generated?prior.original:current;
    let label=helpText(el.getAttribute('aria-label')||el.textContent),text='';
    const f=el.dataset.f,k=el.dataset.k,a=el.dataset.a,t=el.dataset.team;
    if(ids[el.id])text=ids[el.id];
    else if(f&&fieldDescriptions[f])text=fieldDescriptions[f];
    else if(k&&settings[k])text=settings[k];
    else if(t&&team[t])text=team[t];
    else if(el.matches('[data-planner]'))text='Участник совета планирования. Выберите отдельную модель; для обсуждения нужны минимум две разные модели. Недоступный назначенный участник не заменяется молча.';
    else if(el.matches('[data-routing-summary]'))text='Текущее участие модели в автовыборе: приоритет, расписание и причина доступности или пропуска. Это сводка настроек, а не отдельное действие.';
    else if(el.matches('[data-quota]'))text='Сведения поставщика о лимитах и балансе, если они доступны. У нескольких моделей одной подписки лимиты могут быть общими; отсутствие данных не равно нулевому остатку.';
    else if(el.dataset.p&&window.DEWIAR_HELP[el.dataset.p])text=`Открыть раздел «${window.DEWIAR_HELP[el.dataset.p].title}». ${window.DEWIAR_HELP[el.dataset.p].what}`;
    else if(a&&actionHelp[a])text=actionHelp[a];
    else if(el.matches('[data-model-picker]'))text='Выбрать точный идентификатор из каталога сервера. Если модель отсутствует, обновите каталог или укажите идентификатор вручную. Выбор не устанавливает модель.';
    else if(el.matches('input,select,textarea')){
      const holder=el.closest('label.f,.switch,.f,.form-check'),clone=holder?.cloneNode(true);
      clone?.querySelectorAll('input,select,textarea,button,option').forEach(n=>n.remove());
      label=helpText(clone?.textContent||el.getAttribute('aria-label')||el.placeholder||k||f||'Поле настройки');
      text=original||label;
      if(el.type==='checkbox')text+=' Переключатель включает или выключает указанную настройку.';
      else if(el.tagName==='SELECT')text+=' Выберите подходящий вариант из списка.';
      else if(el.type==='password')text+=' Секретное значение: не публикуйте его и не добавляйте в отчёт об ошибке.';
      if(el.min)text+=` Минимум: ${el.min}.`;if(el.max)text+=` Максимум: ${el.max}.`;
    } else {
      const action=label||original||'Действие';
      if(original&&original.length>65)text=original;
      else if(/^(удалить|очистить|стереть)/i.test(action))text=`${original||action}. Действие меняет или удаляет выбранные данные. Перед выполнением проверьте объект и область действия.`;
      else if(/^(сохранить|применить)/i.test(action))text=`${original||action}. Применить введённые значения этого блока; проверьте поля и сообщение о результате.`;
      else if(/^(отмена|закрыть|✕|×)$/i.test(action))text=`${original||'Закрыть текущую форму или панель'}. Это действие не запускает новую задачу. Несохранённые изменения формы могут быть потеряны.`;
      else if(/обновить|проверить/i.test(action))text=`${original||action}. Повторно получить актуальное состояние соответствующего подключения или данных; дождитесь результата и проверьте сообщение об ошибке.`;
      else if(/архив/i.test(action))text=`${original||action}. Перенести выбранную беседу в архив или открыть архив согласно названию действия. Архив сжимается, но продолжает занимать место на диске.`;
      else if(/настроить/i.test(action))text=`${original||action}. Открыть параметры выбранного элемента. Изменяйте только нужные поля и проверяйте результат сохранения.`;
      else if(el.tagName==='SUMMARY')text=`${original||action}. Развернуть или свернуть дополнительные параметры; раскрытие само по себе не меняет настройки.`;
      else {const section=el.closest('section[id^="p-"]'),h=window.DEWIAR_HELP[section?.id.slice(2)];text=original||action;if(h)text+=` Действие раздела «${h.title}». Подробная инструкция доступна через «Помощь» у заголовка.`;else text+=' Наведите указатель для подсказки; перед выполнением проверьте выбранные данные.';}
    }
    text=helpText(text);if(text&&current!==text)el.title=text;
    if(!el.getAttribute('aria-label')&&!helpText(el.textContent)&&el.matches('button,[role="button"]'))el.setAttribute('aria-label',original||text);
    cache.set(el,{generated:text,original});
  }
  function enhance(node){
    if(node.nodeType!==1)return;
    if(node.matches?.(selector))describe(node);
    node.querySelectorAll?.(selector).forEach(describe);
    if(node.matches?.('section[id^="p-"]'))sectionHelp(node);
    node.querySelectorAll?.('section[id^="p-"]').forEach(sectionHelp);
    const section=node.closest?.('main section[id^="p-"]');if(section)sectionHelp(section);
  }
  enhance(document.body);
  const pending=new Set();let scheduled=false;
  function flush(){scheduled=false;for(const node of pending){if(node.isConnected)enhance(node);}pending.clear();}
  new MutationObserver(records=>{
    for(const r of records){if(r.type==='characterData'){const b=r.target.parentElement?.closest(selector);if(b)pending.add(b);}else{r.addedNodes.forEach(n=>{if(n.nodeType===1)pending.add(n);});const b=r.target.closest?.(selector);if(b)pending.add(b);}}
    if(pending.size&&!scheduled){scheduled=true;requestAnimationFrame(flush);}
  }).observe(document.body,{childList:true,subtree:true,characterData:true});
  for(const event of ['pointerover','focusin'])document.addEventListener(event,e=>{const el=e.target.closest?.(selector);if(el)describe(el);},{passive:true});
  window.refreshHelpTitles=()=>enhance(document.body);
})();
