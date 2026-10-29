/* «Что я умею» (18.10: «у нас есть функции, про которые никто не знает») — data/tricks.json, тот же список, что на
   телефоне («Ещё» → «Что я умею»). Здесь — то, что работает на компьютере: ▶ — фраза уходит помощнику (как сказать
   голосом); фраза с «…» — в «Чат», дописать; сочетания клавиш — просто подсказка. */
let TRICKS = null;
async function loadTricks() {
  const box = $("#tr-box"); if (!box) return;
  try { TRICKS = TRICKS || await core.call("tricks"); } catch (e) { box.textContent = e.message; return; }
  box.innerHTML = (TRICKS.groups || []).map((g, gi) => {
    const it = g.items.filter(x => (x.where || []).includes("pc"));
    return it.length ? `<div class="trg"><h3><span>${escH(g.icon || "")}</span>${escH(g.name)}</h3>${it.map(x =>
      `<div class="trk${x.key ? " key" : ""}"><div><b>${x.key ? escH(x.say) : "«" + escH(x.say) + "»"}</b><span>${escH(x.what)}</span></div>${x.key || x.noplay ? "" :
        `<button class="tplay" data-say="${escH(x.say)}"${x.fill ? ' data-fill="1"' : ""} title="${x.fill ? "В «Чат» — дописать" : "Отправить помощнику"}">${SVG(x.fill ? '<path d="M4 20h4L19 9l-4-4L4 16z"/>' : '<path d="M8 5.5v13l10.5-6.5z"/>')}</button>`}</div>`).join("")}</div>` : "";
  }).join("");
  $$("#tr-box .tplay").forEach(b => b.onclick = async () => {
    const say = b.dataset.say;
    if (b.dataset.fill) { $('nav button[data-p="chat"]').click(); const t = $("#ch-text"); t.value = say.replace(/…$/, " ").replace(/:\s*…?$/, ": "); t.focus(); return; }
    b.disabled = true;
    try { await core.call("say", { text: say }); toast("Отправил помощнику — смотрите виджет"); } catch (e) { toast(e.message); }
    setTimeout(() => b.disabled = false, 1500);
  });
}
