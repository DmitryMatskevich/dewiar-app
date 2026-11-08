// Проверка окна встречи (ui/prompter.html) в jsdom: режим разметки, число участников, запись очной встречи.
// Запуск: jsdom во временной папке (npm i jsdom@24), затем NODE_PATH=<папка>/node_modules node tests/<файл> <корень app>.
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const APP = process.argv[2];
let html = fs.readFileSync(path.join(APP, "ui/prompter.html"), "utf8");
html = html.replace('<script src="assets/dewiar.js"></script>', "<script>/*core stub*/</script>");
const calls = [], handlers = {};
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, beforeParse(w) {
  w.core = { call: async (cmd, a) => { calls.push([cmd, a]); if (cmd === "prompter_state") return { meeting: {}, voice: { has: true }, hints: [], lines: [] };
                                     if (cmd === "prompter_start") return { meeting: { id: "1", started: Date.now() / 1000 }, voice: { has: true }, hints: [], lines: [] }; return {}; },
             on: (t, f) => { (handlers[t] = handlers[t] || []).push(f); }, snapshot: {} };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  w.HTMLMediaElement.prototype.play = () => Promise.resolve(); w.HTMLMediaElement.prototype.pause = () => {};
  w.scrollTo = () => {}; w.Element.prototype.scrollIntoView = () => {};
}});
const w = dom.window, $ = (s) => w.document.querySelector(s);
const fail = (m) => { console.log("❌", m); process.exitCode = 1; }, ok = (c, m) => c ? console.log("✅", m) : fail(m);
(async () => {
  await new Promise(r => setTimeout(r, 50));
  (handlers.hello || []).forEach(f => f({ settings: { prompter: { speaker_mode: "participants", expected_speakers: "3" } } }));
  await new Promise(r => setTimeout(r, 20));
  ok($("#m-speaker-mode").value === "participants", "режим из настроек");
  ok($("#m-expected").value === "3" && !$("#m-expected-wrap").classList.contains("hidden"), "число участников показано");
  ok($("#m-record").checked, "«По участникам» включает запись звука");
  ok($("#m-consent").checked && !$("#start").disabled, "согласие отмечено по умолчанию (как было)");
  $("#m-consent").checked = false; $("#m-consent").dispatchEvent(new w.Event("change"));
  ok($("#start").disabled, "без согласия запись не начинается");
  $("#m-consent").checked = true; $("#m-consent").dispatchEvent(new w.Event("change"));
  ok(!$("#start").disabled, "согласие — можно начинать");
  $("#start").click(); await new Promise(r => setTimeout(r, 20));
  const st = calls.find(c => c[0] === "prompter_start");
  ok(st && st[1].speaker_mode === "participants" && st[1].expected_speakers === 3 && st[1].record === true && st[1].consent === true,
     "в ядро уходят режим, число участников, запись: " + JSON.stringify(st && { m: st[1].speaker_mode, n: st[1].expected_speakers, r: st[1].record }));
  // очная встреча
  w.document.querySelector('#m-place button[data-v="live"]').click();
  ok(!$("#m-live-record-wrap").classList.contains("hidden") && $("#o-online").classList.contains("hidden"), "очная: свой переключатель записи");
  ok($("#m-live-record").checked && !$("#m-live-consent-wrap").classList.contains("hidden"), "очная «По участникам»: запись включена, согласие видно");
  $("#m-live-consent").checked = false; $("#m-live-consent").dispatchEvent(new w.Event("change"));
  ok($("#start").disabled, "очная: без согласия не начинается");
  $("#m-live-consent").checked = true; $("#m-live-consent").dispatchEvent(new w.Event("change"));
  ok(!$("#start").disabled, "очная: согласие дано");
  calls.length = 0; $("#start").click(); await new Promise(r => setTimeout(r, 20));
  const st2 = calls.find(c => c[0] === "prompter_start");
  ok(st2 && st2[1].place === "live" && st2[1].record === true && st2[1].consent === true && st2[1].deferred === true, "очная запись уходит в ядро");
  $("#m-live-record").checked = false; $("#m-live-record").dispatchEvent(new w.Event("change"));
  ok($("#m-speaker-mode").value === "owner", "без записи режим возвращается к «Вы и собеседники»");
  $("#m-speaker-mode").value = "none"; $("#m-speaker-mode").dispatchEvent(new w.Event("change"));
  ok($("#m-expected-wrap").classList.contains("hidden") && /Речь/.test($("#m-speaker-hint").textContent), "«Без разметки»: подсказка, без числа");
  console.log(process.exitCode ? "UI FAIL" : "UI OK");
})();
