"use strict";

/* ============================================================
   Workspace dashboard — talks to the FastAPI backend on the
   same origin. Views: Home (kanban) + Notes (editor).
   All user data rendered via textContent (XSS-safe).
   ============================================================ */

const API = window.location.origin;
const TOKEN_KEY = "notes_token";
const EMAIL_KEY = "notes_email";

const COLUMNS = [
  { key: "todo",   label: "To-do",       color: "#8B5CF6" },
  { key: "doing",  label: "In progress", color: "#F59E0B" },
  { key: "review", label: "In review",   color: "#2F80ED" },
  { key: "done",   label: "Complete",    color: "#22C55E" },
];

const AVATARS = ["🦊", "🐼", "🐸", "🦉", "🐙", "🦄", "🐝", "🐳"];
const AVATAR_BG = ["#FDE9E9", "#E8F0FD", "#E8F8EE", "#FFF4DE", "#F3E8FD", "#DFF6F6"];

const $ = (id) => document.getElementById(id);

const els = {
  authView: $("auth-view"), appView: $("app-view"),
  authTitle: $("auth-title"), authSub: $("auth-sub"),
  authEmail: $("auth-email"), authPassword: $("auth-password"),
  authError: $("auth-error"), authSubmit: $("auth-submit"),
  authSwitchText: $("auth-switch-text"), authSwitchBtn: $("auth-switch-btn"),
  authForm: $("auth-form"),
  sidebar: $("sidebar"), collapseBtn: $("collapse-btn"), mobileMenu: $("mobile-menu"),
  userEmail: $("user-email"), logoutBtn: $("logout-btn"),
  crumbPage: $("crumb-page"),
  searchToggle: $("search-toggle"), boardSearch: $("board-search"), sortBtn: $("sort-btn"),
  newTaskBtn: $("new-task-btn"), newTaskMenu: $("new-task-menu"),
  viewHome: $("view-home"), viewNotes: $("view-notes"),
  board: $("board"), timeline: $("timeline"),
  noteList: $("note-list"), searchInput: $("search-input"),
  newNoteBtn: $("new-note-btn"),
  editorEmpty: $("editor-empty"), editorPane: $("editor-pane"),
  noteTitle: $("note-title"), noteContent: $("note-content"),
  notePreview: $("note-preview"), todoProgress: $("todo-progress"),
  saveStatus: $("save-status"), modeToggle: $("mode-toggle"),
  deleteBtn: $("delete-btn"),
  floatStack: $("float-stack"),
  eventList: $("event-list"), addEventBtn: $("add-event-btn"),
  eventModal: $("event-modal"), eventForm: $("event-form"),
  eventTitle: $("event-title"), eventDesc: $("event-desc"),
  eventDate: $("event-date"), eventError: $("event-error"),
  eventCancel: $("event-cancel"), eventModalClose: $("event-modal-close"),
};

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  email: localStorage.getItem(EMAIL_KEY) || null,
  view: "home",
  notes: [], selectedId: null, mode: "edit",
  tasks: [], events: [], boardTab: "board", taskFilter: "",
  sortMode: "manual",
  dismissed: new Set(),
};

/* ---------------- API helpers ---------------- */

async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  let payload;
  if (form) {
    payload = new URLSearchParams(body);
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (state.token) headers["Authorization"] = "Bearer " + state.token;

  const resp = await fetch(API + path, { method, headers, body: payload });

  if (resp.status === 401 && state.token && !path.startsWith("/auth")) {
    logout();
    throw new Error("Session expired — please sign in again.");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const data = await resp.json();
      detail = Array.isArray(data.detail)
        ? data.detail.map((d) => d.msg).join(", ")
        : data.detail;
    } catch (_) { /* keep statusText */ }
    const err = new Error(detail || "Request failed");
    err.status = resp.status;
    throw err;
  }
  return resp.status === 204 ? null : resp.json();
}

/* ---------------- auth flow ---------------- */

