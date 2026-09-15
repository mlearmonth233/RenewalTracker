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

  const ICONS = { bill: "🧾", subscription: "🔁", insurance: "🛡️", passport: "🛂", other: "📌" };
  const CURRENCY = { GBP: "£", USD: "$", EUR: "€" };

  // ------------------------------------------------------------------ utils
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
    el.textContent = message;
    el.classList.toggle("error", isError);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3500);
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

  function whenText(days) {
    if (days < 0) return `${-days} day${days === -1 ? "" : "s"} overdue`;
    if (days === 0) return "Today";
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

  // ------------------------------------------------------------------ views
  function showView(name) {
    state.view = name;
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $(`#view-${name}`).classList.remove("hidden");
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    const loaders = { dashboard: loadDashboard, items: loadItems, alerts: loadAlerts, import: loadImports, settings: loadSettings };
    if (loaders[name]) loaders[name]();
  }

  function showAuth() {
    $$(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-auth").classList.remove("hidden");
    $("#nav").classList.add("hidden");
    $("#user-box").classList.add("hidden");
  }

  function showApp() {
    $("#nav").classList.remove("hidden");
    $("#user-box").classList.remove("hidden");
    $("#user-name").textContent = state.user.username;
    showView("dashboard");
    refreshBadge();
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

  // ------------------------------------------------------------------ auth
  let authMode = "login";
  $$(".tab[data-auth]").forEach((tab) => tab.addEventListener("click", () => {
    authMode = tab.dataset.auth;
    $$(".tab[data-auth]").forEach((t) => t.classList.toggle("active", t === tab));
    $("#auth-email-row").classList.toggle("hidden", authMode !== "register");
    $("#auth-submit").textContent = authMode === "login" ? "Log in" : "Create account";
    $("#auth-error").textContent = "";
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

  $("#logout-btn").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" });
    state.user = null;
    showAuth();
  });

  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

  // ------------------------------------------------------------------ dashboard
  async function loadDashboard() {
    const data = await api("/api/dashboard");
    const t = data.totals;
    $("#stats").innerHTML = `
      <div class="stat"><div class="label">Tracked items</div><div class="value">${t.items}</div></div>
      <div class="stat ${t.overdue ? "danger" : ""}"><div class="label">Overdue</div><div class="value">${t.overdue}</div></div>
      <div class="stat ${t.due_within_30_days ? "warning" : ""}"><div class="label">Due in 30 days</div><div class="value">${t.due_within_30_days}</div></div>
      <div class="stat"><div class="label">Spend next 30 days</div><div class="value">${esc(money(t.spend_next_30_days, "GBP"))}</div></div>
      <div class="stat"><div class="label">Est. monthly cost</div><div class="value">${esc(money(t.estimated_monthly_spend, "GBP"))}</div></div>
    `;
    const labels = {
      overdue: "Overdue", due_today: "Due today", next_7_days: "Next 7 days",
      next_30_days: "Next 30 days", next_90_days: "Next 90 days", later: "Later",
    };
    const container = $("#dash-buckets");
    container.innerHTML = "";
    let any = false;
    Object.entries(labels).forEach(([key, label]) => {
      const items = data.buckets[key];
      if (!items.length) return;
      any = true;
      const section = document.createElement("div");
      section.className = "bucket";
      section.innerHTML = `<h2>${label} <span class="count">(${items.length})</span></h2><div class="item-list"></div>`;
      const list = $(".item-list", section);
      items.forEach((item) => list.appendChild(renderItem(item)));
      container.appendChild(section);
    });
    if (!any) {
      container.innerHTML = `<div class="empty">Nothing tracked yet. Add your first bill, subscription, insurance policy or passport, or import an e-mail confirmation.</div>`;
    }
  }

  $("#dash-check-btn").addEventListener("click", runCheck);
  $("#alerts-check-btn").addEventListener("click", runCheck);

  async function runCheck() {
    try {
      const data = await api("/api/alerts/check", { method: "POST" });
      toast(data.created ? `${data.created} new alert${data.created === 1 ? "" : "s"} raised.` : "No new alerts – you're up to date.");
      refreshBadge();
      if (state.view === "alerts") loadAlerts();
    } catch (err) { toast(err.message, true); }
  }

  // ------------------------------------------------------------------ items
  function renderItem(item, { compact = false } = {}) {
    const el = document.createElement("div");
    el.className = `item status-${item.status}`;
    const pills = [`<span class="pill ${item.status}">${item.status.replace("_", " ")}</span>`];
    if (item.auto_renews) pills.push(`<span class="pill auto">auto-renews</span>`);
    if (item.source === "email") pills.push(`<span class="pill email">from e-mail</span>`);
    const meta = [categoryLabel(item.category)];
    if (item.provider) meta.push(esc(item.provider));
    if (item.reference) meta.push(`Ref ${esc(item.reference)}`);
    if (item.recurrence !== "none") meta.push(item.recurrence === "custom" ? `every ${item.interval_days} days` : item.recurrence);
    el.innerHTML = `
      <div class="icon">${ICONS[item.category] || ICONS.other}</div>
      <div>
        <div class="title">${esc(item.name)} ${pills.join(" ")}</div>
        <div class="meta">${meta.join(" · ")}</div>
      </div>
      <div class="right">
        <div class="amount">${esc(money(item.amount, item.currency))}</div>
        <div class="when">${fmtDate(item.renewal_date)} · ${whenText(item.days_until_renewal)}</div>
        <div class="item-actions">
          ${item.archived ? "" : `<button class="btn btn-sm" data-act="renew">Renewed</button>`}
          <button class="btn btn-sm btn-ghost" data-act="edit">Edit</button>
          <button class="btn btn-sm btn-ghost" data-act="archive">${item.archived ? "Restore" : "Archive"}</button>
          <button class="btn btn-sm btn-ghost btn-danger" data-act="delete">Delete</button>
        </div>
      </div>`;
    el.addEventListener("click", async (e) => {
      const act = e.target.dataset.act;
      if (!act) return;
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
    if (!data.items.length) {
      list.innerHTML = `<div class="empty">No items match. Try a different filter or add a new item.</div>`;
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
      list.innerHTML = `<div class="empty">No alerts yet. Alerts appear here when an item enters its reminder window.</div>`;
      return;
    }
    data.alerts.forEach((a) => {
      const row = document.createElement("div");
      row.className = `alert-row kind-${a.kind} ${a.acknowledged ? "read" : ""}`;
      row.innerHTML = `
        <div class="dot"></div>
        <div class="msg">${esc(a.message)}<div class="sub">${esc(categoryLabel(a.category))} · raised ${new Date(a.created_at).toLocaleString()}${a.emailed_at ? " · e-mailed" : ""}</div></div>
        ${a.acknowledged ? "" : `<button class="btn btn-sm">Mark read</button>`}`;
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
    const recOptions = state.recurrences.map((r) => `<option value="${r}" ${r === p.recurrence ? "selected" : ""}>${r}</option>`).join("");
    const candidates = (p.date_candidates || []).map((c) =>
      `<button type="button" data-date="${c.date}" class="${c.date === p.renewal_date ? "selected" : ""}" title="score ${c.score}">${esc(fmtDate(c.date))} <small>(“${esc(c.text)}”)</small></button>`
    ).join("");
    box.innerHTML = `
      ${p.warnings && p.warnings.length ? `<div class="warnings">${p.warnings.map(esc).join("<br>")}</div>` : ""}
      <div class="confidence">Confidence ${(p.confidence * 100).toFixed(0)}% · ${record.subject ? `Subject: “${esc(record.subject)}”` : "No subject detected"}${record.sender ? ` · From: ${esc(record.sender)}` : ""}</div>
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
          <label>Currency <input name="currency" maxlength="3" value="${esc(p.currency || "GBP")}" style="width:5rem"></label>
          <label class="grow">Renewal / expiry date <input name="renewal_date" type="date" value="${p.renewal_date || ""}" required></label>
        </div>
        ${candidates ? `<div class="candidates">Other dates found in the e-mail: ${candidates}</div>` : ""}
        <div class="row">
          <label>Repeats <select name="recurrence">${recOptions}</select></label>
          <label class="grow">Remind me (days before) <input name="reminder_days" value="${esc(p.reminder_days || "")}"></label>
        </div>
        <label class="check"><input type="checkbox" name="auto_renews" ${p.auto_renews ? "checked" : ""}> Renews automatically</label>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="review-discard">Discard</button>
          <button type="submit" class="btn btn-primary">Add to tracker</button>
        </div>
        <p id="review-error" class="error"></p>
      </form>
      <details><summary class="muted">Show extracted e-mail text</summary><div class="excerpt">${esc(record.raw_excerpt || "")}</div></details>`;

    $$(".candidates button", box).forEach((b) => b.addEventListener("click", () => {
      $("#review-form").renewal_date.value = b.dataset.date;
      $$(".candidates button", box).forEach((x) => x.classList.toggle("selected", x === b));
    }));

    $("#review-discard").addEventListener("click", async () => {
      await api(`/api/imports/${record.id}/discard`, { method: "POST" });
      box.innerHTML = `<p class="muted">Import discarded.</p>`;
      loadImports();
    });

    $("#review-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = formToObject(e.target);
      if (data.amount === "") data.amount = null;
      try {
        const res = await api(`/api/imports/${record.id}/confirm`, { method: "POST", body: data });
        box.innerHTML = `<p class="muted">✅ Added <strong>${esc(res.item.name)}</strong> – renews ${esc(fmtDate(res.item.renewal_date))}.</p>`;
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
      list.innerHTML = `<div class="empty">No imports yet.</div>`;
      return;
    }
    data.imports.forEach((rec) => {
      const p = rec.parsed || {};
      const row = document.createElement("div");
      row.className = "item status-ok";
      row.innerHTML = `
        <div class="icon">✉️</div>
        <div>
          <div class="title">${esc(rec.subject || p.name || rec.filename || "Pasted e-mail")} <span class="pill">${esc(rec.status)}</span></div>
          <div class="meta">${esc(p.provider || "")} ${p.renewal_date ? "· " + esc(fmtDate(p.renewal_date)) : ""} ${p.amount != null ? "· " + esc(money(p.amount, p.currency)) : ""} · ${new Date(rec.created_at).toLocaleString()}</div>
        </div>
        <div class="right">${rec.status === "pending" ? `<button class="btn btn-sm">Review</button>` : ""}</div>`;
      const btn = $("button", row);
      if (btn) btn.addEventListener("click", () => { renderReview(rec); window.scrollTo({ top: 0, behavior: "smooth" }); });
      list.appendChild(row);
    });
  }

  // ------------------------------------------------------------------ settings
  function loadSettings() {
    const form = $("#settings-form");
    form.email.value = state.user.email || "";
    form.notify_by_email.checked = !!state.user.notify_by_email;
    form.current_password.value = "";
    form.new_password.value = "";
    $("#settings-msg").textContent = "";
  }

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = formToObject(e.target);
    const body = { email: data.email, notify_by_email: data.notify_by_email };
    if (data.new_password) { body.new_password = data.new_password; body.current_password = data.current_password; }
    try {
      state.user = await api("/api/auth/me", { method: "PUT", body });
      $("#settings-msg").textContent = "Settings saved.";
      loadSettings();
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
    const catSelects = [$("#item-category"), $("#items-category")];
    catSelects.forEach((sel) => {
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
