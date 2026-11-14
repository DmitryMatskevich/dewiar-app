(() => {
'use strict';
const $ = s => document.querySelector(s), esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.get('token')) { sessionStorage.setItem('sites-token', fragment.get('token')); history.replaceState(null,'',location.pathname); }
let rows=[], status={}, providers=[], catalog={vault:[],servers:[],accounts:[],channels:{}}, currentTab='sites', editing=false, lastDiscovery=null, pendingDelete=null, availabilityFilter=['DOWN','UP','OFF'].includes(fragment.get('state'))?fragment.get('state'):'ALL', providerView=localStorage.getItem('sites-provider-view')==='rows'?'rows':'cards', providerPage=1, providerPageSize=10;
let incidentOverview = null;
const labels={UP:'Доступен',DOWN:'Недоступен',UNKNOWN:'Нет данных',CONFIG_ERROR:'Нужна настройка',PAUSED:'На паузе',PENDING:'Проверяем сбой'};
const probes={http:'HTTP(S)',http_port:'HTTP',https_port:'HTTPS',health:'HTML — тестовая страница',console:'Консоль',web:'Веб-сайт',dns:'DNS',ping:'Ping IP',ssh:'SSH',ftp:'FTP',mail:'Почта'};
const reasons={OK:'Проверка успешна',TIMEOUT:'Превышено время ожидания',HTTP_STATUS:'Неподходящий HTTP-код',TLS_HANDSHAKE:'Не удалось установить TLS',TLS_INVALID:'Сертификат не прошёл проверку',DNS_ERROR:'Не удалось разрешить имя',ICMP_NO_REPLY:'Нет ответа ICMP',CONNECTION_FAILED:'Ошибка соединения',HTTP_PROTOCOL:'Некорректный HTTP-ответ',CONTENT_MISMATCH:'Ожидаемый текст не найден',CONTENT_ENCODING:'Неподдерживаемое сжатие ответа',REDIRECT_LIMIT:'Слишком много перенаправлений',TARGET_POLICY:'Ограничение сетевой цели',BANNER_BAD:'Нет ожидаемого приветствия',STARTTLS_MISSING:'STARTTLS не предложен сервером',SUPERSEDED_BY_WEB_INCIDENT:'Объединён с веб-инцидентом',INVALIDATED_HTTP_HOST_BUG:'Измерение признано ошибочным и исключено'};
function diagnosis(r){let d={};try{d=JSON.parse(r.detail||'{}');}catch{}const parts=[reasons[r.reason]||r.reason];if(d.addresses)parts.push('IP: '+d.addresses.join(', '));else if(d.ip)parts.push('IP: '+d.ip);if(d.http_code)parts.push('HTTP '+d.http_code);if(d.port)parts.push('Порт '+d.port);if(d.tls_until)parts.push('TLS до '+new Date(d.tls_until*60000).toLocaleDateString('ru-RU'));if(d.message)parts.push(d.message);return parts.map(esc).join('<br>');}
const when=m=>m ? new Date(m*60000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
function normalizeSiteUrl(value){const raw=String(value||'').trim();if(!raw)return '';const hasScheme=/^[a-z][a-z\d+.-]*:\/\//i.test(raw);let url;try{url=new URL(hasScheme?raw:'https://'+raw);}catch{throw Error('Проверьте адрес сайта');}if(!['http:','https:'].includes(url.protocol)||!url.hostname||url.username||url.password)throw Error('Укажите HTTP(S)-адрес без логина и пароля');return url.toString();}
function parseDateInput(raw){const s=String(raw||'').trim();if(!s)return '';let y,m,d,parts;if(/^\d{4}\D+\d{1,2}\D+\d{1,2}$/.test(s)){parts=s.split(/\D+/).map(Number);[y,m,d]=parts;}else if(/^\d{8}$/.test(s)){d=Number(s.slice(0,2));m=Number(s.slice(2,4));y=Number(s.slice(4));}else if(/^\d{1,2}\D+\d{1,2}\D+\d{4}$/.test(s)){parts=s.split(/\D+/).map(Number);[d,m,y]=parts;}else return null;const dt=new Date(Date.UTC(y,m-1,d));if(dt.getUTCFullYear()!==y||dt.getUTCMonth()!==m-1||dt.getUTCDate()!==d)return null;return `${String(y).padStart(4,'0')}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;}
function until(iso,days,label){if(!iso)return '';const [y,m,d]=iso.split('-').map(Number),dateText=[String(d).padStart(2,'0'),String(m).padStart(2,'0'),y].join('.'),word=days<0?`истёк ${Math.abs(days)} дн. назад`:days===0?'истекает сегодня':`через ${days} дн.`,cls=days<0||days<=7?'renewal-bad':days<=30?'renewal-soon':'renewal-good';return `<span class="${cls}">${label}: ${word} (${dateText})</span>`;}
const percent=n=>n==null?'Нет данных':Number(n).toLocaleString('ru-RU',{maximumFractionDigits:2})+'%';
const badge=s=>`<span class="badge ${esc(s)}">${esc(labels[s]||s)}</span>`;
function panelState(provider){const check=provider?.panel_check,now=Math.floor(Date.now()/60000);if(!check||check.until_min<=now)return 'unknown';return check.state==='UP'?'good':check.state==='DOWN'?'bad':'unknown';}
function panelMark(provider){if(!provider?.panel_url)return '';const state=panelState(provider),symbol=state==='good'?'+':state==='bad'?'−':'?';return `<span class="provider-console-state ${state}" title="Консоль провайдера: ${state==='good'?'отвечает':state==='bad'?'нет ответа':'не проверена'}" aria-label="Консоль провайдера: ${state==='good'?'отвечает':state==='bad'?'нет ответа':'не проверена'}">${symbol}</span>`;}
function setTheme(theme){const dark=theme==='dark';document.body.classList.toggle('theme-dark',dark);const b=$('#theme-toggle');if(b){const label=dark?'Включить светлую тему':'Включить тёмную тему';b.setAttribute('aria-label',label);b.title=label;}localStorage.setItem('sites-theme',dark?'dark':'light');}
setTheme(localStorage.getItem('sites-theme')==='dark'?'dark':'light');
async function api(path,data) {
 const r=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+(sessionStorage.getItem('sites-token')||''),'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
 const v=await r.json(); if(!r.ok) throw Error(v.error||'Ошибка запроса'); return v;
}
function notice(text,error=false){$('#message').textContent=text;$('#message').className=error?'error':'';}
async function action(fn,button){if(button)button.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{if(button)button.disabled=false;}}
function tooltipFor(el){
 const d=el.dataset||{},name=(el.getAttribute('aria-label')||el.textContent||el.title||el.id||'').trim().replace(/\s+/g,' ');
 if(el.tagName==='BUTTON'){
  if(d.check)return `Запустить внеплановую проверку сайта «${rows.find(s=>s.id===d.check)?.name||name}». Результат появится в фоновых процессах; эта проверка не меняет расчёт аптайма.`;
  if(d.detail)return `Открыть историю сайта «${rows.find(s=>s.id===d.detail)?.name||name}»: аптайм, покрытие, результаты проверок и зарегистрированные инциденты.`;
  if(d.edit)return `Открыть форму редактирования сайта «${rows.find(s=>s.id===d.edit)?.name||name}». Изменения применяются после сохранения карточки.`;
  if(d.service){const def=serviceDefs.find(x=>x[0]===d.service);return `Открыть настройки сервиса «${def?.[1]||d.service}» для сайта «${rows.find(s=>s.id===d.site)?.name||''}». Здесь можно задать адрес или параметры проверки.`;}
  if(d.providerEdit)return `Изменить карточку провайдера «${providers.find(p=>p.id===d.providerEdit)?.name||''}»: название, сайт, панель, контакты и каналы поддержки.`;
  if(d.providerComplaint)return `Подготовить черновик обращения провайдеру «${providers.find(p=>p.id===d.providerComplaint)?.name||''}». Текст можно проверить и исправить перед использованием.`;
  if(d.providerView)return `Показать список провайдеров в виде «${d.providerView==='rows'?'строк':'карточек'}». Выбор сохраняется на этом компьютере.`;
  if(d.providerPage)return `Перейти ${Number(d.providerPage)<0?'на предыдущую':'на следующую'} страницу списка провайдеров.`;
  if(d.tab)return `Открыть раздел «${({sites:'Обзор сайтов',incidents:'Инциденты',deadlines:'Сроки',providers:'Хостинг-провайдеры',processes:'Фоновые процессы',notifications:'Уведомления'}[d.tab]||d.tab)}».`;
  if(d.test)return `Отправить тестовое уведомление через канал «${d.test}», выбранный в настройках. Это тестовая доставка, не уведомление об инциденте.`;
  if(el.hasAttribute('data-contact-remove'))return 'Удалить этот контакт из редактируемого списка провайдера. Изменение сохранится только после нажатия «Сохранить».';
  if(el.id==='add')return 'Создать новую карточку сайта. Мониторинг можно включить или настроить позже.';
  if(el.id==='provider-add')return 'Добавить провайдера в справочник, указать ссылку на его панель и структурированные каналы поддержки.';
  if(el.id==='refresh')return 'Заново загрузить список сайтов, их последние состояния и статистику за выбранный период.';
  if(el.id==='pause')return status.paused?'Возобновить автоматические проверки сайтов.':'Приостановить автоматические проверки сайтов; уже сохранённые карточки останутся в списке.';
  if(el.id==='theme-toggle')return document.body.classList.contains('theme-dark')?'Переключить интерфейс на светлую тему.':'Переключить интерфейс на тёмную тему.';
  if(el.id==='contact-add')return 'Добавить пустую строку контакта в карточку провайдера. Заполните тип канала, подпись и значение, затем сохраните карточку.';
  if(el.id==='complaint-copy')return 'Скопировать подготовленный текст обращения в буфер обмена, чтобы вставить его в выбранный канал поддержки.';
  if(/сохранить/i.test(name))return 'Сохранить введённые значения текущей формы. Перед закрытием проверьте адреса, ссылки и контактные данные.';
  if(/закрыть|отмена|назад/i.test(name))return 'Закрыть эту форму или вернуться назад. Несохранённые изменения могут быть потеряны.';
  if(/обновить/i.test(name))return 'Обновить отображаемые данные и повторно запросить состояние сервера.';
  return name?`Нажмите, чтобы выполнить действие «${name}».`:'';
 }
 if(['INPUT','SELECT','TEXTAREA'].includes(el.tagName)){
  const field=el.getAttribute('aria-label')||el.getAttribute('placeholder')||el.name||el.id||'значение';
  if(el.type==='checkbox')return `Переключатель «${field}». Включите или выключите эту настройку; состояние изменится после сохранения формы, если оно предусмотрено.`;
  if(el.tagName==='SELECT')return `Список «${field}». Выберите значение; оно применяется к отображению или настройке, указанной в названии поля.`;
  if(el.tagName==='TEXTAREA')return `Поле «${field}». Введите или отредактируйте текст; содержание будет использовано только после сохранения формы.`;
  return `Поле «${field}». ${el.type==='search'?'Введите слова для поиска подходящей записи.':'Введите значение для настройки карточки; оно будет сохранено после отправки формы.'}`;
 }
 return '';
}
function ensureTooltips(root=document){root.querySelectorAll('button,input,select,textarea').forEach(el=>{const detail=tooltipFor(el);if(!detail)return;if(!el.title||el.title.length<55||el.id==='pause')el.title=detail;});}
function metrics(){const n=s=>rows.filter(r=>r.state===s).length;$('#metrics').innerHTML=[['Всего сайтов',rows.length],['Доступны',n('UP')],['Недоступны',n('DOWN')],['Без данных / пауза',rows.length-n('UP')-n('DOWN')]].map(([name,val])=>`<div class="metric"><strong>${val}</strong><span>${name}</span></div>`).join('');}
const serviceDefs=[['ip','IP','ping','ping','ssh-settings','ssh_host'],['dns','DNS','dns','dns','dns-settings','dns_host'],['http','HTTP','check_http','http_port','web-settings','url'],['https','HTTPS','check_https','https_port','web-settings','url'],['html','HTML','health_url','health','html-settings','health_url'],['console','Консоль','console_url','console','console-settings','console_url'],['ssh','SSH','ssh','ssh','ssh-settings','ssh_host'],['ftp','FTP','ftp','ftp','ssh-settings','ftp_port']];
const probeIndicators = {
 UP: ['+', 'good', 'проверка прошла'],
 DOWN: ['−', 'bad', 'проверка не прошла'],
 UNKNOWN: ['?', 'unset', 'нет свежего результата'],
 PAUSED: ['Ⅱ', 'unset', 'проверки на паузе'],
 DISABLED: ['·', 'unset', 'не настроено'],
};

function serviceIndicator(site, definition, now) {
 const [, label, setting, probe] = definition;
 const result = site.results.find(r => r.probe === probe && r.until_min > now);
 const enabled = Boolean(site[setting]) && (!['check_http', 'check_https'].includes(setting) || Boolean(site.url));
 let state = 'DISABLED';
 if (enabled || result?.diagnostic) {
  state = site.state === 'PAUSED' ? 'PAUSED' : result?.state || 'UNKNOWN';
 }
 const [symbol, color, description] = probeIndicators[state] || probeIndicators.UNKNOWN;
 const reason = state === 'DOWN' && result?.reason ? ` · ${reasons[result.reason] || result.reason}` : '';
 const source = result?.diagnostic && state !== 'PAUSED' ? ' · разовая диагностика' : '';
 const hint = `${label}: ${description}${reason}${source} · открыть настройки`;
 return `<button class="service-icon ${color}" data-service="${definition[0]}" data-site="${esc(site.id)}" title="${esc(hint)}" aria-label="${esc(hint)}"><span aria-hidden="true">${symbol}</span><small>${label}</small></button>`;
}
function siteFailure(site, now) {
 if (site.state === 'PAUSED') return null;
 const failed = site.results.filter(result => !result.diagnostic && result.probe !== 'web' &&
  result.until_min > now && ['DOWN','CONFIG_ERROR'].includes(result.state));
 const aggregate = site.results.find(result => result.probe === 'web' && result.until_min > now && result.state === 'DOWN');
 if (aggregate) {
  const required = [site.url ? 'http' : 'mail'];
  if (site.health_required) required.push('health');
  const result = failed.find(result => required.includes(result.probe) && result.state === 'DOWN') || aggregate;
  return failureDetails(result);
 }
 const result = failed.sort((a,b) => b.at - a.at)[0];
 return result ? failureDetails(result) : null;
}

function failureDetails(result) {
 return {label: result.reason === 'DNS_ERROR' ? 'DNS' : probes[result.probe] || result.probe,
  reason: reasons[result.reason] || result.reason || 'Проверка не прошла', at: result.at};
}

function render(){
 metrics();const q=$('#search').value.trim().toLowerCase();
 const now=Math.floor(Date.now()/60000);
 const filtered=[...rows].sort((a,b)=>({DOWN:0,PENDING:0,UNKNOWN:1,CONFIG_ERROR:1,UP:2,PAUSED:3}[a.state]??1)-({DOWN:0,PENDING:0,UNKNOWN:1,CONFIG_ERROR:1,UP:2,PAUSED:3}[b.state]??1)||a.name.localeCompare(b.name)).filter(r=>(availabilityFilter==='ALL'||availabilityFilter==='DOWN'&&r.state==='DOWN'||availabilityFilter==='UP'&&r.state==='UP'||availabilityFilter==='OFF'&&!r.enabled)&&[r.name,r.url,r.hoster,r.dns_provider].join(' ').toLowerCase().includes(q));
 $('#list').innerHTML=filtered.length?filtered.map(s=>{
  const dates=[until(s.hosting_expires,s.hosting_expires_in_days,'Хостинг'),until(s.domain_expires,s.domain_expires_in_days,'Домен')].filter(Boolean);
  const failure=siteFailure(s,now);
  const health=s.state==='UP'?'good':s.state==='DOWN'?'bad':s.state==='PENDING'?'pending':'unknown';
  return `<article class="site-row">
   <div class="site-topline">
   <div class="site-actions">
    <button class="row-icon refresh ${health}" data-check="${esc(s.id)}" title="Проверить сайт" aria-label="Проверить ${esc(s.name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 9a7 7 0 0 1 11.7-2L20 12M4 12l2.7 5a7 7 0 0 0 11.7-2"/></svg></button>
    <button class="row-icon history" data-detail="${esc(s.id)}" title="История и сведения" aria-label="История и сведения: ${esc(s.name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg></button>
    <button class="row-icon edit" data-edit="${esc(s.id)}" title="Изменить сайт" aria-label="Изменить ${esc(s.name)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m14 5 5 5M4 20l4.4-.9L19.3 8.2a2.1 2.1 0 0 0-3-3L5.4 16.1 4 20z"/></svg></button>
   </div>
   ${s.url?`<a class="site-title" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="Открыть ${esc(s.name)} в новой вкладке">${esc(new URL(s.url).hostname)}</a>`:`<strong class="site-title">${esc(s.name)}</strong>`}
   <div class="hosting-provider" aria-label="Хостинг-провайдер">${s.hoster?(s.hoster_url?`<a href="${esc(s.hoster_url)}" target="_blank" rel="noopener noreferrer">${esc(s.hoster)}</a>`:`<strong>${esc(s.hoster)}</strong>`):'<span class="site-sub">Не указан</span>'}${panelMark(s.hosting_provider)}${s.dns_provider?`<small class="site-sub">DNS: ${esc(s.dns_provider)}</small>`:''}</div>
   <div class="row-uptime ${health}" title="${esc('Доступность: '+(percent(s.stats.uptime))+' · '+(labels[s.state]||s.state))}"><strong>${percent(s.stats.uptime)}</strong></div>
   </div>
   <div class="site-details">
    <div class="site-observation">${badge(s.state)}<span class="muted small">Покрытие за выбранный период ${percent(s.stats.coverage)}</span></div>
    <div class="service-icons" aria-label="Состояние сервисов">${serviceDefs.map(d=>serviceIndicator(s,d,now)).join('')}</div>
    ${failure ? `<p class="site-failure small"><strong>${esc(failure.label)}</strong> · ${esc(failure.reason)} <span class="muted">· проверено ${when(failure.at)}</span></p>` : ''}
    ${dates.length?`<div class="renewal-info">${dates.join('<br>')}</div>`:''}
   </div>
  </article>`
 }).join(''):'<div class="empty">Нет сайтов, соответствующих выбранному состоянию и поиску.</div>';
}
async function refresh(){
 const [sites, st, directory, incidents] = await Promise.all([
  api('sites?days='+$('#period').value), api('status'), api('providers'),
  api('incidents?view=overview').catch(() => null),
 ]);
 rows=sites.items;
 status=st;
 providers=directory.items;
 incidentOverview=incidents;
 if($('#message').className==='error')notice('Связь восстановлена');
 $('#connection').textContent=`Процесс ${status.pid} · ${status.active} заданий`;
 $('#test-banner').hidden=!status.test;
 if(status.test)document.title='ТЕСТ — Сайты · Dewiar';
 render();
 renderIncidents();
 renderDeadlines();
 renderProcesses();
 if(currentTab==='providers')renderProviders();
 if(currentTab==='notifications')await deliveries();
}
function options(items,label){return '<option value="">Не выбрано</option>'+items.map(i=>`<option value="${esc(i.id)}">${esc(label(i))}</option>`).join('');}
async function loadAccess(){catalog=await api('access');$('#vault-options').innerHTML=options(catalog.vault,i=>i.name+(i.login?' · '+i.login:''));$('#server-options').innerHTML=options(catalog.servers,i=>i.name+' · '+i.host);$('#mail-accounts').innerHTML=options(catalog.accounts,i=>i.address);$('#channel-hint').textContent=['telegram','max'].map(k=>k+': '+(catalog.channels[k]?'подключён':'не подключён')).join(' · ')+(catalog.errors.length?' · '+catalog.errors.join(' '):'');}
function renderProviders(){const typeName={telegram:'Telegram / поддержка',ticket:'Тикет в панели',email:'E-mail',phone:'Телефон',chat:'Онлайн-чат',news:'Новости',status:'Статус сервиса',feedback:'Отзывы / жалоба на оператора',complaint:'Претензии по услугам',abuse:'Обработка abuse-жалоб',other:'Другое'},pages=Math.max(1,Math.ceil(providers.length/providerPageSize));providerPage=Math.min(providerPage,pages);const start=(providerPage-1)*providerPageSize,visible=providers.slice(start,start+providerPageSize);$('#provider-controls').innerHTML=`<div class="provider-view-switch" role="group" aria-label="Вид списка провайдеров"><button type="button" data-provider-view="cards" aria-pressed="${providerView==='cards'}">Карточки</button><button type="button" data-provider-view="rows" aria-pressed="${providerView==='rows'}">Строки</button></div><span class="provider-page-count">${providers.length?`${start+1}–${Math.min(start+providerPageSize,providers.length)} из ${providers.length}`:'0 провайдеров'}</span><label class="provider-page-size">На странице<select id="provider-page-size"><option value="10" ${providerPageSize===10?'selected':''}>10</option><option value="25" ${providerPageSize===25?'selected':''}>25</option><option value="50" ${providerPageSize===50?'selected':''}>50</option></select></label><div class="provider-pagination"><button type="button" data-provider-page="-1" ${providerPage<=1?'disabled':''}>Назад</button><span>Страница ${providerPage} из ${pages}</span><button type="button" data-provider-page="1" ${providerPage>=pages?'disabled':''}>Вперёд</button></div>`;const hrefFor=c=>c.url||(c.type==='email'||c.type==='feedback'||c.type==='complaint'||c.type==='abuse'?'mailto:'+c.value:c.type==='phone'?'tel:'+c.value.replace(/[^+\d]/g,''):'');
 if(!providers.length){$('#provider-list').className='provider-list cards';$('#provider-list').innerHTML='<div class="empty">Провайдеров пока нет.</div>';return;}
 if(providerView==='rows'){const rows=visible.map(p=>`<tr><th scope="row"><strong>${esc(p.name)}</strong>${p.website_url?`<a class="provider-row-site" href="${esc(p.website_url)}" target="_blank" rel="noopener noreferrer">Сайт</a>`:''}</th><td>${p.panel_url?`<a href="${esc(p.panel_url)}" target="_blank" rel="noopener noreferrer">${esc(p.panel_name||'Панель')}</a> ${panelMark(p)}`:'—'}</td><td><div class="provider-row-contacts">${p.contacts.slice(0,3).map(c=>{const href=hrefFor(c);return href?`<a title="${esc((c.label?c.label+': ':'')+c.value)}" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(c.label||c.value)}</a>`:`<span title="${esc(c.value)}">${esc(c.label||c.value)}</span>`}).join('')}${p.contacts.length>3?`<span class="muted">+${p.contacts.length-3}</span>`:''}</div></td><td><div class="provider-row-actions"><button data-provider-complaint="${esc(p.id)}">Обращение</button><button data-provider-edit="${esc(p.id)}">Изменить</button></div></td></tr>`).join('');$('#provider-list').className='provider-list rows';$('#provider-list').innerHTML=`<div class="provider-table-wrap"><table class="provider-table"><thead><tr><th>Провайдер</th><th>Панель</th><th>Поддержка и каналы</th><th>Действия</th></tr></thead><tbody>${rows}</tbody></table></div>`;return;}
 $('#provider-list').className='provider-list cards';$('#provider-list').innerHTML=visible.map(p=>`<article class="provider-card"><div class="provider-card-head"><div class="provider-card-title"><h3>${esc(p.name)}</h3><div class="provider-links">${p.website_url?`<a href="${esc(p.website_url)}" target="_blank" rel="noopener noreferrer">Сайт провайдера</a>`:''}${p.panel_url?`<a href="${esc(p.panel_url)}" target="_blank" rel="noopener noreferrer">${esc(p.panel_name||'Панель')}</a> ${panelMark(p)}`:''}</div></div><button data-provider-edit="${esc(p.id)}">Изменить</button></div>${p.contacts.length?`<ul class="provider-contact-list">${p.contacts.map(c=>{const href=hrefFor(c);return `<li><span class="contact-type">${typeName[c.type]||typeName.other}</span><span class="contact-label">${esc(c.label||'')}</span>${href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(c.value)}</a>`:`<strong>${esc(c.value)}</strong>`}</li>`}).join('')}</ul>`:'<p class="muted">Контакты не добавлены.</p>'}<div class="provider-card-footer">${p.source_url?`<a class="provider-source" href="${esc(p.source_url)}" target="_blank" rel="noopener noreferrer">Источник</a>`:''}<button data-provider-complaint="${esc(p.id)}">Подготовить обращение</button></div>${p.notes?`<p class="muted provider-notes">${esc(p.notes)}</p>`:''}</article>`).join('');}