function setAuthMode(signup) {
  state.isSignup = signup;
  els.authTitle.textContent = signup ? "Create your workspace" : "Welcome back";
  els.authSub.textContent = signup
    ? "A few seconds to your first task."
    : "Sign in to your workspace.";
  els.authSubmit.textContent = signup ? "Sign up" : "Sign in";
  els.authSwitchText.textContent = signup ? "Already have an account?" : "New here?";
  els.authSwitchBtn.textContent = signup ? "Sign in" : "Create an account";
  hideAuthError();
}

function showAuthError(message) {
  els.authError.textContent = message;
  els.authError.hidden = false;
}

function hideAuthError() { els.authError.hidden = true; }

async function handleAuth(event) {
  event.preventDefault();
  hideAuthError();
  els.authSubmit.disabled = true;
  try {
    const email = els.authEmail.value.trim();
    const password = els.authPassword.value;
    if (state.isSignup) {
      await api("/auth/signup", { method: "POST", body: { email, password } });
    }
    const data = await api("/auth/login", {
      method: "POST",
      form: true,
      body: { username: email, password },
    });
    state.token = data.access_token;
    state.email = email;
    localStorage.setItem(TOKEN_KEY, state.token);
    localStorage.setItem(EMAIL_KEY, email);
    await enterApp();
  } catch (err) {
    showAuthError(err.message || "Something went wrong.");
  } finally {
    els.authSubmit.disabled = false;
  }
}

function logout() {
  state.token = null;
  state.email = null;
  state.notes = [];
  state.tasks = [];
  state.selectedId = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  els.appView.hidden = true;
  els.authView.hidden = false;
  setAuthMode(false);
}

async function enterApp() {
  els.authView.hidden = true;
  els.appView.hidden = false;
  els.userEmail.textContent = state.email;
  switchView("home");
  await Promise.allSettled([loadTasks(), refreshNotes(), loadEvents()]);
}

/* ---------------- view switching ---------------- */

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".tab").forEach((tab) =>
    tab.classList.toggle("active", tab.dataset.view === view)
  );
  els.viewHome.hidden = view !== "home";
  els.viewNotes.hidden = view !== "notes";
  els.crumbPage.textContent = view === "home" ? "Home · Product" : "Notes";
  if (view === "home") renderBoard();
}

/* ---------------- notes logic (ported) ---------------- */

async function refreshNotes({ selectFirst = true } = {}) {
  const query = els.searchInput.value.trim();
  const path = "/notes" + (query ? "?search=" + encodeURIComponent(query) : "");
  state.notes = await api(path);
  renderList();
  const stillThere = state.selectedId !== null &&
    state.notes.some((note) => note.id === state.selectedId);
  if (stillThere) return;
  if (selectFirst) {
    selectNote(state.notes.length ? state.notes[0].id : null);
  }
}

function renderList() {
  els.noteList.textContent = "";
  if (!state.notes.length) {
    const empty = document.createElement("p");
    empty.className = "list-empty";
    empty.textContent = els.searchInput.value.trim()
      ? "No notes match your search."
      : "No notes yet — create one!";
    els.noteList.appendChild(empty);
    return;
  }
  for (const note of state.notes) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "note-item" + (note.id === state.selectedId ? " selected" : "");

    const title = document.createElement("span");
    title.className = "note-item-title";
    title.textContent = note.title || "Untitled";

    const meta = document.createElement("span");
    meta.className = "note-item-meta";
    meta.textContent = firstLine(note.content) || formatDate(note.updated_at);

    item.append(title, meta);
    item.addEventListener("click", () => selectNote(note.id));
    els.noteList.appendChild(item);
  }
}

