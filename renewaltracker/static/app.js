/* RenewalTracker single-page front end (no build step, no dependencies). */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    user: null,
    categories: [],
    recurrences: [],
    items: [],
    currentImport: null,
    view: "dashboard",
  };

  const CATEGORY_ICON = { bill: "receipt", subscription: "repeat", insurance: "shield", passport: "passport", other: "pin" };
  const CURRENCY = { GBP: "£", USD: "$", EUR: "€" };
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // ------------------------------------------------------------------ utils
  function icon(name, cls = "") {
    return `<svg class="i ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  }

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", headers: {}, ...options };
    if (opts.body && !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (res.status === 401 && state.user) {
      state.user = null;
      showAuth();
    }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Request failed (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function toast(message, isError = false) {
    const el = $("#toast");
    el.innerHTML = `${icon(isError ? "alert" : "check-circle")}<span>${esc(message)}</span>`;
    el.classList.toggle("error", isError);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 4000);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function money(amount, currency) {
    if (amount === null || amount === undefined || amount === "") return "";
    const sym = CURRENCY[currency] || "";
    const num = Number(amount).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return sym ? `${sym}${num}` : `${num} ${currency || ""}`.trim();
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function dateChip(iso, catClass) {
    const d = new Date(iso + "T00:00:00");
    const showYear = d.getFullYear() !== new Date().getFullYear();
    return `<div class="date-chip ${catClass}" title="${esc(fmtDate(iso))}"><span class="m">${MONTHS[d.getMonth()]}</span><span class="d">${d.getDate()}</span>${showYear ? `<span class="y">${d.getFullYear()}</span>` : ""}</div>`;
  }

  function whenText(days) {
    if (days < 0) return `${-days} day${days === -1 ? "" : "s"} overdue`;
    if (days === 0) return "Due today";
    if (days === 1) return "Tomorrow";
    if (days < 60) return `in ${days} days`;
    if (days < 365) {
      const months = Math.round(days / 30);
      return `in ~${months} month${months === 1 ? "" : "s"}`;
    }
    const years = Math.floor(days / 365);
    const remMonths = Math.round((days - years * 365) / 30);
    return `in ${years} yr${years === 1 ? "" : "s"}${remMonths ? ` ${remMonths} mo` : ""}`;
  }

  function whenClass(status) {
    if (status === "overdue" || status === "due_today") return "critical";
    if (status === "upcoming") return "warning";
    return "";
  }

  function statusLabel(status) {
    return { overdue: "Overdue", due_today: "Due today", upcoming: "Due soon", ok: "On track", archived: "Archived" }[status] || status;
  }

  function statusIcon(status) {
    return { overdue: "alert", due_today: "alert", upcoming: "clock", ok: "check", archived: "archive" }[status] || "check";
  }

  function categoryLabel(key) {
    const c = state.categories.find((x) => x.key === key);
    return c ? c.label : key;
  }

  function formToObject(form) {
    const data = {};
    new FormData(form).forEach((v, k) => { data[k] = v; });
    $$("input[type=checkbox]", form).forEach((cb) => { data[cb.name] = cb.checked; });
    return data;
  }

  function emptyState(iconName, title, text, cta) {
    return `<div class="empty">${icon(iconName)}<strong>${esc(title)}</strong><p>${esc(text)}</p>${cta || ""}</div>`;
  }

  // ------------------------------------------------------------------ theme
  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    const dark = theme === "dark" || (!theme && window.matchMedia("(prefers-color-scheme: dark)").matches);
    $$("#theme-toggle use, #theme-toggle-m use").forEach((u) => u.setAttribute("href", dark ? "#i-sun" : "#i-moon"));
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme");
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = current === "dark" || (!current && systemDark);
    const next = isDark ? "light" : "dark";
    try { localStorage.setItem("rt-theme", next); } catch (_) { /* ignore */ }
    applyTheme(next);
  }

  try { applyTheme(localStorage.getItem("rt-theme")); } catch (_) { applyTheme(null); }
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#theme-toggle-m").addEventListener("click", toggleTheme);

  // ------------------------------------------------------------------ views
  function showView(name) {
    state.view = name;
    $$("#app .view").forEach((v) => v.classList.add("hidden"));
    $(`#view-${name}`).classList.remove("hidden");
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    closeMenus();
    window.scrollTo({ top: 0 });
    const loaders = { dashboard: loadDashboard, items: loadItems, alerts: loadAlerts, import: loadImports, settings: loadSettings };
    if (loaders[name]) loaders[name]().catch((err) => toast(err.message, true));
  }

  function showAuth() {
    $("#shell").classList.add("hidden");
    $("#view-auth").classList.remove("hidden");
  }

  function showApp() {
    $("#view-auth").classList.add("hidden");
    $("#shell").classList.remove("hidden");
    $("#user-name").textContent = state.user.username;
    $("#user-avatar").textContent = (state.user.username || "?").charAt(0).toUpperCase();
    showView("dashboard");
    refreshBadge();
    initPush();
  }

  async function refreshBadge() {
    try {
      const data = await api("/api/alerts?unread=1");
      const n = data.alerts.length;
      const badge = $("#alert-badge");
      badge.textContent = n;
      badge.classList.toggle("hidden", n === 0);
    } catch (_) { /* ignore */ }
  }

  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-view-link]");
    if (link) { e.preventDefault(); showView(link.dataset.viewLink); }
  });

  // ------------------------------------------------------------------ auth
  let authMode = "login";
  $$(".tab[data-auth]").forEach((tab) => tab.addEventListener("click", () => {
    authMode = tab.dataset.auth;
    $$(".tab[data-auth]").forEach((t) => t.classList.toggle("active", t === tab));
    $("#auth-email-row").classList.toggle("hidden", authMode !== "register");
    $("#auth-submit").textContent = authMode === "login" ? "Log in" : "Create account";
    $("#auth-title").textContent = authMode === "login" ? "Welcome back" : "Create your account";
    $("#auth-sub").textContent = authMode === "login" ? "Log in to see what's coming up." : "Takes ten seconds. No e-mail required.";
    $("#auth-error").textContent = "";
    $("#auth-form input[name=password]").setAttribute("autocomplete", authMode === "login" ? "current-password" : "new-password");
  }));

  $("#auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#auth-error").textContent = "";
    const data = formToObject(e.target);
    try {
      state.user = await api(`/api/auth/${authMode}`, { method: "POST", body: data });
      e.target.reset();
      showApp();
    } catch (err) {
      $("#auth-error").textContent = err.message;
    }
  });

  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    state.user = null;
    showAuth();
  }
  $("#logout-btn").addEventListener("click", logout);
  $("#logout-btn-m").addEventListener("click", logout);

  // ------------------------------------------------------------------ dashboard
  async function loadDashboard() {
    const [data, alerts] = await Promise.all([api("/api/dashboard"), api("/api/alerts")]);
    const t = data.totals;
    const b = data.buckets;

    const soon = [...b.overdue, ...b.due_today, ...b.next_7_days, ...b.next_30_days];
    $("#dash-sub").textContent = t.items === 0
      ? "Add your first item or import an e-mail to get started."
      : `${t.items} item${t.items === 1 ? "" : "s"} tracked · ${soon.length} due in the next 30 days${t.overdue ? ` · ${t.overdue} overdue` : ""}`;

    $("#stats").innerHTML = `
      <div class="stat"><div class="label">${icon("list")}Tracked</div><div class="value">${t.items}</div><div class="hint">active items</div></div>
      <div class="stat ${t.overdue ? "critical" : ""}"><div class="label">${icon("alert")}Overdue</div><div class="value">${t.overdue}</div><div class="hint">${t.overdue ? "needs attention" : "nothing overdue"}</div></div>
      <div class="stat ${t.due_within_30_days ? "warning" : ""}"><div class="label">${icon("clock")}Due in 30 days</div><div class="value">${t.due_within_30_days}</div><div class="hint">${esc(money(t.spend_next_30_days, "GBP"))} to pay</div></div>
      <div class="stat"><div class="label">${icon("wallet")}Monthly cost</div><div class="value">${esc(money(t.estimated_monthly_spend, "GBP"))}</div><div class="hint">≈ ${esc(money(t.estimated_monthly_spend * 12, "GBP"))} a year</div></div>
    `;

    // Coming up: the next 8 items due, soonest first.
    const upcoming = soon.length ? soon : b.next_90_days.slice(0, 5);
    $("#dash-upcoming-sub").textContent = soon.length ? "next 30 days" : upcoming.length ? "next 90 days" : "";
    const list = $("#dash-upcoming");
    list.innerHTML = "";
    if (!upcoming.length) {
      list.innerHTML = t.items === 0
        ? emptyState("calendar", "Nothing tracked yet", "Add a bill, subscription, insurance policy or passport, or import a confirmation e-mail.", `<button class="btn btn-primary" data-action="new-item">${icon("plus")}Add your first item</button>`)
        : emptyState("check-circle", "You're all clear", "Nothing is due in the next 90 days.");
      $$("[data-action=new-item]", list).forEach((btn) => btn.addEventListener("click", () => openItemModal(null)));
    } else {
      upcoming.slice(0, 8).forEach((item) => list.appendChild(renderItem(item, { compact: true })));
    }

    // Spend by category bar chart (monthly equivalent).
    const chart = $("#dash-chart");
    const rows = Object.entries(data.by_category)
      .filter(([, v]) => v.count > 0 && v.monthly_equivalent > 0)
      .sort((a, c) => c[1].monthly_equivalent - a[1].monthly_equivalent);
    const max = Math.max(1, ...rows.map(([, v]) => v.monthly_equivalent));
    chart.innerHTML = rows.length
      ? rows.map(([key, v]) => `
        <div class="bar-row cat-${key}" title="${esc(categoryLabel(key))}: ${esc(money(v.monthly_equivalent, "GBP"))} per month across ${v.count} item${v.count === 1 ? "" : "s"}">
          <div class="lbl"><span class="tag"><span class="dot"></span>${esc(categoryLabel(key))}</span></div>
          <div class="track"><div class="fill" style="width:${Math.max(2, (v.monthly_equivalent / max) * 100)}%"></div></div>
          <div class="val">${esc(money(v.monthly_equivalent, "GBP"))}<small>${v.count}</small></div>
        </div>`).join("")
      : `<p class="muted">Add items with amounts to see where the money goes.</p>`;

    // Recent alerts.
    // One line per item (the most urgent stage), unread first.
    const seen = new Set();
    const recent = alerts.alerts.filter((a) => !seen.has(a.item_id) && seen.add(a.item_id)).slice(0, 5);
    $("#dash-alerts").innerHTML = recent.length
      ? recent.map((a) => `<div class="mini-row"><span class="pill ${a.kind === "upcoming" ? "upcoming" : "overdue"}">${icon(a.kind === "upcoming" ? "clock" : "alert")}${a.kind === "upcoming" ? "soon" : a.kind === "overdue" ? "overdue" : "today"}</span><span class="txt" title="${esc(a.message)}">${esc(a.item_name || "")}</span><span class="when">${esc(fmtDate(a.renewal_date))}</span></div>`).join("")
      : `<p class="muted">No alerts yet.</p>`;

    // Everything else, grouped.
    const later = [...b.next_90_days, ...b.later].filter((i) => !upcoming.includes(i));
    const container = $("#dash-buckets");
    container.innerHTML = "";
    if (later.length) {
      const groups = [["Next 90 days", b.next_90_days.filter((i) => !upcoming.includes(i))], ["Later", b.later]];
      groups.forEach(([label, items]) => {
        if (!items.length) return;
        const head = document.createElement("div");
        head.className = "section-head";
        head.innerHTML = `<h2>${label}</h2><span class="count">${items.length}</span><span class="rule"></span>`;
        const wrap = document.createElement("div");
        wrap.className = "item-list";
        items.forEach((item) => wrap.appendChild(renderItem(item)));
        container.appendChild(head);
        container.appendChild(wrap);
      });
    }
  }

  $("#dash-check-btn").addEventListener("click", runCheck);
  $("#alerts-check-btn").addEventListener("click", runCheck);

  async function runCheck() {
    try {
      const data = await api("/api/alerts/check", { method: "POST" });
      toast(data.created ? `${data.created} new alert${data.created === 1 ? "" : "s"} raised.` : "No new alerts. You're up to date.");
      refreshBadge();
      reloadCurrent();
    } catch (err) { toast(err.message, true); }
  }

  // ------------------------------------------------------------------ items
  function closeMenus() {
    $$(".menu").forEach((m) => m.remove());
  }
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".menu-wrap")) closeMenus();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenus(); });

  function renderItem(item, { compact = false } = {}) {
    const el = document.createElement("div");
    const catClass = `cat-${item.category}`;
    el.className = `item status-${item.status} ${catClass}`;
    const meta = [`<span class="tag"><span class="dot"></span>${esc(categoryLabel(item.category))}</span>`];
    if (item.provider) meta.push(`<span>${esc(item.provider)}</span>`);
    if (item.reference && !compact) meta.push(`<span>Ref ${esc(item.reference)}</span>`);
    if (item.recurrence !== "none" && !compact) meta.push(`<span>${item.recurrence === "custom" ? `every ${item.interval_days} days` : esc(item.recurrence)}</span>`);
    if (item.auto_renews) meta.push(`<span class="pill auto">${icon("repeat")}auto-renews</span>`);
    if (item.source === "email") meta.push(`<span class="pill email">${icon("mail")}from e-mail</span>`);

    el.innerHTML = `
      ${dateChip(item.renewal_date, catClass)}
      <div class="body">
        <div class="title"><span class="name">${esc(item.name)}</span><span class="pill ${item.status}">${icon(statusIcon(item.status))}${statusLabel(item.status)}</span></div>
        <div class="meta">${meta.join('<span class="sep"></span>')}</div>
      </div>
      <div class="right">
        <div class="amount">${esc(money(item.amount, item.currency))}</div>
        <div class="when ${whenClass(item.status)}">${esc(whenText(item.days_until_renewal))}</div>
      </div>
      <div class="item-actions">
        ${item.archived ? "" : `<button class="btn btn-sm" data-act="renew" title="Mark as paid / renewed">${icon("check")}Renewed</button>`}
        ${compact ? "" : `<div class="menu-wrap"><button class="icon-btn" data-act="menu" aria-label="More actions">${icon("more")}</button></div>`}
      </div>`;

    el.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      const act = btn.dataset.act;
      if (act === "menu") {
        e.stopPropagation();
        const wrap = btn.parentElement;
        const open = $(".menu", wrap);
        closeMenus();
        if (open) return;
        const menu = document.createElement("div");
        menu.className = "menu";
        menu.innerHTML = `
          <button data-act="edit">${icon("edit")}Edit</button>
          <button data-act="archive">${icon("archive")}${item.archived ? "Restore" : "Archive"}</button>
          <hr>
          <button data-act="delete" class="danger">${icon("trash")}Delete</button>`;
        wrap.appendChild(menu);
        return;
      }
      closeMenus();
      if (act === "edit") openItemModal(item);
      if (act === "renew") openRenewModal(item);
      if (act === "archive") {
        await api(`/api/items/${item.id}/archive`, { method: "POST", body: { archived: !item.archived } });
        toast(item.archived ? "Item restored." : "Item archived.");
        reloadCurrent();
      }
      if (act === "delete") {
        if (!confirm(`Delete "${item.name}"? This cannot be undone.`)) return;
        await api(`/api/items/${item.id}`, { method: "DELETE" });
        toast("Item deleted.");
        reloadCurrent();
      }
    });
    return el;
  }

  function reloadCurrent() {
    showView(state.view);
    refreshBadge();
  }

  async function loadItems() {
    const params = new URLSearchParams();
    const q = $("#items-search").value.trim();
    const cat = $("#items-category").value;
    if (q) params.set("q", q);
    if (cat) params.set("category", cat);
    if ($("#items-archived").checked) params.set("archived", "all");
    const data = await api(`/api/items?${params}`);
    state.items = data.items;
    const list = $("#items-list");
    list.innerHTML = "";
    $("#items-sub").textContent = `${data.items.length} item${data.items.length === 1 ? "" : "s"}${q || cat ? " match your filters" : ""}.`;
    if (!data.items.length) {
      list.innerHTML = q || cat
        ? emptyState("search", "No matches", "Try a different search or category.")
        : emptyState("calendar", "Nothing tracked yet", "Add a bill, subscription, insurance policy or passport, or import a confirmation e-mail.", `<button class="btn btn-primary" data-action="new-item">${icon("plus")}Add your first item</button>`);
      $$("[data-action=new-item]", list).forEach((btn) => btn.addEventListener("click", () => openItemModal(null)));
      return;
    }
    data.items.forEach((item) => list.appendChild(renderItem(item)));
  }

  let searchTimer;
  $("#items-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadItems, 250); });
  $("#items-category").addEventListener("change", loadItems);
  $("#items-archived").addEventListener("change", loadItems);

  // ------------------------------------------------------------------ item modal
  $$("[data-action=new-item]").forEach((b) => b.addEventListener("click", () => openItemModal(null)));
  $$("[data-action=close-modal]").forEach((b) => b.addEventListener("click", closeItemModal));
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeItemModal(); });

  function openItemModal(item) {
    const form = $("#item-form");
    form.reset();
    $("#item-error").textContent = "";
    $("#modal-title").textContent = item ? "Edit item" : "Add item";
    form.id.value = item ? item.id : "";
    if (item) {
      form.name.value = item.name;
      form.category.value = item.category;
      form.provider.value = item.provider || "";
      form.reference.value = item.reference || "";
      form.amount.value = item.amount ?? "";
      form.currency.value = item.currency || "GBP";
      form.renewal_date.value = item.renewal_date;
      form.recurrence.value = item.recurrence;
      form.interval_days.value = item.interval_days || "";
      form.reminder_days.value = item.reminder_days;
      form.auto_renews.checked = !!item.auto_renews;
      form.notes.value = item.notes || "";
    } else {
      form.category.value = "subscription";
      applyCategoryDefaults(form, true);
    }
    toggleInterval(form);
    $("#modal").classList.remove("hidden");
    form.name.focus();
  }

  function closeItemModal() { $("#modal").classList.add("hidden"); }

  function applyCategoryDefaults(form, force = false) {
    const cat = state.categories.find((c) => c.key === form.category.value);
    if (!cat) return;
    if (force || !form.reminder_days.value) form.reminder_days.value = cat.default_reminder_days;
    if (force || !form.recurrence.value) form.recurrence.value = cat.default_recurrence;
    toggleInterval(form);
  }

  function toggleInterval(form) {
    $("#interval-row").classList.toggle("hidden", form.recurrence.value !== "custom");
  }

  $("#item-category").addEventListener("change", (e) => applyCategoryDefaults(e.target.form, !e.target.form.id.value));
  $("#item-recurrence").addEventListener("change", (e) => toggleInterval(e.target.form));

  $("#item-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const data = formToObject(form);
    const id = data.id;
    delete data.id;
    if (data.amount === "") data.amount = null;
    if (data.interval_days === "") data.interval_days = null;
    $("#item-save").disabled = true;
    try {
      if (id) await api(`/api/items/${id}`, { method: "PUT", body: data });
      else await api("/api/items", { method: "POST", body: data });
      closeItemModal();
      toast(id ? "Item updated." : "Item added.");
      reloadCurrent();
    } catch (err) {
      $("#item-error").textContent = err.message;
    } finally {
      $("#item-save").disabled = false;
    }
  });

  // ------------------------------------------------------------------ renew modal
  $$("[data-action=close-renew]").forEach((b) => b.addEventListener("click", () => $("#renew-modal").classList.add("hidden")));
  $("#renew-modal").addEventListener("click", (e) => { if (e.target.id === "renew-modal") $("#renew-modal").classList.add("hidden"); });

  function openRenewModal(item) {
    const form = $("#renew-form");
    form.reset();
    $("#renew-error").textContent = "";
    form.id.value = item.id;
    form.amount.value = item.amount ?? "";
    const recurring = item.recurrence !== "none";
    $("#renew-summary").textContent = recurring
      ? `${item.name} repeats ${item.recurrence}. Leave the date blank to roll forward to the next cycle automatically.`
      : `${item.name} does not repeat. Enter the new expiry / renewal date (e.g. from your new passport or policy documents).`;
    form.new_date.required = !recurring;
    $("#renew-modal").classList.remove("hidden");
  }

  $("#renew-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formToObject(e.target);
    const body = {};
    if (data.new_date) body.new_date = data.new_date;
    if (data.amount !== "") body.amount = data.amount;
    try {
      const item = await api(`/api/items/${data.id}/renew`, { method: "POST", body });
      $("#renew-modal").classList.add("hidden");
      toast(`Marked as renewed. Next date: ${fmtDate(item.renewal_date)}.`);
      reloadCurrent();
    } catch (err) {
      $("#renew-error").textContent = err.message;
    }
  });

  // ------------------------------------------------------------------ alerts
  async function loadAlerts() {
    const data = await api("/api/alerts");
    const list = $("#alerts-list");
    list.innerHTML = "";
    if (!data.alerts.length) {
      list.innerHTML = emptyState("bell", "No alerts yet", "Alerts appear here when an item enters its reminder window, on the due date, and when something is overdue.");
      return;
    }
    data.alerts.forEach((a) => {
      const row = document.createElement("div");
      row.className = `alert-row kind-${a.kind} ${a.acknowledged ? "read" : ""}`;
      const channels = [a.pushed_at ? "pushed" : null, a.emailed_at ? "e-mailed" : null].filter(Boolean).join(", ");
      row.innerHTML = `
        <div class="ico">${icon(a.kind === "upcoming" ? "clock" : "alert")}</div>
        <div><div class="msg">${esc(a.message)}</div><div class="sub">${esc(categoryLabel(a.category))} · raised ${new Date(a.created_at).toLocaleString()}${channels ? " · " + channels : ""}</div></div>
        ${a.acknowledged ? `<span class="pill">${icon("check")}read</span>` : `<button class="btn btn-sm">Mark read</button>`}`;
      const btn = $("button", row);
      if (btn) btn.addEventListener("click", async () => {
        await api(`/api/alerts/${a.id}/ack`, { method: "POST" });
        loadAlerts();
        refreshBadge();
      });
      list.appendChild(row);
    });
  }

  $("#alerts-ack-all").addEventListener("click", async () => {
    await api("/api/alerts/ack-all", { method: "POST" });
    loadAlerts();
    refreshBadge();
  });

  // ------------------------------------------------------------------ import
  const dropzone = $("#dropzone");
  const fileInput = $("#import-file-form input[type=file]");
  fileInput.addEventListener("change", () => {
    $("#dropzone-name").textContent = fileInput.files[0] ? fileInput.files[0].name : "";
  });
  ["dragenter", "dragover"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); }));
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      $("#dropzone-name").textContent = fileInput.files[0].name;
    }
  });

  $("#import-file-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    if (!fd.get("file") || !fd.get("file").name) { $("#import-error").textContent = "Choose a file first."; return; }
    await submitImport(fd);
  });

  $("#import-text-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = e.target.text.value.trim();
    if (!text) { $("#import-error").textContent = "Paste some e-mail text first."; return; }
    await submitImport({ text });
  });

  async function submitImport(body) {
    $("#import-error").textContent = "";
    try {
      const record = await api("/api/imports/parse", { method: "POST", body });
      state.currentImport = record;
      renderReview(record);
      loadImports();
    } catch (err) {
      $("#import-error").textContent = err.message;
    }
  }

  function renderReview(record) {
    const p = record.parsed;
    const box = $("#import-review");
    const catOptions = state.categories.map((c) => `<option value="${c.key}" ${c.key === p.category ? "selected" : ""}>${esc(c.label)}</option>`).join("");
    const recOptions = state.recurrences.map((r) => `<option value="${r}" ${r === p.recurrence ? "selected" : ""}>${r === "none" ? "Does not repeat" : r}</option>`).join("");
    const candidates = (p.date_candidates || []).map((c) =>
      `<button type="button" data-date="${c.date}" class="${c.date === p.renewal_date ? "selected" : ""}" title="found as “${esc(c.text)}”">${esc(fmtDate(c.date))}</button>`
    ).join("");
    const pct = Math.round((p.confidence || 0) * 100);
    box.innerHTML = `
      ${p.warnings && p.warnings.length ? `<div class="callout">${icon("alert")}<div>${p.warnings.map(esc).join("<br>")}</div></div>` : ""}
      <div class="confidence"><span class="meter"><span style="width:${pct}%"></span></span><span>${pct}% confidence</span>${record.subject ? `<span>· “${esc(record.subject)}”</span>` : ""}${record.sender ? `<span>· from ${esc(record.sender)}</span>` : ""}</div>
      <form id="review-form" class="form">
        <div class="row">
          <label class="grow">Name <input name="name" value="${esc(p.name || "")}" required></label>
          <label>Category <select name="category">${catOptions}</select></label>
        </div>
        <div class="row">
          <label class="grow">Provider <input name="provider" value="${esc(p.provider || "")}"></label>
          <label>Reference <input name="reference" value="${esc(p.reference || "")}"></label>
        </div>
        <div class="row">
          <label>Amount <input name="amount" type="number" step="0.01" min="0" value="${p.amount ?? ""}"></label>
          <label style="flex:0 0 6rem">Currency <input name="currency" maxlength="3" value="${esc(p.currency || "GBP")}"></label>
          <label class="grow">Renewal / expiry date <input name="renewal_date" type="date" value="${p.renewal_date || ""}" required></label>
        </div>
        ${candidates ? `<div class="candidates"><span>Dates found in the e-mail:</span>${candidates}</div>` : ""}
        <div class="row">
          <label>Repeats <select name="recurrence">${recOptions}</select></label>
          <label class="grow">Remind me (days before) <input name="reminder_days" value="${esc(p.reminder_days || "")}"></label>
        </div>
        <label class="check"><input type="checkbox" name="auto_renews" ${p.auto_renews ? "checked" : ""}> Renews automatically</label>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="review-discard">Discard</button>
          <button type="submit" class="btn btn-primary">${icon("plus")}Add to tracker</button>
        </div>
        <p id="review-error" class="error"></p>
      </form>
      <details><summary>Show extracted e-mail text</summary><div class="excerpt">${esc(record.raw_excerpt || "")}</div></details>`;

    $$(".candidates button", box).forEach((b) => b.addEventListener("click", () => {
      $("#review-form").renewal_date.value = b.dataset.date;
      $$(".candidates button", box).forEach((x) => x.classList.toggle("selected", x === b));
    }));

    $("#review-discard").addEventListener("click", async () => {
      await api(`/api/imports/${record.id}/discard`, { method: "POST" });
      box.innerHTML = `<div class="callout info">${icon("check")}<div>Import discarded.</div></div>`;
      loadImports();
    });

    $("#review-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = formToObject(e.target);
      if (data.amount === "") data.amount = null;
      try {
        const res = await api(`/api/imports/${record.id}/confirm`, { method: "POST", body: data });
        box.innerHTML = `<div class="callout success">${icon("check-circle")}<div>Added <strong>${esc(res.item.name)}</strong>, renews ${esc(fmtDate(res.item.renewal_date))}.</div></div>`;
        toast("Item added from e-mail.");
        loadImports();
        refreshBadge();
      } catch (err) {
        $("#review-error").textContent = err.message;
      }
    });
  }

  async function loadImports() {
    const data = await api("/api/imports");
    const list = $("#imports-list");
    list.innerHTML = "";
    if (!data.imports.length) {
      list.innerHTML = emptyState("inbox", "No imports yet", "Parsed e-mails show up here so you can come back to one later.");
      return;
    }
    data.imports.forEach((rec) => {
      const p = rec.parsed || {};
      const row = document.createElement("div");
      row.className = `item status-ok cat-${p.category || "other"}`;
      const meta = [p.provider ? esc(p.provider) : null, p.renewal_date ? esc(fmtDate(p.renewal_date)) : null, p.amount != null ? esc(money(p.amount, p.currency)) : null, new Date(rec.created_at).toLocaleString()].filter(Boolean);
      row.innerHTML = `
        <div class="cat-icon">${icon("mail")}</div>
        <div class="body">
          <div class="title"><span class="name">${esc(rec.subject || p.name || rec.filename || "Pasted e-mail")}</span><span class="pill ${rec.status === "confirmed" ? "ok" : rec.status === "discarded" ? "archived" : "upcoming"}">${esc(rec.status)}</span></div>
          <div class="meta">${meta.map((m) => `<span>${m}</span>`).join('<span class="sep"></span>')}</div>
        </div>
        <div class="right"></div>
        <div class="item-actions">${rec.status === "pending" ? `<button class="btn btn-sm">Review</button>` : ""}</div>`;
      const btn = $("button", row);
      if (btn) btn.addEventListener("click", () => { renderReview(rec); window.scrollTo({ top: 0, behavior: "smooth" }); });
      list.appendChild(row);
    });
  }

  // ------------------------------------------------------------------ push notifications
  const push = {
    supported: "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
    serverEnabled: false,
    publicKey: null,
    registration: null,
    subscription: null,
  };

  function urlBase64ToUint8Array(base64) {
    const padding = "=".repeat((4 - (base64.length % 4)) % 4);
    const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  }

  async function initPush() {
    try {
      const meta = await api("/api/push/vapid-public-key");
      push.serverEnabled = !!meta.enabled;
      push.publicKey = meta.public_key;
    } catch (_) { push.serverEnabled = false; }
    if (!push.supported || !push.serverEnabled) return;
    try {
      push.registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      push.subscription = await push.registration.pushManager.getSubscription();
      if (push.subscription && state.user && Notification.permission === "granted") {
        await api("/api/push/subscribe", { method: "POST", body: { subscription: push.subscription.toJSON() } });
      }
    } catch (err) {
      console.warn("Service worker registration failed:", err);
    }
  }

  async function renderPushSettings() {
    const status = $("#push-status");
    const enableBtn = $("#push-enable-btn");
    const disableBtn = $("#push-disable-btn");
    const testBtn = $("#push-test-btn");
    [enableBtn, disableBtn, testBtn].forEach((b) => b.classList.add("hidden"));

    if (!push.supported) {
      status.textContent = "This browser does not support push notifications. Try Chrome, Edge, Firefox, or Safari 16.4+ (installed to the Home Screen on iOS).";
      return;
    }
    if (!push.serverEnabled) {
      status.textContent = "Push notifications are disabled on this server.";
      return;
    }
    if (!window.isSecureContext) {
      status.textContent = "Push notifications need HTTPS (or localhost).";
      return;
    }
    if (Notification.permission === "denied") {
      status.textContent = "Notifications are blocked for this site. Allow them in your browser's site settings, then reload.";
      return;
    }
    if (push.subscription) {
      status.innerHTML = `<span class="pill ok">${icon("check")}enabled</span><span>This device will receive renewal alerts.</span>`;
      disableBtn.classList.remove("hidden");
      testBtn.classList.remove("hidden");
    } else {
      status.textContent = "Not enabled on this device.";
      enableBtn.classList.remove("hidden");
    }
    await renderPushDevices();
  }

  async function renderPushDevices() {
    const box = $("#push-devices");
    try {
      const current = push.subscription ? push.subscription.endpoint : "";
      const data = await api(`/api/push/subscriptions?endpoint=${encodeURIComponent(current)}`);
      if (!data.subscriptions.length) { box.innerHTML = ""; return; }
      box.innerHTML = `<h3>Devices receiving alerts</h3>` + data.subscriptions.map((s) => {
        const ua = (s.user_agent || "").match(/(Chrome|Firefox|Safari|Edg)[^\s;)]*/g);
        const label = ua ? ua[ua.length - 1].replace("Edg", "Edge") : "Browser";
        return `<div class="push-device"><span>${esc(label)} · ${esc(s.service)}${s.current ? " <strong>(this device)</strong>" : ""}</span>
          <span class="muted">added ${new Date(s.created_at).toLocaleDateString()}</span>
          <button class="btn btn-sm btn-ghost btn-danger" data-id="${s.id}" data-current="${s.current ? 1 : 0}">Remove</button></div>`;
      }).join("");
      $$("button[data-id]", box).forEach((b) => b.addEventListener("click", async () => {
        await api("/api/push/unsubscribe", { method: "POST", body: { id: Number(b.dataset.id) } });
        if (b.dataset.current === "1" && push.subscription) {
          await push.subscription.unsubscribe().catch(() => {});
          push.subscription = null;
        }
        renderPushSettings();
      }));
    } catch (_) { box.innerHTML = ""; }
  }

  $("#push-enable-btn").addEventListener("click", async () => {
    const btn = $("#push-enable-btn");
    btn.disabled = true;
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        toast("Notifications were not allowed.", true);
        return;
      }
      if (!push.registration) push.registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("the browser's push service did not respond. Check your internet connection and try again.")), 20000));
      push.subscription = await Promise.race([
        push.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(push.publicKey) }),
        timeout,
      ]);
      await api("/api/push/subscribe", { method: "POST", body: { subscription: push.subscription.toJSON() } });
      toast("Push notifications enabled on this device.");
    } catch (err) {
      let hint = "";
      if (/permission denied|registration failed/i.test(err.message) && Notification.permission === "granted") {
        hint = " Private/incognito windows and some browsers block the Push API even after notifications are allowed. Try a normal window.";
      }
      toast("Could not enable push notifications: " + err.message + hint, true);
    } finally {
      btn.disabled = false;
      renderPushSettings();
    }
  });

  $("#push-disable-btn").addEventListener("click", async () => {
    try {
      if (push.subscription) {
        await api("/api/push/unsubscribe", { method: "POST", body: { endpoint: push.subscription.endpoint } });
        await push.subscription.unsubscribe();
        push.subscription = null;
      }
      toast("Push notifications turned off on this device.");
    } catch (err) {
      toast(err.message, true);
    }
    renderPushSettings();
  });

  $("#push-test-btn").addEventListener("click", async () => {
    try {
      const res = await api("/api/push/test", { method: "POST", body: { endpoint: push.subscription ? push.subscription.endpoint : null } });
      toast(res.sent ? "Test notification sent. It should appear in a moment." : "No notification was sent.", !res.sent);
    } catch (err) {
      toast(err.message, true);
    }
  });

  // ------------------------------------------------------------------ settings
  async function loadSettings() {
    const form = $("#settings-form");
    form.email.value = state.user.email || "";
    form.notify_by_email.checked = !!state.user.notify_by_email;
    form.current_password.value = "";
    form.new_password.value = "";
    $("#settings-msg").textContent = "";
    await renderPushSettings();
  }

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formToObject(e.target);
    const body = { email: data.email, notify_by_email: data.notify_by_email };
    if (data.new_password) { body.new_password = data.new_password; body.current_password = data.current_password; }
    try {
      state.user = await api("/api/auth/me", { method: "PUT", body });
      await loadSettings();
      $("#settings-msg").textContent = "Settings saved.";
    } catch (err) {
      $("#settings-msg").textContent = err.message;
    }
  });

  // ------------------------------------------------------------------ boot
  async function boot() {
    const meta = await api("/api/categories");
    state.categories = meta.categories;
    state.recurrences = meta.recurrences;
    [$("#item-category"), $("#items-category")].forEach((sel) => {
      state.categories.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c.key;
        opt.textContent = c.label;
        sel.appendChild(opt);
      });
    });
    state.recurrences.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r;
      opt.textContent = r === "none" ? "Does not repeat" : r.charAt(0).toUpperCase() + r.slice(1);
      $("#item-recurrence").appendChild(opt);
    });

    const me = await api("/api/auth/me");
    if (me.user) {
      state.user = me.user;
      showApp();
    } else {
      showAuth();
    }
  }

  boot().catch((err) => toast(err.message, true));
})();
