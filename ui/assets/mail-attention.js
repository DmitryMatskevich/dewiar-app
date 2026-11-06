(() => {
  const bar = document.getElementById("assistant-topbar");
  if (!bar) return;
  const label = document.createElement("span");
  label.id = "mail-scope-status";
  label.hidden = true;
  label.setAttribute("role", "status");
  label.style.cssText = "flex:0 1 auto;max-width:38%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:10px;font-weight:650;color:var(--accent);";
  bar.prepend(label);

  async function refresh() {
    try {
      const state = await core.call("mail_scope_state");
      const selected = state.selected;
      label.hidden = !state.active;
      label.textContent = state.active ? "Почта" : "";
      const title = selected ? `Почта · ${selected.subject || "без темы"} · ${selected.from || ""}` : "Режим работы с почтой";
      label.title = state.active ? title : "";
      label.setAttribute("aria-label", state.active ? title : "Режим работы с почтой выключен");
    } catch (_) { label.hidden = true; }
  }
  core.on("hello", refresh).on("offline", () => { label.hidden = true; });
  setInterval(() => { if (!document.hidden) refresh(); }, 2500);
  refresh();
})();