function firstLine(text) {
  const line = (text || "").split("\n").find((l) => l.trim());
  return line ? line.trim().slice(0, 60) : "";
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function selectNote(id) {
  state.selectedId = id;
  setMode("edit");
  renderList();
  if (id === null) {
    els.editorEmpty.hidden = false;
    els.editorPane.hidden = true;
    return;
  }
  const note = state.notes.find((n) => n.id === id);
  if (!note) {
    els.editorEmpty.hidden = false;
    els.editorPane.hidden = true;
    return;
  }
  els.editorEmpty.hidden = true;
  els.editorPane.hidden = false;
  els.noteTitle.value = note.title;
  els.noteContent.value = note.content;
  els.saveStatus.textContent = "";
  updateTodoProgress();
}

let saveTimer = null;

function scheduleSave() {
  if (state.selectedId === null) return;
  els.saveStatus.textContent = "Saving…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNote, 800);
}

async function saveNote() {
  const id = state.selectedId;
  if (id === null) return;
  try {
    const updated = await api("/notes/" + id, {
      method: "PUT",
      body: {
        title: els.noteTitle.value.trim() || "Untitled",
        content: els.noteContent.value,
      },
    });
    const index = state.notes.findIndex((n) => n.id === id);
    if (index !== -1) state.notes[index] = updated;
    renderList();
    els.saveStatus.textContent = "Saved";
    updateTodoProgress();
  } catch (err) {
    els.saveStatus.textContent = "Error: " + err.message;
  }
}

async function createNote() {
  const note = await api("/notes", {
    method: "POST",
    body: { title: "Untitled", content: "" },
  });
  els.searchInput.value = "";
  state.notes.unshift(note);
  renderList();
  selectNote(note.id);
  els.noteTitle.focus();
  els.noteTitle.select();
}

async function deleteNote() {
  if (state.selectedId === null) return;
  if (!confirm("Delete this note? This cannot be undone.")) return;
  await api("/notes/" + state.selectedId, { method: "DELETE" });
  state.selectedId = null;
  await refreshNotes();
}

/* ---------------- note search & to-dos (ported) ---------------- */

let searchTimer = null;
els.searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const query = els.searchInput.value.trim();
    state.notes = await api(
      "/notes" + (query ? "?search=" + encodeURIComponent(query) : "")
    );
    renderList();
  }, 250);
});

function setMode(mode) {
  state.mode = mode;
  const preview = mode === "preview";
  els.notePreview.hidden = !preview;
  els.noteContent.hidden = preview;
  els.modeToggle.textContent = preview ? "Edit" : "Preview";
  if (preview) renderPreview();
}

function renderPreview() {
  const container = els.notePreview;
  container.textContent = "";
  const lines = els.noteContent.value.split("\n");

  lines.forEach((line, index) => {
    const todo = line.match(/^- \[([ xX])\] ?(.*)$/);
    if (todo) {
      const row = document.createElement("label");
      row.className = "todo-row";

      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = todo[1].toLowerCase() === "x";
      box.addEventListener("change", () => {
        lines[index] = `- [${box.checked ? "x" : " "}] ` + todo[2];
        els.noteContent.value = lines.join("\n");
        row.classList.toggle("done", box.checked);
        updateTodoProgress();
        scheduleSave();
      });

      const text = document.createElement("span");
      text.textContent = todo[2];

      row.append(box, text);
      if (box.checked) row.classList.add("done");
      container.appendChild(row);
    } else if (line.trim() === "") {
      const gap = document.createElement("div");
      gap.className = "preview-gap";
      container.appendChild(gap);
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = line;
      container.appendChild(paragraph);
    }
  });
}

function updateTodoProgress() {
  const todos = els.noteContent.value.match(/^- \[[ xX]\]/gm) || [];
  const done = todos.filter((t) => /[xX]/.test(t)).length;
  if (!todos.length) { els.todoProgress.hidden = true; return; }
  els.todoProgress.hidden = false;
  els.todoProgress.textContent = "";
  const label = document.createElement("span");
  label.textContent = `${done}/${todos.length} to-dos`;
  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("div");
  fill.className = "fill";
  fill.style.width = Math.round((done / todos.length) * 100) + "%";
  bar.appendChild(fill);
  els.todoProgress.append(label, bar);
}

/* ---------------- kanban board ---------------- */

async function loadTasks() {
  state.tasks = await api("/tasks");
  renderBoard();
  renderFloatStack();
}