function providerOptions(selected=''){const sel=$('#provider-options');sel.innerHTML='<option value="">Не указан</option>'+providers.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')+'<option value="__custom">Другой провайдер…</option>';sel.value=selected;syncCustomHoster();}
function dnsProviderOptions(selected=''){const sel=$('#dns-provider-options');sel.innerHTML='<option value="">Не указан</option>'+providers.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')+'<option value="__custom">Другой DNS-провайдер…</option>';sel.value=selected;syncCustomDnsProvider();}
function syncCustomHoster(){const custom=$('#provider-options').value==='__custom';document.querySelectorAll('.custom-hoster-field').forEach(el=>el.hidden=!custom);}
function syncCustomDnsProvider(){const custom=$('#dns-provider-options').value==='__custom';document.querySelectorAll('.custom-dns-provider-field').forEach(el=>el.hidden=!custom);}
function requestDelete(kind,id,name,rev){pendingDelete={kind,id,name,rev};const sites=kind==='provider'?rows.filter(s=>s.hosting_provider_id===id||s.dns_provider_id===id):[];$('#delete-title').textContent=kind==='site'?'Удалить сайт?':'Удалить хостинг-провайдера?';$('#delete-message').textContent=kind==='site'?`Удалить «${name}»? Будут удалены карточка, история доступности, результаты проверок и инциденты. Это действие нельзя отменить.`:sites.length?`Удалить «${name}» из справочника? Он назначен ${sites.length} сайтам. На карточках этих сайтов останутся название и ссылка провайдера, но связь со справочником будет снята.`:`Удалить «${name}» из справочника хостинг-провайдеров? Это действие нельзя отменить.`;$('#delete-confirm').showModal();}
async function edit(id,service){await loadAccess();if(!providers.length)providers=(await api('providers')).items;const f=$('#site-form');f.reset();f.elements.id.value='';f.elements.rev.value='';const s=rows.find(r=>r.id===id);$('#delete-site').hidden=!s;if(s)for(const [k,v]of Object.entries(s)){const el=f.elements[k];if(el&&'value'in el){if(el.type==='checkbox')el.checked=!!v;else el.value=v;}}providerOptions(s?.hosting_provider_id||(s?.hoster?'__custom':''));dnsProviderOptions(s?.dns_provider_id||(s?.dns_provider?'__custom':''));if(s&&!s.hosting_provider_id){f.elements.hoster.value=s.hoster||'';f.elements.hoster_url.value=s.hoster_url||'';}$('#editor-title').textContent=s?'Изменить сайт':'Новый сайт';$('#form-error').textContent='';$('#discovery-output').textContent='';$('#use-expiry').hidden=true;lastDiscovery=null;editing=true;$('#editor').showModal();if(service){const def=serviceDefs.find(x=>x[0]===service);if(def){$('#'+def[4]).open=true;f.elements[def[5]]?.focus();}}else f.elements.name.focus();}
function contactRow(contact={type:'phone',label:'',value:'',url:''}){const types=[['phone','Телефон'],['email','E-mail'],['ticket','Тикет в панели'],['telegram','Telegram / поддержка'],['news','Новостной канал'],['status','Статус сервиса'],['chat','Онлайн-чат'],['feedback','Отзывы / жалоба на оператора'],['complaint','Претензии по услугам'],['abuse','Обработка abuse-жалоб'],['other','Другое']];const row=document.createElement('div');row.className='provider-contact-row';row.innerHTML=`<select class="contact-type-input" aria-label="Тип канала">${types.map(([v,t])=>`<option value="${v}" ${contact.type===v?'selected':''}>${t}</option>`).join('')}</select><input class="contact-label-input" maxlength="120" placeholder="Например: поддержка, Россия" aria-label="Подпись" value="${esc(contact.label||'')}"><input class="contact-value-input" maxlength="500" placeholder="Телефон, адрес или имя бота" aria-label="Значение" value="${esc(contact.value||'')}"><input class="contact-url-input" type="url" placeholder="Ссылка (если есть)" aria-label="Ссылка" value="${esc(contact.url||'')}"><button type="button" data-contact-remove aria-label="Удалить канал">×</button>`;$('#provider-contacts').append(row);}
async function editProvider(id){const p=providers.find(x=>x.id===id);const f=$('#provider-form');f.reset();$('#provider-contacts').replaceChildren();f.elements.id.value=p?.id||'';f.elements.rev.value=p?.rev||'';$('#delete-provider').hidden=!p;for(const k of ['name','website_url','panel_url','panel_name','source_url','notes'])f.elements[k].value=p?.[k]||'';(p?.contacts?.length?p.contacts:[{type:'email',label:'Техническая поддержка',value:'',url:''}]).forEach(contactRow);$('#provider-editor-title').textContent=p?'Изменить провайдера':'Новый провайдер';$('#provider-error').textContent='';$('#provider-editor').showModal();if(!p)f.elements.name.focus();}
function updateComplaintEmail(){const to=$('#complaint-to').value.trim(),subject=$('#complaint-subject').value,body=$('#complaint-body').value,link=$('#complaint-email');link.href=to?`mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`:'#';link.setAttribute('aria-disabled',to?'false':'true');}
function editComplaint(id){const p=providers.find(x=>x.id===id);if(!p)return;$('#complaint-title').textContent=`Обращение: ${p.name}`;const emailContacts=p.contacts.filter(c=>['feedback','complaint','abuse','email'].includes(c.type)&&c.value.includes('@'));$('#complaint-targets').innerHTML=emailContacts.map(c=>`<button type="button" data-complaint-to="${esc(c.value)}">${esc(c.label||c.value)} · ${esc(c.value)}</button>`).join('');const target=emailContacts.find(c=>c.type==='feedback')||emailContacts.find(c=>c.type==='complaint')||emailContacts[0];$('#complaint-to').value=target?.value||'';$('#complaint-ticket-links').innerHTML=p.contacts.filter(c=>c.type==='ticket'&&c.url).map(c=>`<a href="${esc(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.label||c.value||'Открыть тикет в панели')}</a>`).join('');const legalNote=$('#complaint-provider-note');legalNote.hidden=p.id!=='beget';legalNote.innerHTML=p.id==='beget'?'Для договорной претензии Beget указывает письменное обращение на почтовый или юридический адрес; e-mail abuse@beget.com можно использовать как дубликат. Срок рассмотрения — до 30 календарных дней. <a href="https://beget.com/files/contract_org.pdf" target="_blank" rel="noopener noreferrer">Условия договора</a>':'';$('#complaint-subject').value=`Обращение по услугам ${p.name}`;$('#complaint-body').value=`Здравствуйте.\n\nПрошу рассмотреть обращение по услугам провайдера ${p.name}.\n\nСайт / домен: [укажите]\nЛогин аккаунта / номер договора: [укажите]\nДата и время события: [укажите]\n\nОписание проблемы и последовательность событий:\n[кратко изложите факты]\n\nПрошу: [укажите ожидаемое решение]\n\nНомер предыдущего обращения (если есть): [укажите]\nПодтверждения: [перечислите ссылки, скриншоты и документы]\n\nПрошу подтвердить получение обращения и сообщить его номер и срок ответа.`;updateComplaintEmail();$('#complaint-editor').showModal();}
async function tab(name){currentTab=name;for(const n of ['sites','incidents','deadlines','providers','processes','notifications']){
 $('#'+n+'-view').hidden=n!==name;
 const button=document.querySelector(`nav [data-tab="${n}"]`);
 button.classList.toggle('selected',n===name);
 if(n===name)button.setAttribute('aria-current','page');
 else button.removeAttribute('aria-current');
 }if(name==='providers'){providers=(await api('providers')).items;renderProviders();}if(name==='notifications'){await loadAccess();const f=$('#notify-form'),cfg=status.settings;for(const k of ['telegram','max','email','simulation'])f.elements[k].checked=cfg.channels.includes(k);f.elements.mail_account_id.value=cfg.mail_account_id;f.elements.email_to.value=cfg.email_to;await deliveries();}}
function providerMatch(candidate){if(!candidate)return null;const text=String(candidate.name||candidate.operator||'').toLowerCase().replace(/[^a-zа-яё0-9]+/g,'');const exact=providers.find(p=>String(p.name).toLowerCase().replace(/[^a-zа-яё0-9]+/g,'')===text);if(exact)return exact;const aliases=[['beget',['beget','бегет']],['firstvds',['firstvds','jsciot']],['timeweb',['timeweb']],['timeweb-cloud',['timewebcloud']],['sweb',['sweb','spaceweb']],['nic-ru',['nicru','rucenter','руцентр']]];const alias=aliases.find(([,values])=>values.some(v=>text.includes(v)));if(!alias)return null;const candidates=providers.filter(p=>{const name=String(p.name).toLowerCase().replace(/[^a-zа-яё0-9]+/g,'');return alias[1].some(v=>name.includes(v));});return candidates[0]||null;}
function assignIfEmpty(form,name,value){if(value==null||value==='')return;const field=form.elements[name];if(field&&!String(field.value||'').trim())field.value=String(value);}
function showDiscovery(d){lastDiscovery=d;const f=$('#site-form');const formDomain=d.domain||'';assignIfEmpty(f,'name',formDomain);assignIfEmpty(f,'url',formDomain?normalizeSiteUrl(formDomain):'');assignIfEmpty(f,'dns_host',formDomain);assignIfEmpty(f,'server_ip',d.addresses?.[0]);const network=d.networks?.[0],operatorCandidate=network?.operator||network?.organization||network?.network;assignIfEmpty(f,'network_operator',operatorCandidate);assignIfEmpty(f,'registrar',d.registrar);assignIfEmpty(f,'dns_nameservers',(d.nameservers||[]).join(', '));assignIfEmpty(f,'dns_mx_hosts',(d.mx_hosts||[]).join(', '));assignIfEmpty(f,'domain_expires',d.domain_expires);assignIfEmpty(f,'mail_mx_host',d.mx_hosts?.length===1?d.mx_hosts[0]:'');
 const dns=d.dns_provider_candidate,host=d.hosting_provider_candidate|| (operatorCandidate?{name:operatorCandidate,confidence:'не подтверждена'}:null),mail=d.mail_provider_candidate,dnsMatch=providerMatch(dns),hostMatch=providerMatch(host),mailMatch=providerMatch(mail);
 if(dns){assignIfEmpty(f,'dns_provider',dns.name);assignIfEmpty(f,'dns_provider_url',dns.url);assignIfEmpty(f,'dns_panel_url',dns.panel);assignIfEmpty(f,'dns_panel_name',dns.panel_name);const match=dnsMatch;if(match&&!f.elements.dns_provider_id.value)dnsProviderOptions(match.id);}
 if(host){assignIfEmpty(f,'hosting_candidate',host.name||host.operator);if(hostMatch&&!f.elements.hosting_provider_id.value){providerOptions(hostMatch.id);f.elements.panel_url.value=hostMatch.panel_url||host.panel||'';f.elements.panel_name.value=hostMatch.panel_name||host.panel_name||'';}}
 if(mail){assignIfEmpty(f,'mail_provider',mail.name);if(mailMatch&&d.mx_hosts?.length===1)assignIfEmpty(f,'mail_host',d.mx_hosts[0]);}
 const orgs=[...new Set((d.networks||[]).map(n=>n.organization||n.network).filter(Boolean))],query=orgs[0]||d.domain,search='https://duckduckgo.com/?q='+encodeURIComponent(query+' официальный хостинг панель управления');
 const candidates=[dns?`DNS: ${esc(dns.name)}`:'DNS-провайдер не определён',host?`Хостинг-кандидат: ${esc(host.name||host.operator)}${host.confidence?` (${esc(host.confidence)} уверенность)`:''}`:'Хостинг-провайдер по IP/ASN не определён',mail?`Почта: ${esc(mail.name)}`:''];
 $('#discovery-output').innerHTML=`<b>Найденные данные заполнены в карточке</b><br>Домен: ${esc(d.domain)}<br>IP: ${esc((d.addresses||[]).join(', ')||'не найден')}<br>NS: ${esc((d.nameservers||[]).join(', ')||'не найдены')}<br>Регистратор: ${esc(d.registrar||'не определён')}<br>${d.domain_expires?'Срок домена по RDAP: '+esc(d.domain_expires)+'<br>':''}${(d.networks||[]).map(n=>`Сеть ${esc(n.ip)}: ${esc([n.operator,n.organization,n.network,n.country].filter(Boolean).join(' · ')||'владелец не определён')}`).join('<br>')}<br>${candidates.filter(Boolean).join('<br>')}<br><span>${esc(d.note||'Публичные сетевые данные могут указывать на оператора сети, а не на компанию, выставляющую счёт за хостинг.')}</span><br><a href="${esc(d.sources?.[0]?.url||'#')}" target="_blank" rel="noopener noreferrer">Источник RDAP домена</a> · <a href="${esc(search)}" target="_blank" rel="noopener noreferrer">Искать официальный сайт и панель владельца сети</a>`;
 $('#use-expiry').hidden=!d.domain_expires;$('#dns-settings').open=true;if(d.domain_expires)$('#renewal-settings').open=true;
 if(hostMatch)notice(`Найдены IP и провайдер ${hostMatch.name}; проверьте кандидата и сохраните карточку.`);else if(d.addresses?.length)notice('IP и DNS найдены; хостинг-провайдер автоматически не подтверждён. Проверьте сведения перед сохранением.');else notice('Домен просканирован, но IP-адрес не найден.',true);
}
$('#site-form').elements.url.addEventListener('blur',e=>{if(!e.target.value.trim())return;try{e.target.value=normalizeSiteUrl(e.target.value);$('#form-error').textContent='';}catch(err){$('#form-error').textContent=err.message;}});
$('#discover').addEventListener('click',e=>action(async()=>{const f=$('#site-form'),field=f.elements.url;field.value=normalizeSiteUrl(field.value);const domain=new URL(field.value).hostname;$('#discovery-output').textContent='Запрашиваю публичные DNS/RDAP данные…';$('#use-expiry').hidden=true;showDiscovery(await api('discover',{domain}));},e.target));
$('#use-expiry').addEventListener('click',()=>{if(lastDiscovery?.domain_expires)$('#site-form').elements.domain_expires.value=lastDiscovery.domain_expires;$('#renewal-settings').open=true;});
$('#site-form').addEventListener('keydown',e=>{if(e.key!=='Enter'||e.isComposing||!(e.target instanceof HTMLInputElement))return;if(['button','submit','reset','checkbox','radio','file','color','range','image'].includes(e.target.type))return;e.preventDefault();e.target.form.requestSubmit();});
$('#site-form').addEventListener('submit',e=>{e.preventDefault();const f=e.target,btn=f.querySelector('[type=submit]');action(async()=>{const d={};for(const el of f.elements){if(!el.name)continue;d[el.name]=el.type==='checkbox'?el.checked:el.type==='number'||el.name==='rev'?Number(el.value):el.value;}d.url=normalizeSiteUrl(d.url);f.elements.url.value=d.url;for(const k of ['hosting_expires','domain_expires']){if(d[k]){const iso=parseDateInput(d[k]);if(!iso)throw new Error(`Проверьте дату «${d[k]}»: используйте ДД.ММ.ГГГГ или ГГГГ-ММ-ДД`);d[k]=iso;}}if(d.hosting_provider_id==='__custom')d.hosting_provider_id='';else{d.hoster='';d.hoster_url='';}if(d.dns_provider_id==='__custom')d.dns_provider_id='';else if(d.dns_provider_id){const dp=providers.find(p=>p.id===d.dns_provider_id);d.dns_provider=dp?.name||d.dns_provider;d.dns_provider_url=dp?.website_url||d.dns_provider_url;d.dns_panel_url=dp?.panel_url||d.dns_panel_url;d.dns_panel_name=dp?.panel_name||d.dns_panel_name;}if(!d.id){delete d.id;delete d.rev;}try{await api('sites',d);$('#editor').close();editing=false;notice('Карточка сохранена. Проверки выполняются в фоне.');await refresh();}catch(err){$('#form-error').textContent=err.message;throw err;}},btn);});
$('#provider-form').addEventListener('submit',e=>{e.preventDefault();const f=e.target,btn=f.querySelector('[type=submit]');action(async()=>{const contacts=[...$('#provider-contacts').querySelectorAll('.provider-contact-row')].map(r=>({type:r.querySelector('.contact-type-input').value,label:r.querySelector('.contact-label-input').value,value:r.querySelector('.contact-value-input').value,url:r.querySelector('.contact-url-input').value})).filter(c=>c.value.trim());const d={id:f.elements.id.value||undefined,rev:f.elements.rev.value?Number(f.elements.rev.value):undefined,name:f.elements.name.value,website_url:f.elements.website_url.value,panel_url:f.elements.panel_url.value,panel_name:f.elements.panel_name.value,source_url:f.elements.source_url.value,notes:f.elements.notes.value,contacts};try{await api('providers',d);$('#provider-editor').close();providers=(await api('providers')).items;renderProviders();providerOptions($('#provider-options').value);notice('Данные провайдера сохранены.');}catch(err){$('#provider-error').textContent=err.message;throw err;}},btn);});
$('#delete-site').addEventListener('click',e=>{const f=$('#site-form'),site=rows.find(x=>x.id===f.elements.id.value);if(site)requestDelete('site',site.id,site.name,Number(f.elements.rev.value));});
$('#delete-provider').addEventListener('click',()=>{const f=$('#provider-form'),provider=providers.find(x=>x.id===f.elements.id.value);if(provider)requestDelete('provider',provider.id,provider.name,Number(f.elements.rev.value));});
$('#confirm-delete').addEventListener('click',e=>action(async()=>{const item=pendingDelete;if(!item)return;const result=await api(item.kind==='site'?'sites/delete':'providers/delete',{id:item.id,rev:item.rev});$('#delete-confirm').close();pendingDelete=null;if(item.kind==='site'){$('#editor').close();editing=false;await refresh();notice(`Сайт «${item.name}» удалён вместе с его историей.`);}else{$('#provider-editor').close();providers=(await api('providers')).items;providerOptions('');renderProviders();await refresh();notice(result.unlinked_sites?`Провайдер «${item.name}» удалён из справочника. ${result.unlinked_sites} сайтам оставлены название и ссылка.`:`Провайдер «${item.name}» удалён из справочника.`);}},e.target));
$('#vault-options').addEventListener('change',e=>{const item=catalog.vault.find(x=>x.id===e.target.value);if(item?.url&&!$('#site-form').elements.panel_url.value)$('#site-form').elements.panel_url.value=item.url;});
$('#server-options').addEventListener('change',e=>{const item=catalog.servers.find(x=>x.id===e.target.value);if(item){$('#site-form').elements.ssh_host.value=item.host;$('#site-form').elements.ssh_port.value=item.port||22;}});
$('#site-form').elements.mail_kind.addEventListener('change',e=>{$('#site-form').elements.mail_port.value={imap_tls:993,smtp_tls:465,smtp_starttls:587,smtp:25}[e.target.value];});
function table(headers,lines){return '<div class="table-wrap"><table><thead><tr>'+headers.map(x=>'<th>'+esc(x)+'</th>').join('')+'</tr></thead><tbody>'+lines.map(c=>'<tr>'+c.map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';}
async function detail(id){const s=rows.find(x=>x.id===id);if(!s)return;const days=Number($('#period').value),end=Math.floor(Date.now()/60000),start=days?end-days*1440:s.created;const [h,inc]=await Promise.all([api(`history?site=${id}&start=${start}&end=${end}`),api('incidents?site='+id)]);$('#detail-title').textContent=s.name;const span=h.end-h.start;$('#detail-body').innerHTML=`<div class="actions">${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">Открыть сайт</a>`:''}${s.panel_url?`<a href="${esc(s.panel_url)}" target="_blank" rel="noopener noreferrer">Панель</a>`:''}${s.vault_id?`<button id="reveal" data-vault="${esc(s.vault_id)}">Показать пароль из сейфа</button>`:''}</div><div id="secret"></div><div class="detail-grid"><div class="panel"><span class="muted">Наблюдаемая доступность</span><h2>${percent(h.uptime)}</h2><span>Покрытие ${percent(h.coverage)}</span></div><div class="panel">Работал: ${h.U} мин<br>Недоступен: ${h.D} мин<br>Нет данных: ${h.X} мин<br>Пауза: ${h.E} мин</div></div><div class="history-bar" aria-label="История состояний">${h.bars.map(b=>`<span class="${b.state}" style="flex:${b.end-b.start}" title="${esc(b.state+' '+when(b.start)+' — '+when(b.end))}"></span>`).join('')}</div><p class="muted small">Зелёный — доступен · красный — недоступен · серый — нет данных · песочный — пауза</p><h2>Проверки</h2>${table(['Сервис','Результат','Последняя проба','Диагностика'],s.results.filter(r=>r.probe!=='web').map(r=>[esc(probes[r.probe]||r.probe),badge(r.until_min>end?r.state:'UNKNOWN'),when(r.at),diagnosis(r)]))}<h2>Инциденты</h2>${inc.items.length?table(['Сервис','Начало','Завершение','Причина'],inc.items.map(i=>[esc(probes[i.probe]||i.probe),when(i.start),i.recovered?'Восстановлен '+when(i.recovered):i.closed?'Закрыт без восстановления '+when(i.closed):'Открыт',esc(reasons[i.reason]||i.reason)])):'<p class="muted">Подтверждённых инцидентов нет.</p>'}<h2>Инфраструктура</h2><p>Хостинг: ${esc(s.hoster||'не указан')}</p><p>DNS: ${esc(s.dns_provider||'не указан')}</p><p class="muted">${esc(s.notes||'Заметок нет')}</p><button id="export-history">Скачать историю JSON</button>`;const domainHost=s.url?new URL(s.url).hostname.toLowerCase():'';if(domainHost.endsWith('.ru')||domainHost.endsWith('.xn--p1ai'))$('#detail-body').insertAdjacentHTML('beforeend',`<section class="panel registry-info"><b>Сведения об администраторе домена .RU/.РФ</b><p>Откройте WHOIS Координационного центра. Публичные сведения могут быть ограничены; для получения более полных данных обратитесь к регистратору домена.</p><a href="https://cctld.ru/" target="_blank" rel="noopener noreferrer">Сайт Координационного центра</a> · <a href="https://cctld.ru/help/trademark/" target="_blank" rel="noopener noreferrer">Как запрашивать сведения об администраторе</a></section>`);$('#export-history').onclick=()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({site:s.name,...h},null,2)],{type:'application/json'}));a.download='site-history.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);};$('#reveal')?.addEventListener('click',e=>action(async()=>{const result=await api('reveal',{id:e.target.dataset.vault});$('#secret').textContent=result.value;$('#secret').className='secret';setTimeout(()=>{$('#secret').textContent='';$('#secret').className='';},15000);},e.target));$('#detail').showModal();}
const lastMarkup = new WeakMap();
function updateMarkup(element, markup) {
 // Preserve keyboard focus when the periodic refresh returns identical content.
 if (lastMarkup.get(element) === markup) return;
 lastMarkup.set(element, markup);
 element.innerHTML = markup;
}

function renderIncidents() {
 const summary = $('#incident-summary');
 const available = Array.isArray(incidentOverview?.active) && Array.isArray(incidentOverview?.recent);
 const count = available ? incidentOverview.active.length : 0;
 summary.hidden = count === 0;
 updateMarkup(summary, count ? `<button type="button" data-tab="incidents">Открытые инциденты · ${count}</button>` : '');
 if (!available) {
  updateMarkup($('#incident-list'), '<p class="muted">Журнал инцидентов сейчас недоступен.</p>');
  return;
 }
 const filter = $('#incident-filter').value;
 const items = incidentOverview[filter];
 const names = new Map(rows.map(site => [site.id, site.name]));
 const markup = items.map(item => {
  const name = names.get(item.site);
  const state = item.recovered ? 'Восстановлен' : item.closed ? 'Закрыт без восстановления' : 'Открыт';
  const reason = reasons[item.reason] || item.reason;
  const time = item.closed ? ` · ${state} ${when(item.recovered || item.closed)}` : '';
  return `<article class="panel incident-card">
   <div><h3>${esc(name || 'Карточка сайта недоступна')}</h3>
   <p>${esc(probes[item.probe] || item.probe)} · ${esc(reason)}</p>
   <p class="muted small">Начало ${when(item.start)}${time}</p></div>
   <div class="incident-actions">${item.closed ? '' : '<span class="badge DOWN">Открыт</span>'}
   ${name ? `<button type="button" data-detail="${esc(item.site)}">История сайта</button>` : ''}</div>
   </article>`;
 }).join('');
 updateMarkup($('#incident-list'), markup || `<p class="muted">${filter === 'active' ? 'Открытых инцидентов нет.' : 'Закрытых инцидентов пока нет.'}</p>`);
}

function siteDeadlines(site) {
 const entries = [];
 for (const [field, label] of [['domain_expires','Домен'], ['hosting_expires','Хостинг']]) {
  if (site[field]) {
   const days = site[field+'_in_days'];
   entries.push({site, label, date: site[field], days, expired: days < 0});
  }
 }
 if (site.tls_deadline) entries.push({site, label:'TLS', ...site.tls_deadline});
 return entries;
}

function renderDeadlines() {
 const entries = rows.flatMap(siteDeadlines).sort((a,b) => a.date.localeCompare(b.date) || a.site.name.localeCompare(b.site.name, 'ru'));
 const due = entries.filter(entry => entry.days <= 30);
 const summary = $('#deadline-summary');
 summary.hidden = due.length === 0;
 updateMarkup(summary, due.length ? `<button type="button" data-tab="deadlines" data-due-only>Истекли или до 30 дней · ${due.length}</button>` : '');
 const onlyDue = $('#deadline-filter').value === 'due';
 const markup = (onlyDue ? due : entries).map(entry => {
  const status = entry.expired ? (entry.days < 0 ? `Истёк ${Math.abs(entry.days)} дн. назад` : 'Истёк сегодня')
   : entry.days === 0 ? 'Истекает сегодня' : `Осталось ${entry.days} дн.`;
  const severity = entry.days <= 7 ? 'renewal-bad' : entry.days <= 30 ? 'renewal-soon' : 'renewal-good';
  const displayDate = entry.date.split('-').reverse().join('.');
  const checked = entry.checked_at == null ? '' : entry.fresh
   ? ` · проверено ${when(entry.checked_at)}` : ` · данные от ${when(entry.checked_at)}, требуется свежая проверка`;
  return `<article class="panel incident-card deadline-card">
   <div><h3>${esc(entry.site.name)}</h3><p>${entry.label} · <time datetime="${esc(entry.date)}">${esc(displayDate)}</time></p>
   <p class="muted small"><strong class="${severity}">${status}</strong>${checked}</p></div>
   <div class="incident-actions"><button type="button" data-detail="${esc(entry.site.id)}">История сайта</button></div>
   </article>`;
 }).join('');
 updateMarkup($('#deadline-list'), markup || `<p class="muted">${onlyDue ? 'Истёкших сроков и сроков на ближайшие 30 дней нет.' : 'В карточках пока нет известных сроков.'}</p>`);
}

$('#deadline-filter').addEventListener('change', renderDeadlines);
$('#deadline-summary').addEventListener('click', event => {
 if (event.target.closest('[data-due-only]')) {
  $('#deadline-filter').value='due';
  renderDeadlines();
 }
});

const jobModes = {
 scheduled: 'По расписанию', 'port-diagnostic': 'SSH/FTP при открытии раздела',
 'console-diagnostic': 'Проверка панели провайдера', diagnostic: 'Ручная диагностика',
};

function renderJobs() {
 const filter = $('#job-filter').value;
 const jobs = [...(status.jobs || [])].reverse().filter(job => {
  if (filter === 'running') return job.state === 'running';
  if (filter === 'problems') return job.state === 'failed' ||
   job.results.some(result => result.state === 'DOWN' || result.state === 'UNKNOWN');
  return true;
 });
 const markup = jobs.length ? table(['Объект','Режим','Состояние','Сервисы','Начало'], jobs.map(job => {
  const state = {running:'Выполняется', complete:'Готово', failed:'Ошибка'}[job.state] || job.state;
  const results = job.results.map(result => {
   const service = probes[result.probe] || result.probe;
   const reason = reasons[result.reason] || result.reason;
   return `<div>${esc(service)} · ${esc(labels[result.state] || result.state)}${reason ? ' · '+esc(reason) : ''}</div>`;
  }).join('');
  const current = job.state === 'running' && job.probe ? esc(probes[job.probe] || job.probe) : '';
  return [esc(job.name), esc(jobModes[job.mode] || job.mode), esc(state),
   `<div class="small job-results">${current ? `<strong>${current}</strong>` : ''}${results || (current ? '' : '—')}${job.error ? `<div class="job-error">${esc(job.error)}</div>` : ''}</div>`, when(job.started)];
 })) : '<p class="muted">По этому фильтру заданий нет.</p>';
 updateMarkup($('#jobs'), markup);
}

function renderProcesses() {
 const t = status;
 const heartbeatAge = t.heartbeat ? Date.now() / 1000 - t.heartbeat : null;
 const scheduler = t.policy_paused ? 'Остановлен сетевой политикой' : t.paused ? 'Опрос на паузе'
  : heartbeatAge == null ? 'Ожидаем первый сигнал' : heartbeatAge > 30 ? 'Нет свежего сигнала планировщика' : 'Планировщик активен';
 updateMarkup($('#process-info'), `<div class="process-grid">
  <div class="panel"><span class="muted">Отдельный процесс</span><h2>PID ${t.pid}</h2><span>${Math.floor(t.uptime_seconds/60)} мин работы · автозапуск ${t.autostart?'включён':'не установлен'}</span></div>
  <div class="panel"><span class="muted">Проверки</span><h2>${t.active} в работе</h2><span>${scheduler}</span></div>
  <div class="panel"><span class="muted">База и журнал WAL</span><h2>${(t.database_bytes/1024).toFixed(0)} КиБ</h2><span>Сигнал планировщика ${t.heartbeat?new Date(t.heartbeat*1000).toLocaleTimeString('ru-RU'):'ожидается'}</span></div>
  </div>${t.error ? '<p class="error">'+esc(t.error)+'</p>' : ''}`);
 $('#pause').textContent=t.paused?'Возобновить опрос':'Приостановить опрос';
 $('#workers').value=t.settings.workers;
 renderJobs();
}

$('#incident-filter').addEventListener('change', renderIncidents);
$('#job-filter').addEventListener('change', renderJobs);
async function deliveries(){const r=await api('deliveries');$('#deliveries').innerHTML=r.items.length?table(['Канал','Статус','Попыток','Ошибка'],r.items.map(d=>[esc(d.channel),esc({pending:'В очереди',sending:'Отправляется',sent:'Принято каналом',simulated:'Эмуляция — без отправки',uncertain:'Результат неизвестен',failed:'Ошибка',superseded:'Объединено с восстановлением'}[d.state]||d.state),d.tries,esc(d.error)])):'<p class="muted">Очередь пуста. Стабильная работа не создаёт сообщений.</p>';}
$('#notify-form').addEventListener('submit',e=>{e.preventDefault();action(async()=>{const f=e.target;await api('settings',{channels:['telegram','max','email','simulation'].filter(k=>f.elements[k].checked),mail_account_id:f.elements.mail_account_id.value,email_to:f.elements.email_to.value});notice('Настройки уведомлений сохранены');await refresh();},e.submitter);});
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.close){$('#'+b.dataset.close).close();editing=false;$('#secret')?.replaceChildren();}if(b.hasAttribute('data-contact-remove'))b.closest('.provider-contact-row')?.remove();if(b.dataset.providerEdit)action(()=>editProvider(b.dataset.providerEdit),b);if(b.dataset.service)action(()=>edit(b.dataset.site,b.dataset.service),b);if(b.dataset.edit)action(()=>edit(b.dataset.edit),b);if(b.dataset.detail)action(()=>detail(b.dataset.detail),b);if(b.dataset.tab)action(()=>tab(b.dataset.tab),b);if(b.dataset.check)action(async()=>{await api('check',{id:b.dataset.check});notice('Диагностика запущена. Результат — в фоновых процессах; статистику не изменяет.');await refresh();await tab('processes');},b);if(b.dataset.test)action(async()=>{await api('notify-test',{channel:b.dataset.test});notice('Тест добавлен в очередь');await deliveries();},b);});
document.addEventListener('click',e=>{const b=e.target.closest('[data-provider-view],[data-provider-page]');if(!b)return;if(b.dataset.providerView){providerView=b.dataset.providerView;localStorage.setItem('sites-provider-view',providerView);renderProviders();}if(b.dataset.providerPage){providerPage+=Number(b.dataset.providerPage);renderProviders();}});
document.addEventListener('click',e=>{const complaint=e.target.closest('[data-provider-complaint]');if(complaint)editComplaint(complaint.dataset.providerComplaint);const target=e.target.closest('[data-complaint-to]');if(target){$('#complaint-to').value=target.dataset.complaintTo;updateComplaintEmail();}});
for(const id of ['complaint-to','complaint-subject','complaint-body'])$('#'+id).addEventListener('input',updateComplaintEmail);
$('#complaint-copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('#complaint-body').value);notice('Текст обращения скопирован');}catch{notice('Не удалось скопировать автоматически — выделите и скопируйте текст обращения',true);}});
$('#add').onclick=()=>action(()=>edit());$('#provider-add').onclick=()=>action(()=>editProvider());$('#provider-controls').addEventListener('change',e=>{if(e.target.id==='provider-page-size'){providerPageSize=Number(e.target.value)||10;providerPage=1;renderProviders();}});$('#contact-add').onclick=()=>contactRow();$('#provider-options').onchange=e=>{syncCustomHoster();const p=providers.find(x=>x.id===e.target.value);if(p){$('#site-form').elements.panel_url.value=p.panel_url||'';$('#site-form').elements.panel_name.value=p.panel_name||'';$('#custom-hoster').value='';$('#custom-hoster-url').value='';}};$('#dns-provider-options').onchange=()=>{syncCustomDnsProvider();const p=providers.find(x=>x.id===$('#dns-provider-options').value);if(p){$('#custom-dns-provider').value='';$('#site-form').elements.dns_provider_url.value=p.website_url||'';$('#site-form').elements.dns_panel_url.value=p.panel_url||'';$('#site-form').elements.dns_panel_name.value=p.panel_name||'';}};$('#refresh').onclick=e=>action(refresh,e.target);$('#search').oninput=render;$('#availability-filter').value=availabilityFilter;$('#availability-filter').onchange=e=>{availabilityFilter=e.target.value;render();};$('#theme-toggle').onclick=()=>setTheme(document.body.classList.contains('theme-dark')?'light':'dark');$('#period').onchange=()=>action(refresh);$('#pause').onclick=e=>action(async()=>{await api('settings',{paused:!status.paused});await refresh();},e.target);$('#workers').onchange=e=>action(async()=>{await api('settings',{workers:Number(e.target.value)});await refresh();});
$('#editor').addEventListener('close',()=>editing=false);$('#detail').addEventListener('close',()=>$('#secret')?.replaceChildren());
ensureTooltips();new MutationObserver(()=>ensureTooltips()).observe(document.body,{childList:true,subtree:true});
action(refresh);setInterval(()=>{if(!document.hidden&&!editing)refresh().catch(e=>{$('#connection').textContent='Нет связи с процессом';notice(e.message,true);});},10000);
})();
