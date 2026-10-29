/* Студия → «Звук и поведение» → «Закинуть идею одной клавишей»: включить, сменить сочетание, попробовать. Всё — через
   окно программы (Api.capture_status / capture_set / show_capture в ui/app.py): сочетание назначает интерфейс — на
   Windows RegisterHotKey, в GNOME своё сочетание клавиатуры (ui/hotkey.py). В браузере (студия без окна) — только подсказка. */
function capRender(st) {
  if (!st) { $("#cap-st").textContent = "Сочетание назначает окно программы — откройте студию из Dewiar, не из браузера."; return; }
  $("#cap-on").checked = !!st.on; $("#cap-key").value = st.key || "Ctrl+Alt+N";
  const el = $("#cap-st");
  el.className = "hint " + (st.error ? "bad" : st.works ? "ok" : "");
  el.textContent = st.error ? st.error : !st.on ? "Выключено." : st.works ? `${st.key} — ${st.how}` : (st.how || "Сочетание пока не работает.");
  $("#cap-cmdh").hidden = !st.on || st.works || !st.command; $("#cap-cmd").textContent = st.command || "";
}
async function capLoad() {
  const a = window.api && window.api();
  try { capRender(a && a.capture_status ? await a.capture_status() : null); } catch (e) { capRender(null); }
}
async function capSave() {
  const a = window.api && window.api(); if (!a || !a.capture_set) return capRender(null);
  $("#cap-st").className = "hint"; $("#cap-st").textContent = "…";
  try { capRender(await a.capture_set($("#cap-on").checked, $("#cap-key").value.trim())); } catch (e) { $("#cap-st").textContent = e.message; }
}
$("#cap-on").onchange = capSave;
$("#cap-key").onchange = capSave;
$("#cap-key").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); capSave(); } };
$("#cap-try").onclick = () => { const a = window.api && window.api(); if (a && a.show_capture) a.show_capture(); else toast("Окно открывается из программы Dewiar"); };
{ const b = $('nav button[data-p="sound"]'); if (b) b.addEventListener("click", capLoad); }
window.addEventListener("pywebviewready", () => { if ($("#p-sound").classList.contains("cur")) capLoad(); });