function tasksInColumn(statusKey) {
  const inColumn = state.tasks.filter((t) => t.status === statusKey);
  return [...inColumn].sort((a, b) =>
    state.sortMode === "title"
      ? a.title.localeCompare(b.title)
      : a.position - b.position
  );
}

function avatarFor(task) {
  return {
    emoji: AVATARS[task.id % AVATARS.length],
    bg: AVATAR_BG[task.id % AVATAR_BG.length],
  };
}

function matchesFilter(task) {
  if (!state.taskFilter) return true;
  return task.title.toLowerCase().includes(state.taskFilter.toLowerCase());
}

function renderBoard() {
  els.board.textContent = "";
  for (const column of COLUMNS) {
    els.board.appendChild(buildColumn(column));
  }
}

function buildColumn(column) {
  const col = document.createElement("div");
  col.className = "column";
  col.dataset.status = column.key;

  const head = document.createElement("div");
  head.className = "column-head";
  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.style.background = column.color;
  const label = document.createElement("span");
  label.textContent = column.label;
  const count = document.createElement("span");
  count.className = "column-count";
  count.textContent = String(tasksInColumn(column.key).filter(matchesFilter).length);
  head.append(dot, label, count);

  const body = document.createElement("div");
  body.className = "column-body";
  for (const task of tasksInColumn(column.key).filter(matchesFilter)) {
    body.appendChild(buildCard(task));
  }
  body.appendChild(buildGhost(column.key));

  col.append(head, body);

  col.addEventListener("dragover", (event) => {
    event.preventDefault();
    col.classList.add("drop-target");
  });
  col.addEventListener("dragleave", (event) => {
    if (!col.contains(event.relatedTarget)) col.classList.remove("drop-target");
  });
  col.addEventListener("drop", (event) => {
    event.preventDefault();
    col.classList.remove("drop-target");
    const id = Number(event.dataTransfer.getData("text/plain"));
    if (!id) return;
    const cardEl = event.target.closest(".task-card");
    const beforeId =
      cardEl && Number(cardEl.dataset.id) !== id ? Number(cardEl.dataset.id) : null;
    moveTask(id, column.key, beforeId);
  });

  return col;
}

function buildCard(task) {
  const card = document.createElement("div");
  card.className = "task-card";
  card.draggable = true;
  card.dataset.id = task.id;

  const look = avatarFor(task);
  const avatar = document.createElement("span");
  avatar.className = "task-avatar";
  avatar.textContent = look.emoji;
  avatar.style.background = look.bg;

  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = task.title;

  const del = document.createElement("button");
  del.type = "button";
  del.className = "task-del";
  del.textContent = "✕";
  del.title = "Delete task";
  del.addEventListener("click", (event) => {
    event.stopPropagation();
    deleteTask(task.id);
  });

  card.append(avatar, title, del);
  card.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", String(task.id));
    event.dataTransfer.effectAllowed = "move";
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => card.classList.remove("dragging"));
  return card;
}

function buildGhost(statusKey) {
  const ghost = document.createElement("button");
  ghost.type = "button";
  ghost.className = "ghost-add";
  ghost.textContent = "+ New";
  ghost.addEventListener("click", () => {
    const input = document.createElement("input");
    input.className = "ghost-input";
    input.placeholder = "Task title, then Enter";
    ghost.replaceWith(input);
    input.focus();
    const finish = () => input.replaceWith(ghost);
    input.addEventListener("keydown", async (event) => {
      if (event.key === "Enter") {
        const title = input.value.trim();
        if (!title) { finish(); return; }
        try {
          await api("/tasks", { method: "POST", body: { title, status: statusKey } });
          await loadTasks();
        } catch (err) {
          console.error(err);
          finish();
        }
      } else if (event.key === "Escape") {
        finish();
      }
    });
    input.addEventListener("blur", finish);
  });
  return ghost;
}

function computePosition(statusKey, beforeId) {
  const column = tasksInColumn(statusKey);
  if (beforeId === null) {
    return column.length ? column[column.length - 1].position + 1024 : 1024;
  }
  const index = column.findIndex((t) => t.id === beforeId);
  if (index === -1) return 1024;
  const target = column[index];
  const previous = index > 0 ? column[index - 1] : null;
  return previous ? (previous.position + target.position) / 2 : target.position - 1024;
}

