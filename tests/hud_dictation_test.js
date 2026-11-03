// Exercise the real HUD handlers with a small DOM/bridge double.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const src = fs.readFileSync(require('path').join(__dirname, '../ui/hud.html'), 'utf8');
const elements = new Map();
function $(id) {
  if (!elements.has(id)) {
    const element = {
    value: '', title: '', innerHTML: '', disabled: false, dataset: {}, attributes: {}, style: {},
    firstElementChild: { style: {} }, classList: { toggle() {} },
    setAttribute(k, v) { this.attributes[k] = v; }, focus() {}, addEventListener() {},
    setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
    setRangeText(text, start, end) {
      this.value = this.value.slice(0, start) + text + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + text.length;
    },
    };
    let value = '';
    Object.defineProperty(element, 'value', { get: () => value, set: v => {
      value = v; element.selectionStart = element.selectionEnd = v.length;
    } });
    element.value = '';
    elements.set(id, element);
  }
  return elements.get(id);
}
const calls = [], sent = [], handlers = {};
const ctx = vm.createContext({ $, console, audioMode: 'voice', atts: [],
  core: {
    connected: true, on: (event, fn) => { handlers[event] = fn; },
    call: async (...args) => { calls.push(args); },
    sendLater: async (...args) => { sent.push(args); },
  },
  fitAsk() {}, updateDraftActions() {}, openAsk() {}, showErr(e) { throw e; },
  guard: (el, fn) => fn, tlReset() {}, showCap() {}, renderAtts() {}, WAIT: 'wait',
});
vm.runInContext(src.slice(src.indexOf('let dictating ='), src.indexOf('let turnUntil =')), ctx);
const recording = () => vm.runInContext('dictating', ctx);
vm.runInContext(src.slice(src.indexOf('let dictationSession ='), src.indexOf('async function showMicMenu(')), ctx);
vm.runInContext(src.slice(src.indexOf('function sendAsk()'), src.indexOf('/* Поле растёт')), ctx);
(async () => {
  $('askin').value = 'Мой черновик.';
  await $('a-mic').onclick();
  assert.equal(recording(), true);
  assert.equal($('dictation-ring').attributes.r, 0);
  assert.equal($('a-mic').attributes['aria-pressed'], 'true');
  handlers.dictation({ state: 'partial', text: 'Первая часть.' });
  handlers.dictation({ state: 'partial', text: 'Вторая часть.' });
  assert.equal(recording(), true);
  assert.equal(sent.length, 0);
  assert.equal($('askin').value, 'Мой черновик. Первая часть. Вторая часть.');
  await $('a-mic').onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1))), ['dictation_cancel']);
  assert.equal(recording(), false);
  assert.equal(sent.length, 0);
  // Send while recording waits for final STT; draft and attachments are preserved.
  handlers.dictation({ state: 'recording' });
  ctx.atts = [{ kind: 'image', name: 'Фото', url: 'image:test' }];
  vm.runInContext('sendAsk()', ctx);
  assert.equal(sent.length, 0);
  handlers.dictation({ state: 'finishing' });
  assert.equal($('a-mic').disabled, false);
  handlers.dictation({ state: 'partial', text: 'Последние слова.' });
  handlers.dictation({ state: 'done', submit: true });
  assert.equal(sent.length, 1);
  assert.equal(sent[0][1].text, 'Мой черновик. Первая часть. Вторая часть. Последние слова.');
  assert.equal(sent[0][1].images[0], 'image:test');
  assert.equal($('askin').value, '');
  assert.equal($('a-mic').disabled, false);
  assert.equal(recording(), false);
  console.log('OK: live HUD stop icon, partials, stop keeps draft, send waits for final text and preserves attachments');
  console.log('OK');
})().catch(e => { console.error(e); process.exitCode = 1; });

// The shared shell runner recognises an exact OK line.
