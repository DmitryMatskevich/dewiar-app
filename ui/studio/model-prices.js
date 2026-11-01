/* Price comparison never changes profiles or performs inference. */
(() => {
  let data = null, busy = false;
  const panel = $('#model-prices-panel');
  const money = value => value == null ? '—' : '$' + Number(value).toLocaleString('en-US', {maximumFractionDigits:8});
  function link(label, href) {
    const a = document.createElement('a');
    try {
      const u = new URL(href);
      if (u.protocol !== 'https:' || !['openrouter.ai','developers.openai.com','platform.claude.com','api-docs.deepseek.com'].includes(u.hostname)) return document.createTextNode(label);
      a.href = u.href;
    } catch (_) { return document.createTextNode(label); }
    a.textContent = label; a.target = '_blank'; a.rel = 'noopener'; return a;
  }
  function render() {
    const box = $('#model-prices-rows'); box.replaceChildren();
    if (!data) return;
    const q = $('#model-prices-search').value.trim().toLowerCase(), provider = $('#model-prices-provider').value;
    const main = /gpt-6(?:\.1)?-(?:luna|terra|sol|astra)$|claude-(?:haiku|sonnet|opus)-5\.5$|deepseek-(?:v4\.1-flash(?:-1017)?|v4-pro(?:-0813)?)$|ling-3\.1-flash$/;
    const models = data.models.filter(m => (!provider || m.provider === provider) && (!q || `${m.name} ${m.id} ${m.provider}`.toLowerCase().includes(q)) && ($('#model-prices-scope').value === 'all' || q || main.test(m.id)));
    for (const m of models) {
      const tr = document.createElement('tr'), name = document.createElement('td'); name.append(link(m.name,m.url));
      const id = document.createElement('small'); id.textContent = m.id; name.append(id);
      const detail = document.createElement('small'); detail.textContent = [m.context ? `Контекст: ${m.context.toLocaleString('ru-RU')}` : '', m.note].filter(Boolean).join(' · '); name.append(detail); tr.append(name);
      for (const value of [m.input,m.output,m.cache,m.cost]) { const td = document.createElement('td'); td.textContent = money(value); tr.append(td); }
      box.append(tr);
    }
    if (!models.length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 5; td.textContent = 'Модели не найдены. Измените поиск или выберите «Все модели каталога».'; tr.append(td); box.append(tr); }
    $('#model-prices-status').textContent = `${data.source}. Проверено: ${new Date(data.checked_at).toLocaleString('ru-RU')}. Расчёт: ${new Date(data.calculated_at).toLocaleString('ru-RU')}. Показано: ${models.length}.`;
    $('#model-prices-warning').textContent = data.warning || '';
    const sources = $('#model-prices-sources'); sources.replaceChildren();
    for (const s of data.sources) sources.append(link(s.name,s.url));
  }
  async function load(refresh) {
    if (busy) return;
    const input = $('#model-prices-input'), output = $('#model-prices-output');
    if (!input.reportValidity() || !output.reportValidity()) return;
    busy = true;
    const buttons = ['model-prices-open','model-prices-refresh','model-prices-calculate'].map(id=>$('#'+id)); buttons.forEach(b=>b.disabled=true);
    $('#model-prices-warning').textContent = refresh ? 'Проверяю тарифы в интернете…' : 'Рассчитываю…';
    try {
      data = await core.call('model_prices',{refresh,input_tokens:Number(input.value),output_tokens:Number(output.value)}); render();
    } catch (e) { $('#model-prices-warning').textContent = e.message; }
    finally { busy = false; buttons.forEach(b=>b.disabled=false); }
  }
  $('#model-prices-open').onclick = () => {
    panel.hidden = !panel.hidden;
    $('#model-prices-open').setAttribute('aria-expanded', String(!panel.hidden));
    if (!panel.hidden) load(true);
  };
  $('#model-prices-refresh').onclick = () => load(true);
  $('#model-prices-calculate').onclick = () => load(false);
  for (const id of ['model-prices-search','model-prices-provider','model-prices-scope']) $('#'+id).oninput = render;
})();