async function moveTask(id, statusKey, beforeId) {
  const position = computePosition(statusKey, beforeId);
  const task = state.tasks.find((t) => t.id === id);
  if (task && task.status === statusKey && task.position === position) return;

  // Optimistic move, then reconcile with the server.
  if (task) { task.status = statusKey; task.position = position; }
  renderBoard();
  try {
    await api("/tasks/" + id, { method: "PATCH", body: { status: statusKey, position } });
    await loadTasks();
  } catch (err) {
    console.error(err);
    await loadTasks();
  }
}

async function deleteTask(id) {
  if (!confirm("Delete this task?")) return;
  try {
    await api("/tasks/" + id, { method: "DELETE" });
    state.tasks = state.tasks.filter((t) => t.id !== id);
    renderBoard();
    renderFloatStack();
  } catch (err) {
    console.error(err);
  }
}

/* ---------------- timeline view ---------------- */

function setBoardTab(tab) {
  state.boardTab = tab;
  document.querySelectorAll(".ptab").forEach((button) =>
    button.classList.toggle("active", button.dataset.boardTab === tab)
  );
  els.board.hidden = tab !== "board";
  els.timeline.hidden = tab !== "timeline";
  if (tab === "timeline") renderTimeline();
}

function renderTimeline() {
  els.timeline.textContent = "";
  if (!state.tasks.length) {
    const empty = document.createElement("p");
    empty.className = "tl-empty";
    empty.textContent = "No tasks yet — add one from the board.";
    els.timeline.appendChild(empty);
    return;
  }
  for (const column of COLUMNS) {
    const items = tasksInColumn(column.key).filter(matchesFilter);
    if (!items.length) continue;

    const group = document.createElement("div");
    group.className = "tl-group";

    const head = document.createElement("div");
    head.className = "tl-head";
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.style.background = column.color;
    const label = document.createElement("span");
    label.textContent = `${column.label} · ${items.length}`;
    head.append(dot, label);
    group.appendChild(head);

    for (const task of items) {
      const row = document.createElement("div");
      row.className = "tl-row";

      const look = avatarFor(task);
      const avatar = document.createElement("span");
      avatar.className = "task-avatar";
      avatar.textContent = look.emoji;
      avatar.style.background = look.bg;

      const title = document.createElement("span");
      title.className = "task-title";
      title.textContent = task.title;

      const when = document.createElement("span");
      when.className = "tl-when";
      when.textContent = formatDate(task.updated_at);

      row.append(avatar, title, when);
      group.appendChild(row);
    }
    els.timeline.appendChild(group);
  }
}

/* ---------------- floating insight cards ---------------- */

function renderFloatStack() {
  els.floatStack.textContent = "";
  const count = (statusKey) => state.tasks.filter((t) => t.status === statusKey).length;

  const insights = [];
  if (!state.tasks.length) {
    insights.push({
      emoji: "✨", bg: AVATAR_BG[4],
      title: "Empty board",
      text: "Create your first task to get going.",
    });
  } else {
    const review = count("review");
    const doing = count("doing");
    const done = count("done");
    const todo = count("todo");
    if (review) {
      insights.push({
        emoji: "👀", bg: AVATAR_BG[1],
        title: `${review} task${review > 1 ? "s" : ""} in review`,
        text: "Worth a pass before shipping.",
      });
    }
    if (doing) {
      insights.push({
        emoji: "🔥", bg: AVATAR_BG[3],
        title: `${doing} in progress`,
        text: "Momentum looks good — keep going.",
      });
    }
    if (done) {
      insights.push({
        emoji: "🎉", bg: AVATAR_BG[2],
        title: `${done} completed`,
        text: "Nice work shipping these.",
      });
    }
    if (todo >= 3) {
      insights.push({
        emoji: "📝", bg: AVATAR_BG[0],
        title: `${todo} to-dos queued`,
        text: "Maybe pick the top one today?",
      });
    }
  }

  for (const item of insights.slice(0, 3)) {
    if (state.dismissed.has(item.title)) continue;
    els.floatStack.appendChild(buildFloatCard(item));
  }
}

