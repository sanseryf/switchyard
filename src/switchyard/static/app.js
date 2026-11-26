const ui = {
  banner: document.getElementById("banner"),
  live: document.getElementById("live"),
  auto: document.getElementById("auto"),
  statEvents: document.getElementById("stat-events"),
  statDelivered: document.getElementById("stat-delivered"),
  statPending: document.getElementById("stat-pending"),
  statDead: document.getElementById("stat-dead"),
  routesBody: document.getElementById("routes-body"),
  eventsBody: document.getElementById("events-body"),
  deliveriesBody: document.getElementById("deliveries-body"),
  dlqBody: document.getElementById("dlq-body"),
  deliveryStatus: document.getElementById("delivery-status"),
  drawer: document.getElementById("drawer"),
  drawerTitle: document.getElementById("drawer-title"),
  drawerBody: document.getElementById("drawer-body"),
  drawerStatus: document.getElementById("drawer-status"),
  drawerClose: document.getElementById("drawer-close"),
  replay: document.getElementById("replay"),
  themeToggle: document.getElementById("theme-toggle"),
  themeColor: document.getElementById("theme-color")
};

let currentDeadLetter = null;
let refreshTimer = null;
let busy = false;

function cell(text, className) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text === null || text === undefined ? "" : String(text);
  return td;
}

function pill(text, kind) {
  const span = document.createElement("span");
  span.className = "pill " + kind;
  span.textContent = text;
  return span;
}

function relative(epoch) {
  if (!epoch) return "never";
  const seconds = Math.round(Date.now() / 1000 - Number(epoch));
  if (seconds < 5) return "just now";
  if (seconds < 60) return seconds + "s ago";
  if (seconds < 3600) return Math.round(seconds / 60) + "m ago";
  if (seconds < 86400) return Math.round(seconds / 3600) + "h ago";
  return Math.round(seconds / 86400) + "d ago";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("switchyard.theme", theme);
  } catch (error) {}
  const day = theme === "day";
  if (ui.themeColor) ui.themeColor.content = day ? "#D8CDB7" : "#030705";
  if (ui.themeToggle) {
    ui.themeToggle.setAttribute("aria-pressed", day ? "true" : "false");
    ui.themeToggle.setAttribute(
      "aria-label",
      day ? "Switch to night theme" : "Switch to day theme"
    );
  }
}

function wireTheme() {
  if (!ui.themeToggle) return;
  ui.themeToggle.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "day" ? "night" : "day";
    applyTheme(next);
  });
  applyTheme(document.documentElement.dataset.theme === "day" ? "day" : "night");
}

wireTheme();

function plural(count, singular) {
  return count + " " + singular + (count === 1 ? "" : "s");
}

function showBanner(message) {
  if (!message) {
    ui.banner.hidden = true;
    ui.banner.textContent = "";
    return;
  }
  ui.banner.hidden = false;
  ui.banner.textContent = message;
}

async function getJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = "HTTP " + response.status;
    try {
      const problem = await response.json();
      if (problem.detail) detail = String(problem.detail);
    } catch (ignored) {}
    throw new Error(detail);
  }
  return response.json();
}

function renderStats(stats) {
  ui.statEvents.textContent = stats.events;
  ui.statDelivered.textContent = stats.deliveries.delivered || 0;
  ui.statPending.textContent = stats.deliveries.pending || 0;
  ui.statDead.textContent = stats.dead_letters;
}

function renderRoutes(routes) {
  ui.routesBody.replaceChildren();
  if (!routes.length) {
    ui.routesBody.appendChild(
      Object.assign(document.createElement("tr"), {
        innerHTML: '<td colspan="4" class="empty">No routes configured.</td>'
      })
    );
    return;
  }
  routes.forEach((route) => {
    const row = document.createElement("tr");
    row.appendChild(cell(route.id, "mono"));
    row.appendChild(cell(route.event, "mono"));
    row.appendChild(cell(route.destination, "mono"));
    const assayCell = document.createElement("td");
    assayCell.appendChild(
      route.validates_with_assay ? pill("assay", "assay") : document.createTextNode("-")
    );
    row.appendChild(assayCell);
    ui.routesBody.appendChild(row);
  });
}

