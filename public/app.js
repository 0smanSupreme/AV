(() => {
  const root = document.documentElement;
  const savedTheme = localStorage.getItem("admin-vote-theme");
  if (savedTheme === "light" && !document.body.classList.contains("auth-page")) root.classList.remove("dark");

  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      root.classList.toggle("dark");
      localStorage.setItem("admin-vote-theme", root.classList.contains("dark") ? "dark" : "light");
    });
  });

  const nav = document.querySelector("[data-mobile-nav]");
  document.querySelector("[data-mobile-toggle]")?.addEventListener("click", () => nav?.classList.toggle("open"));
  document.querySelectorAll(".toast button").forEach((button) => button.addEventListener("click", () => button.parentElement.remove()));
  window.setTimeout(() => document.querySelectorAll(".toast").forEach((toast) => toast.remove()), 5500);

  document.querySelectorAll("[data-tabs]").forEach((tabs) => {
    const buttons = [...tabs.querySelectorAll("[data-tab]")];
    const panels = [...tabs.querySelectorAll("[data-panel]")];
    const activate = (name) => {
      buttons.forEach((button) => button.classList.toggle("active", button.dataset.tab === name));
      panels.forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === name));
      if (history.replaceState) history.replaceState(null, "", `#${name}`);
    };
    buttons.forEach((button) => button.addEventListener("click", () => activate(button.dataset.tab)));
    const requested = location.hash.slice(1);
    if (buttons.some((button) => button.dataset.tab === requested)) activate(requested);
  });

  document.querySelectorAll("[data-dialog-open]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.dialogOpen)?.showModal());
  });
  document.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
  });
  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
})();