function buildFloatCard(item) {
  const card = document.createElement("div");
  card.className = "float-card";

  const avatar = document.createElement("span");
  avatar.className = "float-avatar";
  avatar.textContent = item.emoji;
  avatar.style.background = item.bg;

  const text = document.createElement("div");
  text.className = "float-text";
  const heading = document.createElement("b");
  heading.textContent = item.title;
  const body = document.createElement("span");
  body.textContent = item.text;
  text.append(heading, body);

  const close = document.createElement("button");
  close.type = "button";
  close.className = "float-close";
  close.textContent = "✕";
  close.title = "Dismiss";
  close.addEventListener("click", () => {
    state.dismissed.add(item.title);
    card.remove();
  });

  card.append(avatar, text, close);
  return card;
}

/* ---------------- events (sidebar: Upcoming events) ---------------- */

async function loadEvents() {
  state.events = await api("/events");
  renderEvents();
}

function daysUntil(isoDate) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(isoDate + "T00:00:00");
  return Math.round((target - today) / 86400000);
}

function remainingLabel(days) {
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days < 0) return `${Math.abs(days)}d overdue`;
  return `in ${days}d`;
}

function formatDateOnly(isoDate) {
  return new Date(isoDate + "T00:00:00").toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function renderEvents() {
  els.eventList.textContent = "";
  const sorted = [...state.events].sort((a, b) => {
    if (a.event_date !== b.event_date) return a.event_date < b.event_date ? -1 : 1;
    return a.id - b.id;
  });

  if (!sorted.length) {
    const empty = document.createElement("p");
    empty.className = "event-empty";
    empty.textContent = "No events yet — click + to add one.";
    els.eventList.appendChild(empty);
    return;
  }
  for (const event of sorted) {
    els.eventList.appendChild(buildEventItem(event));
  }
}

function buildEventItem(event) {
  const days = daysUntil(event.event_date);

  const item = document.createElement("div");
  item.className = "event-item";

  const icon = document.createElement("span");
  icon.className = "si-icon";
  icon.textContent = "📅";

  const body = document.createElement("div");
  body.className = "event-body";

  const title = document.createElement("span");
  title.className = "event-title";
  title.textContent = event.title;
  if (event.description) title.title = event.description;

  const meta = document.createElement("div");
  meta.className = "event-meta";

  const when = document.createElement("span");
  when.textContent = formatDateOnly(event.event_date);

  const chip = document.createElement("span");
  chip.className =
    "chip" + (days === 0 || days === 1 ? " soon" : days < 0 ? " overdue" : "");
  chip.textContent = remainingLabel(days);

  meta.append(when, chip);
  body.append(title, meta);

  const del = document.createElement("button");
  del.type = "button";
  del.className = "event-del";
  del.textContent = "✕";
  del.title = "Delete event";
  del.addEventListener("click", async () => {
    if (!confirm(`Delete "${event.title}"?`)) return;
    try {
      await api("/events/" + event.id, { method: "DELETE" });
      await loadEvents();
    } catch (err) {
      console.error(err);
    }
  });

  item.append(icon, body, del);
  return item;
}

function openEventModal() {
  els.eventForm.reset();
  els.eventError.hidden = true;
  const today = new Date();
  const iso =
    `${today.getFullYear()}-` +
    `${String(today.getMonth() + 1).padStart(2, "0")}-` +
    `${String(today.getDate()).padStart(2, "0")}`;
  els.eventDate.value = iso;
  els.eventModal.hidden = false;
  els.eventTitle.focus();
}

function closeEventModal() {
  els.eventModal.hidden = true;
}

async function handleEventSubmit(event) {
  event.preventDefault();
  els.eventError.hidden = true;
  try {
    await api("/events", {
      method: "POST",
      body: {
        title: els.eventTitle.value.trim(),
        description: els.eventDesc.value.trim(),
        event_date: els.eventDate.value,
      },
    });
    closeEventModal();
    await loadEvents();
  } catch (err) {
    els.eventError.textContent = err.message || "Could not add event.";
    els.eventError.hidden = false;
  }
}

els.addEventBtn.addEventListener("click", openEventModal);
els.eventCancel.addEventListener("click", closeEventModal);
els.eventModalClose.addEventListener("click", closeEventModal);
els.eventModal.addEventListener("click", (event) => {
  if (event.target === els.eventModal) closeEventModal();
});
els.eventForm.addEventListener("submit", handleEventSubmit);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.eventModal.hidden) closeEventModal();
});

