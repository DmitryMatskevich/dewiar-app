// Exercise the real HUD handlers with a small DOM/bridge double.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const src = fs.readFileSync(require('path').join(__dirname, '../ui/hud.html'), 'utf8');
const elements = new Map();
function $(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', title: '', innerHTML: '', disabled: false, attributes: {}, style: {},
    firstElementChild: { style: {} }, classList: { toggle() {} },
    setAttribute(k, v) { this.attributes[k] = v; }, focus() {},
  });
  return elements.get(id);
}
const calls = [], sent = [], handlers = {};
const ctx = vm.createContext({ $, console, dictating: false, audioMode: 'voice', atts: [],
  core: {
    connected: true, on: (event, fn) => { handlers[event] = fn; },
    call: async (...args) => { calls.push(args); },
    sendLater: async (...args) => { sent.push(args); },
  },
  fitAsk() {}, openAsk() {}, showErr(e) { throw e; },
  guard: (el, fn) => fn, tlReset() {}, showCap() {}, renderAtts() {}, WAIT: 'wait',
});
vm.runInContext(src.slice(src.indexOf('function dictationUi('), src.indexOf('async function showMicMenu(')), ctx);
vm.runInContext(src.slice(src.indexOf('function sendAsk()'), src.indexOf('/* Поле растёт')), ctx);
(async () => {
  $('askin').value = 'Мой черновик.';
  await $('a-mic').onclick();
  assert.equal(ctx.dictating, true);
  assert.match($('a-mic').innerHTML, /fill="currentColor"/);
  assert.equal($('a-mic').attributes['aria-pressed'], 'true');
  handlers.dictation({ state: 'partial', text: 'Первая часть.' });
  handlers.dictation({ state: 'partial', text: 'Вторая часть.' });
  assert.equal(ctx.dictating, true);
  assert.equal(sent.length, 0);
  assert.equal($('askin').value, 'Мой черновик. Первая часть. Вторая часть.');
  await $('a-mic').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['dictation_finish', { submit: false }]);
  handlers.dictation({ state: 'done', submit: false });
  assert.equal(ctx.dictating, false);
  assert.equal(sent.length, 0);
  assert.match($('a-mic').innerHTML, /M5 11/);
  // Send while recording waits for final STT; draft and attachments are preserved.
  handlers.dictation({ state: 'recording' });
  ctx.atts = [{ kind: 'image', name: 'Фото', url: 'image:test' }];
  vm.runInContext('sendAsk()', ctx);
  assert.equal(sent.length, 0);
  handlers.dictation({ state: 'finishing' });
  assert.equal($('a-mic').disabled, true);
  handlers.dictation({ state: 'partial', text: 'Последние слова.' });
  handlers.dictation({ state: 'done', submit: true });
  assert.equal(sent.length, 1);
  assert.equal(sent[0][1].text, 'Мой черновик. Первая часть. Вторая часть. Последние слова.');
  assert.equal(sent[0][1].images[0], 'image:test');
  assert.equal($('askin').value, '');
  assert.equal($('a-mic').disabled, false);
  assert.equal(ctx.dictating, false);
  console.log('OK: live HUD stop icon, partials, stop keeps draft, send waits for final text and preserves attachments');
  console.log('OK');
})().catch(e => { console.error(e); process.exitCode = 1; });

// The shared shell runner recognises an exact OK line.