function renderEvents(events) {
  ui.eventsBody.replaceChildren();
  if (!events.length) {
    const row = document.createElement("tr");
    const td = cell("No events yet. Send a signed webhook.", "empty");
    td.colSpan = 6;
    row.appendChild(td);
    ui.eventsBody.appendChild(row);
    return;
  }
  events.forEach((event) => {
    const row = document.createElement("tr");
    row.appendChild(cell(event.id, "mono"));
    row.appendChild(cell(event.event, "mono"));
    row.appendChild(cell(event.delivery, "mono"));
    row.appendChild(cell(relative(event.received_at)));
    row.appendChild(cell(event.routes, "mono"));
    const status = document.createElement("td");
    const counts = document.createElement("div");
    counts.className = "counts";
    if (event.delivered) counts.appendChild(pill(event.delivered + " delivered", "delivered"));
    if (event.pending) counts.appendChild(pill(event.pending + " pending", "pending"));
    if (event.dead) counts.appendChild(pill(event.dead + " dead", "dead"));
    if (!event.routes) counts.appendChild(document.createTextNode("no route"));
    status.appendChild(counts);
    row.appendChild(status);
    ui.eventsBody.appendChild(row);
  });
}

function renderDeliveries(deliveries) {
  ui.deliveriesBody.replaceChildren();
  if (!deliveries.length) {
    const row = document.createElement("tr");
    const td = cell("No deliveries for this filter.", "empty");
    td.colSpan = 6;
    row.appendChild(td);
    ui.deliveriesBody.appendChild(row);
    return;
  }
  deliveries.forEach((delivery) => {
    const row = document.createElement("tr");
    row.appendChild(cell(delivery.id, "mono"));
    row.appendChild(cell(delivery.event, "mono"));
    row.appendChild(cell(delivery.route_id, "mono"));
    const status = document.createElement("td");
    status.appendChild(pill(delivery.status, delivery.status));
    row.appendChild(status);
    row.appendChild(cell(delivery.attempts, "mono"));
    row.appendChild(cell(delivery.last_error || "-", "mono"));
    ui.deliveriesBody.appendChild(row);
  });
}

function renderDlq(letters) {
  ui.dlqBody.replaceChildren();
  if (!letters.length) {
    const row = document.createElement("tr");
    const td = cell("No dead letters. Nothing is stuck.", "empty");
    td.colSpan = 6;
    row.appendChild(td);
    ui.dlqBody.appendChild(row);
    return;
  }
  letters.forEach((letter) => {
    const row = document.createElement("tr");
    row.className = "clickable";
    row.tabIndex = 0;
    row.addEventListener("click", () => openDeadLetter(letter.id));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDeadLetter(letter.id);
      }
    });
    row.appendChild(cell(letter.id, "mono"));
    row.appendChild(cell(letter.reason, "mono"));
    row.appendChild(cell(letter.route_id, "mono"));
    row.appendChild(cell(letter.event_id, "mono"));
    row.appendChild(cell(relative(letter.created_at)));
    const report = letter.report;
    const summary = report
      ? (report.groups ? plural(report.groups.length, "group") + ", " : "") +
        plural(report.errors || 0, "error")
      : "none";
    row.appendChild(cell(summary, "mono"));
    ui.dlqBody.appendChild(row);
  });
}

async function loadDeliveries() {
  const status = ui.deliveryStatus.value;
  const query = status ? "?status=" + encodeURIComponent(status) : "";
  const data = await getJSON("/ui/deliveries" + query);
  renderDeliveries(data.deliveries);
}

async function loadAll() {
  if (busy) return;
  busy = true;
  try {
    const [stats, routes, events, dlq] = await Promise.all([
      getJSON("/ui/stats"),
      getJSON("/ui/routes"),
      getJSON("/ui/events"),
      getJSON("/dlq?limit=50")
    ]);
    renderStats(stats);
    renderRoutes(routes.routes);
    renderEvents(events.events);
    renderDlq(dlq.dead_letters);
    await loadDeliveries();
    showBanner("");
  } catch (error) {
    showBanner("Could not refresh: " + error.message);
  } finally {
    busy = false;
  }
}

function metaItem(term, value) {
  const box = document.createElement("div");
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = value === null || value === undefined ? "-" : String(value);
  box.append(dt, dd);
  return box;
}