/* ---------------- wiring & init ---------------- */

els.authForm.addEventListener("submit", handleAuth);
els.authSwitchBtn.addEventListener("click", () => setAuthMode(!state.isSignup));
els.logoutBtn.addEventListener("click", logout);

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => switchView(tab.dataset.view))
);
document.querySelectorAll(".side-item[data-view]").forEach((item) =>
  item.addEventListener("click", () => switchView(item.dataset.view))
);
document.querySelectorAll(".group-head").forEach((head) =>
  head.addEventListener("click", () =>
    head.closest(".side-group").classList.toggle("open")
  )
);

els.collapseBtn.addEventListener("click", () => {
  els.sidebar.classList.toggle("collapsed");
  const collapsed = els.sidebar.classList.contains("collapsed");
  els.collapseBtn.textContent = collapsed ? "»" : "«";
  els.collapseBtn.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
});
els.mobileMenu.addEventListener("click", () => els.sidebar.classList.toggle("open"));

els.newNoteBtn.addEventListener("click", () => createNote().catch(showAuthError));
els.deleteBtn.addEventListener("click", () => deleteNote().catch(showAuthError));
els.modeToggle.addEventListener("click", () =>
  setMode(state.mode === "edit" ? "preview" : "edit")
);
els.noteTitle.addEventListener("input", scheduleSave);
els.noteContent.addEventListener("input", () => {
  updateTodoProgress();
  scheduleSave();
});
els.noteContent.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    clearTimeout(saveTimer);
    saveNote();
  }
});

els.searchToggle.addEventListener("click", () => {
  els.boardSearch.hidden = !els.boardSearch.hidden;
  if (!els.boardSearch.hidden) {
    els.boardSearch.focus();
  } else {
    state.taskFilter = "";
    els.boardSearch.value = "";
    renderBoard();
  }
});
els.boardSearch.addEventListener("input", () => {
  state.taskFilter = els.boardSearch.value.trim();
  renderBoard();
});
els.sortBtn.addEventListener("click", () => {
  state.sortMode = state.sortMode === "manual" ? "title" : "manual";
  els.sortBtn.title = "Sort: " + (state.sortMode === "manual" ? "manual order" : "title (A–Z)");
  renderBoard();
});
els.newTaskBtn.addEventListener("click", () => {
  els.newTaskMenu.hidden = !els.newTaskMenu.hidden;
});
els.newTaskMenu.querySelectorAll("button").forEach((button) =>
  button.addEventListener("click", async () => {
    els.newTaskMenu.hidden = true;
    try {
      await api("/tasks", {
        method: "POST",
        body: { title: "New task", status: button.dataset.status },
      });
      setBoardTab("board");
      await loadTasks();
    } catch (err) {
      console.error(err);
    }
  })
);
document.querySelectorAll(".ptab").forEach((button) =>
  button.addEventListener("click", () => setBoardTab(button.dataset.boardTab))
);
document.addEventListener("click", (event) => {
  if (!event.target.closest(".new-wrap")) els.newTaskMenu.hidden = true;
});

(async function init() {
  if (state.token) {
    try {
      await enterApp();
      return;
    } catch (_) { /* dead token — fall through to the login screen */ }
  }
  els.appView.hidden = true;
  els.authView.hidden = false;
  setAuthMode(false);
})();





