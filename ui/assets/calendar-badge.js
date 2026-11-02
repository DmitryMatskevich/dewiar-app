/* Shared calendar counter: local dates, exclusive categories, no duplicate category counts. */
window.CalendarBadge = (() => {
  const labels = {birthday:"Дни рождения",event:"Встречи",reminder:"Напоминания",task:"Задачи",money:"Деньги",note:"Заметки",diary:"Дневник"};
  const dateKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  function config(ui = {}) {
    return {types:Array.isArray(ui.calendar_badge_types) ? ui.calendar_badge_types.filter(k => Object.hasOwn(labels,k)) : ['birthday','event'],
      days:[1,7,30].includes(ui.calendar_badge_days) ? ui.calendar_badge_days : 1};
  }
  function category(item) {
    if (['event','reminder'].includes(item.type) && (item.kind === 'birthday' || /д(?:ень|ня|н[её]м|ню|ни)\s+рождени[яюе]/i.test(item.title || ''))) return 'birthday';
    return item.type;
  }
  function count(months, prefs, now = new Date()) {
    const start = dateKey(now), end = new Date(now.getFullYear(),now.getMonth(),now.getDate()+prefs.days-1);
    const last = dateKey(end), days = Object.assign({},...months.map(m => m.days || {}));
    return Object.entries(days).reduce((sum,[day,items]) => sum + (day >= start && day <= last ? items.filter(i => !i.done && !i.paused && prefs.types.includes(category(i))).length : 0),0);
  }
  async function load(core, prefs, now = new Date()) {
    if (!prefs.types.length) return 0;
    const end = new Date(now.getFullYear(),now.getMonth(),now.getDate()+prefs.days-1), months = [];
    for(let y=now.getFullYear(),m=now.getMonth(); y<end.getFullYear() || y===end.getFullYear() && m<=end.getMonth(); m++) {
      if(m===12){m=0;y++;} months.push({year:y,month:m+1});
    }
    return count(await Promise.all(months.map(args=>core.call('calendar_month',args))),prefs,now);
  }
  return {labels,config,category,count,load};
})();
