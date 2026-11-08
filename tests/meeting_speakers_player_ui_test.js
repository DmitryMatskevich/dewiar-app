// Проигрыватель встречи (ui/studio/links.js): подписи участников, панель «Участники», правки, разбиение, аудио.
// Запуск: jsdom во временной папке (npm i jsdom@24), затем NODE_PATH=<папка>/node_modules node tests/<файл> <корень app>.
const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const src = fs.readFileSync(path.join(process.argv[2], "ui/studio/links.js"), "utf8");
const a = src.indexOf("async function prPlayer("), b = src.indexOf("// Итог и видео создаются в фоне после остановки.");
const code = src.slice(a, b);
const dom = new JSDOM("<!doctype html><body></body>", { runScripts: "outside-only", pretendToBeVisual: true });
const w = dom.window;
w.HTMLMediaElement.prototype.play = () => Promise.resolve(); w.HTMLMediaElement.prototype.pause = () => {};
w.Element.prototype.scrollIntoView = () => {};
const calls = []; let view = null, toasts = [];
const PLAYER = { tracks: [{ key: "mix", label: "Общая", url: "/m/a.wav", seconds: 10 }], video: "", frames: [], duration: 10,
  speakers: [{ id: "ME", label: "Вы" }, { id: "R1", label: "Участник 1" }, { id: "R2", label: "Участник 2" }],
  lines: [{ who: "me", text: "Добрый день", t: 1, speaker: "ME", source_speaker: "ME", label: "Вы" }, { who: "them", text: "Сколько стоит?", t: 3, speaker: "R1", source_speaker: "R1", label: "Участник 1" }] };
w.core = { call: async (cmd, args) => { calls.push([cmd, args]);
  if (cmd === "prompter_meeting_player") return PLAYER;
  if (cmd === "prompter_meeting_speakers") return view;
  if (cmd === "prompter_meeting_speakers_audio") return { files: [{ id: "ME" }, { id: "R1" }] };
  return { started: true }; } };
w.toast = (t) => toasts.push(t);
w.eval(`const escH = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const toast = (...x) => window.toast(...x); const core = window.core; const $ = (s, r = document) => r.querySelector(s);
  ${code}; window.prPlayer = prPlayer;`);
const $ = (s) => w.document.querySelector(s), $$ = (s) => [...w.document.querySelectorAll(s)];
const ok = (c, m) => { console.log(c ? "✅" : "❌", m); if (!c) process.exitCode = 1; };
const tick = (ms = 30) => new Promise(r => setTimeout(r, ms));
(async () => {
  view = { speakers: [{ id: "ME", label: "Вы", display: "Вы", speech_sec: 120 }, { id: "R1", label: "Участник 1", display: "Участник 1", speech_sec: 300 },
                      { id: "R2", label: "Участник 2", display: "Участник 2", speech_sec: 30 }], can_diarize: true, not_transferred: [], job: null };
  await w.prPlayer({ path: "Встречи/A.md", frames: [] }, "A", null, "frames"); await tick();
  ok($$(".pr-pl-lines li b").map(x => x.textContent).join("|") === "Вы|Участник 1", "в расшифровке — подписи участников");
  ok($$(".pr-spk").length === 3 && /Пересчитать/.test($(".pr-spk-run").textContent) && $(".pr-spk-audio"), "панель: участники, «Пересчитать», «Аудио участников»");
  // переименование
  const b = $$(".pr-spk")[1].querySelector("b"); b.click(); await tick(5);
  const inp = $(".pr-spk-in"); inp.value = "Иван"; inp.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter" })); await tick();
  ok(calls.some(c => c[0] === "prompter_meeting_speakers_edit" && c[1].action === "rename" && c[1].id === "R1" && c[1].name === "Иван"), "переименование уходит в ядро");
  // объединение
  const sel = $$(".pr-spk")[2].querySelector("select"); sel.value = "R1"; sel.dispatchEvent(new w.Event("change")); await tick();
  ok(calls.some(c => c[0] === "prompter_meeting_speakers_edit" && c[1].action === "merge" && c[1].id === "R2" && c[1].to === "R1"), "объединение участников");
  // переназначение реплики
  const li = $$(".pr-pl-lines li[data-t]")[1]; li.dispatchEvent(new w.MouseEvent("contextmenu", { bubbles: true, clientX: 5, clientY: 5 })); await tick(5);
  const mb = $$(".pr-spk-menu button"); ok(mb.length === 3, "меню «Сказал(а)»"); mb[0].click(); await tick();
  ok(calls.some(c => c[1] && c[1].action === "reassign" && c[1].t === 3 && c[1].source === "R1" && c[1].to === "ME"), "переназначение реплики с исходным говорящим");
  // аудио участников
  $(".pr-spk-audio").click(); await tick();
  ok(calls.some(c => c[0] === "prompter_meeting_speakers_audio") && toasts.some(t => /Аудио участников: 2/.test(t)), "аудио участников");
  // встреча без разбиения: кнопка «Разбить», опрос состояния до готовности
  w.document.body.innerHTML = ""; calls.length = 0;
  view = { speakers: [], can_diarize: true, not_transferred: [], job: null };
  await w.prPlayer({ path: "Встречи/B.md", frames: [] }, "B", null, "frames"); await tick();
  ok(/Разбить по участникам/.test($(".pr-spk-run").textContent) && /не выполнялось/.test($(".pr-spk-none").textContent), "«Разбить по участникам»");
  $(".pr-spk-n").value = "2";
  view = { speakers: [], can_diarize: true, not_transferred: [], job: { busy: true, stage: "diarize", progress: 0.4 } };
  $(".pr-spk-run").click(); await tick(1700);
  ok(calls.some(c => c[0] === "prompter_meeting_speakers_diarize" && c[1].expected_speakers === 2), "запуск разбиения с числом участников");
  ok(/40%/.test($(".pr-spk-run").textContent), "прогресс: " + $(".pr-spk-run").textContent);
  view = { speakers: [], can_diarize: true, not_transferred: [], job: { busy: false, error: "запись слишком короткая" } };
  await tick(1700);
  ok(toasts.some(t => /короткая/.test(t)) && $(".pr-spk-err") && /короткая/.test($(".pr-spk-err").textContent), "ошибка разбиения видна");
  // без аудио
  w.document.body.innerHTML = "";
  view = { speakers: [], can_diarize: false, not_transferred: [], job: null };
  await w.prPlayer({ path: "Встречи/C.md", frames: [] }, "C", null, "frames"); await tick();
  ok(!$(".pr-spk-run") && /Нет аудиозаписи/.test($(".pr-spk-none").textContent), "нет записи — нет кнопки");
  console.log(process.exitCode ? "PLAYER FAIL" : "PLAYER OK"); process.exit(process.exitCode || 0);
})();