function reportSection(report) {
  const section = document.createElement("section");
  if (!report) {
    const note = document.createElement("p");
    note.className = "subtle";
    note.textContent = "No Assay report. This was a delivery or route failure.";
    section.appendChild(note);
    return section;
  }
  const heading = document.createElement("h3");
  heading.textContent = "Assay report";
  section.appendChild(heading);
  const groups = report.groups || [];
  if (!groups.length) {
    const note = document.createElement("p");
    note.className = "subtle";
    note.textContent = "The report has no groups.";
    section.appendChild(note);
    return section;
  }
  groups.forEach((group) => {
    const card = document.createElement("div");
    card.className = "report-group";
    const path = document.createElement("div");
    path.className = "path";
    path.textContent = group.path;
    card.appendChild(path);
    const message = document.createElement("p");
    message.textContent = group.message;
    card.appendChild(message);
    if (group.hint) {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "hint: " + group.hint;
      card.appendChild(hint);
    }
    if (group.sample_values && group.sample_values.length) {
      const samples = document.createElement("p");
      samples.className = "samples";
      samples.textContent = "e.g. " + group.sample_values.join(", ");
      card.appendChild(samples);
    }
    section.appendChild(card);
  });
  return section;
}

function preBlock(title, text) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const pre = document.createElement("pre");
  pre.textContent = text;
  section.append(heading, pre);
  return section;
}

async function openDeadLetter(id) {
  ui.drawer.hidden = false;
  ui.drawerTitle.textContent = "Dead letter #" + id;
  ui.drawerBody.replaceChildren();
  ui.drawerStatus.textContent = "Loading...";
  ui.replay.disabled = true;
  try {
    const letter = await getJSON("/dlq/" + id);
    currentDeadLetter = letter;
    const meta = document.createElement("dl");
    meta.className = "meta";
    meta.append(
      metaItem("Reason", letter.reason),
      metaItem("Route", letter.route_id),
      metaItem("Event", letter.event_id),
      metaItem("Destination", letter.destination),
      metaItem("Created", new Date(letter.created_at * 1000).toLocaleString())
    );
    ui.drawerBody.appendChild(meta);
    ui.drawerBody.appendChild(reportSection(letter.report));
    ui.drawerBody.appendChild(preBlock("Delivery body", letter.body));
    ui.drawerBody.appendChild(preBlock("Original payload", letter.payload));
    ui.drawerStatus.textContent = "";
    ui.replay.disabled = false;
  } catch (error) {
    ui.drawerStatus.textContent = "Could not load: " + error.message;
  }
}

function closeDrawer() {
  ui.drawer.hidden = true;
  currentDeadLetter = null;
  ui.replay.disabled = false;
  ui.drawerStatus.textContent = "";
}

async function replay() {
  if (!currentDeadLetter) return;
  ui.replay.disabled = true;
  ui.drawerStatus.textContent = "Replaying...";
  try {
    const result = await getJSON("/dlq/" + currentDeadLetter.id + "/replay", {
      method: "POST"
    });
    ui.drawerStatus.textContent =
      "Queued as delivery #" + result.delivery_id + ". Re-validated before sending.";
    await loadAll();
  } catch (error) {
    ui.drawerStatus.textContent = "Replay failed: " + error.message;
    ui.replay.disabled = false;
  }
}

function schedule() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    const active = ui.auto.checked;
    ui.live.classList.toggle("paused", !active);
    ui.live.textContent = active ? "live" : "paused";
    if (active && ui.drawer.hidden) loadAll();
  }, 5000);
}

ui.auto.addEventListener("change", () => {
  ui.live.classList.toggle("paused", !ui.auto.checked);
  ui.live.textContent = ui.auto.checked ? "live" : "paused";
  if (ui.auto.checked) loadAll();
});

ui.deliveryStatus.addEventListener("change", () => {
  loadDeliveries().catch((error) => showBanner("Could not load deliveries: " + error.message));
});

ui.drawerClose.addEventListener("click", closeDrawer);
ui.replay.addEventListener("click", replay);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !ui.drawer.hidden) closeDrawer();
});

loadAll().then(() => {
  const dead = new URLSearchParams(window.location.search).get("dead");
  if (dead) openDeadLetter(Number(dead));
});
schedule();
