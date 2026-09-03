"use strict";

/* ============================================================
   Workspace dashboard — talks to the FastAPI backend on the
   same origin. Views: Home (kanban) + Notes (editor).
   All user data rendered via textContent (XSS-safe).
   ============================================================ */

const API = window.location.origin;
const TOKEN_KEY = "notes_token";
const EMAIL_KEY = "notes_email";
const THEME_KEY = "notes_theme";

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
  authResendLine: $("auth-resend-line"), authResendBtn: $("auth-resend-btn"),
  forgotPasswordBtn: $("forgot-password-btn"),
  forgotForm: $("forgot-form"), forgotEmail: $("forgot-email"),
  forgotError: $("forgot-error"), forgotSubmit: $("forgot-submit"),
  forgotBackBtn: $("forgot-back-btn"),
  authSwitchText: $("auth-switch-text"), authSwitchBtn: $("auth-switch-btn"),
  authSwitch: $("auth-switch"),
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
  deleteBtn: $("delete-btn"), favoriteBtn: $("favorite-btn"),
  notesStats: $("notes-stats"), noteMeta: $("note-meta"), noteStats: $("note-stats"),
  floatStack: $("float-stack"),
  eventList: $("event-list"), addEventBtn: $("add-event-btn"),
  eventModal: $("event-modal"), eventForm: $("event-form"),
  eventTitle: $("event-title"), eventDesc: $("event-desc"),
  eventDate: $("event-date"), eventError: $("event-error"),
  eventCancel: $("event-cancel"), eventModalClose: $("event-modal-close"),
  verifyBanner: $("verify-banner"), resendVerifyBtn: $("resend-verify-btn"),
  themeToggle: $("theme-toggle"), themeToggleAuth: $("theme-toggle-auth"),
};

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  email: localStorage.getItem(EMAIL_KEY) || null,
  view: "home",
  notes: [], selectedId: null, mode: "edit",
  tasks: [], events: [], boardTab: "board", taskFilter: "",
  isVerified: true,
  sortMode: "manual",
  dismissed: new Set(),
  resendEmail: null,
};

/* ---------------- API helpers ---------------- */

async function api(path, { method = "GET", body, form, signal } = {}) {
  const headers = {};
  let payload;
  if (form) {
    payload = new URLSearchParams(body);
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  if (state.token) headers["Authorization"] = "Bearer " + state.token;

  const resp = await fetch(API + path, { method, headers, body: payload, signal });

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

/* ---------------- theme (dark mode) ----------------
   Preference precedence: explicit choice in localStorage ("light"/"dark")
   → OS prefers-color-scheme. The <head> inline script already set
   data-theme pre-paint; this module keeps the toggle icons in sync,
   handles clicks, and follows OS changes while no choice is stored. */

const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

function effectiveTheme() {
  let stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (_) { /* private mode */ }
  if (stored === "light" || stored === "dark") return stored;
  return systemDark.matches ? "dark" : "light";
}

function applyTheme() {
  const theme = effectiveTheme();
  document.documentElement.dataset.theme = theme;
  const icon = theme === "dark" ? "☀️" : "🌙";
  const title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  for (const btn of [els.themeToggle, els.themeToggleAuth]) {
    if (!btn) continue;
    btn.textContent = icon;
    btn.title = title;
  }
}

function toggleTheme() {
  try { localStorage.setItem(THEME_KEY, effectiveTheme() === "dark" ? "light" : "dark"); } catch (_) {}
  // Cross-fade the palette instead of snapping (class removed after the animation).
  const root = document.documentElement;
  root.classList.add("theme-anim");
  clearTimeout(toggleTheme._timer);
  toggleTheme._timer = setTimeout(() => root.classList.remove("theme-anim"), 240);
  applyTheme();
}

// Follow OS switches live until the user makes an explicit choice.
systemDark.addEventListener("change", () => {
  let stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (_) { /* private mode */ }
  if (stored !== "light" && stored !== "dark") applyTheme();
});

els.themeToggle.addEventListener("click", toggleTheme);
els.themeToggleAuth.addEventListener("click", toggleTheme);
applyTheme();

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
  els.forgotForm.hidden = true;
  hideAuthError();
  hideResendLine();
}

function showAuthError(message) {
  els.authError.textContent = message;
  els.authError.hidden = false;
}

function hideAuthError() { els.authError.hidden = true; }

function hideResendLine() { els.authResendLine.hidden = true; }

function showResendLine() { els.authResendLine.hidden = false; }

async function handleAuth(event) {
  event.preventDefault();
  hideAuthError();
  hideResendLine();
  els.authSubmit.disabled = true;
  try {
    const email = els.authEmail.value.trim();
    const password = els.authPassword.value;

    if (state.isSignup) {
      await api("/auth/signup", { method: "POST", body: { email, password } });
      // Account created — but login is hard-gated until the user verifies
      // their email, so don't attempt an auto-login that will 401. Flip back
      // to the login form with a clear, friendly message.
      els.authForm.reset();
      setAuthMode(false);
      showAuthError(
        "Account created! Check your inbox for the verification link — click it, then sign in."
      );
      return;
    }

    try {
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
      // If the account exists but isn't verified, offer a one-click resend
      // right here on the login screen (the in-app resend would require login,
      // which is blocked — a dead-end otherwise).
      if (err.status === 401 && /not verified/i.test(err.message || "")) {
        state.resendEmail = email;
        showAuthError(err.message);
        showResendLine();
      } else {
        throw err;
      }
    }
  } catch (err) {
    showAuthError(err.message || "Something went wrong.");
  } finally {
    els.authSubmit.disabled = false;
  }
}

async function handleResendVerification() {
  const email = state.resendEmail || els.authEmail.value.trim();
  els.authResendBtn.disabled = true;
  try {
    await api("/auth/resend-verification-email", {
      method: "POST",
      body: { email },
    });
    showAuthError("Verification link sent — check your inbox (and spam).");
    hideResendLine();
  } catch (err) {
    showAuthError(err.message || "Could not send the link right now.");
  } finally {
    els.authResendBtn.disabled = false;
  }
}

function openForgotForm() {
  hideAuthError();
  hideResendLine();
  els.authForm.hidden = true;
  els.authSwitch.hidden = true;
  els.forgotForm.hidden = false;
  els.forgotForm.reset();
  els.forgotEmail.focus();
}

function closeForgotForm() {
  els.forgotForm.hidden = true;
  els.authForm.hidden = false;
  els.authSwitch.hidden = false;
  hideAuthError();
}

async function handleForgotPassword(event) {
  event.preventDefault();
  els.forgotError.hidden = true;
  els.forgotSubmit.disabled = true;
  try {
    await api("/auth/forgot-password", {
      method: "POST",
      body: { email: els.forgotEmail.value.trim() },
    });
    els.forgotError.textContent =
      "If that email exists, a reset link has been sent. Check your inbox (and spam).";
    els.forgotError.classList.add("auth-hint");
    els.forgotError.hidden = false;
  } catch (err) {
    els.forgotError.textContent = err.message || "Something went wrong.";
    els.forgotError.classList.remove("auth-hint");
    els.forgotError.hidden = false;
  } finally {
    els.forgotSubmit.disabled = false;
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
  await checkVerification();
}

async function checkVerification() {
  try {
    const me = await api("/auth/me");
    state.isVerified = me.is_verified === true;
  } catch (_) {
    state.isVerified = true; // unknown → don't nag
  }
  els.verifyBanner.hidden = state.isVerified;
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
    const empty = document.createElement("div");
    empty.className = "list-empty";
    const illus = document.createElement("div");
    illus.className = "empty-illustration";
    illus.textContent = "📝";
    const msg = document.createElement("p");
    msg.textContent = els.searchInput.value.trim()
      ? "No notes match your search."
      : "No notes yet — create one!";
    empty.append(illus, msg);
    els.noteList.appendChild(empty);
    return;
  }

  // Update notes stats
  els.notesStats.textContent = `${state.notes.length} note${state.notes.length !== 1 ? "s" : ""}`;

  // Favorites float to the top (stable — keeps recency order inside each group)
  const ordered = [...state.notes].sort(
    (a, b) => (b.favorite === true) - (a.favorite === true)
  );

  for (const note of ordered) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "note-item" + (note.id === state.selectedId ? " selected" : "");

    // Category dot based on content patterns
    const category = detectCategory(note.title || "", note.content || "");
    const dot = document.createElement("span");
    dot.className = `note-category-dot category-${category}`;
    dot.style.background = categoryColors[category];
    dot.title = category === "default" ? "Note" : category[0].toUpperCase() + category.slice(1);

    // Note content wrapper
    const contentWrap = document.createElement("div");
    contentWrap.className = "note-item-content";

    const title = document.createElement("span");
    title.className = "note-item-title";
    title.textContent = note.title || "Untitled";

    const meta = document.createElement("span");
    meta.className = "note-item-meta";
    meta.textContent = firstLine(note.content) || formatDate(note.updated_at);

    // Word count subtitle
    const wordCount = countWords(note.content);
    const wordCountSpan = document.createElement("span");
    wordCountSpan.className = "note-item-word-count";
    wordCountSpan.textContent = wordCount === 1 ? "1 word" : `${wordCount} words`;

    contentWrap.append(title, meta, wordCountSpan);

    // Favorite star (visible always for favorites; on hover otherwise)
    const favorite = document.createElement("span");
    favorite.className = "note-favorite" + (note.favorite ? " active" : "");
    favorite.textContent = note.favorite ? "⭐" : "☆";
    favorite.title = note.favorite ? "Remove from favorites" : "Add to favorites";
    favorite.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleFavorite(note.id);
    });

    item.append(dot, contentWrap, favorite);
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

/* Category detection based on content keywords */
const categoryColors = {
  work: "#2f80ed",
  personal: "#8b5cf6",
  ideas: "#22c55e",
  learning: "#f59e0b",
  default: "#6b7280",
};

const categoryKeywords = {
  work: ["meeting", "project", "task", "deadline", "report", "email", "client", "review", "sprint", "roadmap"],
  personal: ["family", "friends", "birthday", "vacation", "holiday", "personal", "home"],
  ideas: ["idea", "brainstorm", "concept", "innovation", "creative", "think", "wonder"],
  learning: ["learn", "tutorial", "course", "study", "research", "how to", "guide", "documentation"],
};

function detectCategory(title, content) {
  const text = (title + " " + content).toLowerCase();
  for (const [category, keywords] of Object.entries(categoryKeywords)) {
    if (keywords.some(keyword => text.includes(keyword))) {
      return category;
    }
  }
  return "default";
}

/* Word count helper */
function countWords(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return 0;
  return trimmed.split(/\s+/).length;
}

/* Toggle favorite status for a note */
async function toggleFavorite(id) {
  try {
    const note = state.notes.find((n) => n.id === id);
    if (!note) return;
    const updated = await api(`/notes/${id}/favorite`, {
      method: "PUT",
      body: { favorite: !note.favorite },
    });
    const index = state.notes.findIndex((n) => n.id === id);
    if (index !== -1) state.notes[index] = updated;
    renderList();
    // If this is the selected note, refresh the toolbar star + meta row
    if (state.selectedId === id && !els.editorPane.hidden) {
      syncFavoriteButton(updated);
      updateNoteMeta(updated);
    }
  } catch (err) {
    console.error("Failed to toggle favorite:", err);
  }
}

/* Keep the editor toolbar's favorite button in sync with the note state */
function syncFavoriteButton(note) {
  if (!els.favoriteBtn) return;
  els.favoriteBtn.textContent = note.favorite ? "★ Favorited" : "☆ Favorite";
  els.favoriteBtn.classList.toggle("fav-active", !!note.favorite);
  els.favoriteBtn.title = note.favorite
    ? "Remove from favorites"
    : "Add to favorites";
}

/* Update note meta row in the editor */
function updateNoteMeta(note) {
  if (!els.noteMeta || !els.noteStats) return;
  const wordCount = countWords(note.content);
  els.noteStats.textContent = `${wordCount} ${wordCount === 1 ? 'word' : 'words'} · Updated ${formatDate(note.updated_at)}`;
  els.noteMeta.hidden = false;
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
  els.saveStatus.classList.remove("saved");
  updateTodoProgress();
  // Show note metadata (word count, last updated) + favorite state
  syncFavoriteButton(note);
  updateNoteMeta(note);
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
    els.saveStatus.textContent = "✓ Saved";
    els.saveStatus.classList.add("saved");
    // Update note meta with new stats
    updateNoteMeta(updated);
    setTimeout(() => {
      els.saveStatus.textContent = "";
      els.saveStatus.classList.remove("saved");
      updateTodoProgress();
    }, 3000);
  } catch (err) {
    els.saveStatus.textContent = "✗ Failed to save";
    els.saveStatus.classList.add("saved");
    setTimeout(() => {
      els.saveStatus.textContent = "";
      els.saveStatus.classList.remove("saved");
    }, 3000);
    showAuthError(err.message || "Couldn't save note.");
    console.error(err);
  }
}

async function createNote() {
  const note = await api("/notes", {
    method: "POST",
    body: { title: "Untitled note", content: "" },
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
els.forgotPasswordBtn.addEventListener("click", openForgotForm);
els.forgotBackBtn.addEventListener("click", closeForgotForm);
els.forgotForm.addEventListener("submit", handleForgotPassword);
els.authResendBtn.addEventListener("click", handleResendVerification);
els.logoutBtn.addEventListener("click", logout);
els.resendVerifyBtn.addEventListener("click", async () => {
  try {
    await api("/auth/resend-verification", { method: "POST" });
    els.resendVerifyBtn.textContent = "Sent ✓";
    setTimeout(() => { els.resendVerifyBtn.textContent = "Resend email"; }, 3000);
  } catch (err) {
    console.error(err);
    els.resendVerifyBtn.textContent = "Please wait a minute";
    setTimeout(() => { els.resendVerifyBtn.textContent = "Resend email"; }, 3000);
  }
});

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
els.mobileMenu.addEventListener("click", () => {
  // On mobile, toggle the 'open' class (controls visibility via transform)
  els.sidebar.classList.toggle("open");
});

// Close mobile sidebar when clicking outside
document.addEventListener("click", (e) => {
  if (window.innerWidth <= 820 && !els.sidebar.contains(e.target) && !els.mobileMenu.contains(e.target)) {
    els.sidebar.classList.remove("open");
  }
});

// Close mobile sidebar on resize to desktop
window.addEventListener("resize", () => {
  if (window.innerWidth > 820) {
    els.sidebar.classList.remove("open");
    els.sidebar.classList.remove("collapsed");
  }
});

els.newNoteBtn.addEventListener("click", () => createNote().catch(showAuthError));
els.deleteBtn.addEventListener("click", () => deleteNote().catch(showAuthError));
els.favoriteBtn.addEventListener("click", () => {
  if (state.selectedId !== null) toggleFavorite(state.selectedId);
});
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
  // Sign in with Google: the OAuth callback redirects here with ?google_token=…
  const params = new URLSearchParams(window.location.search);
  const googleToken = params.get("google_token");
  const googleEmail = params.get("email");
  const googleError = params.get("error");
  if (googleToken) {
    state.token = googleToken;
    state.email = googleEmail || "";
    localStorage.setItem(TOKEN_KEY, state.token);
    localStorage.setItem(EMAIL_KEY, state.email);
    // Clean the token out of the address bar so it isn't shared with anyone.
    window.history.replaceState({}, "", window.location.pathname);
    try {
      await enterApp();
      return;
    } catch (_) { /* bad token — fall through to the login screen */ }
  }

  if (state.token) {
    try {
      await enterApp();
      return;
    } catch (_) { /* dead token — fall through to the login screen */ }
  }
  els.appView.hidden = true;
  els.authView.hidden = false;
  setAuthMode(false);

  if (googleError) {
    const messages = {
      "google-auth-denied": "Google sign-in was cancelled — try again or use email & password.",
      "google-auth-failed": "Google sign-in failed. Please try again.",
      "google-email-not-verified": "That Google account's email couldn't be verified.",
    };
    showAuthError(messages[googleError] || "Google sign-in didn't complete. Please try again.");
  }
})();





/* ============================================================
   AI assistant — sidebar chat panel (Agents & tools)
   Talks to POST /ai/chat; delete proposals render as cards with
   Confirm / Cancel buttons wired to /ai/actions/{id}/confirm|cancel.
   All text rendered via textContent (XSS-safe, same as the rest).
   ============================================================ */

const aiEls = {
  btn: $("ai-chat-btn"), panel: $("ai-panel"), close: $("ai-close"),
  messages: $("ai-messages"), notice: $("ai-notice"),
  form: $("ai-form"), input: $("ai-input"), send: $("ai-send"),
};

const aiState = { status: null, busy: false };

async function aiEnsureStatus() {
  if (aiState.status) return aiState.status;
  try {
    aiState.status = await api("/ai/status");
  } catch (_) {
    aiState.status = { enabled: false, provider: "off" };
  }
  if (!aiState.status.enabled) {
    aiEls.notice.textContent =
      "The AI assistant is disabled on this server. Set AI_ENABLED=true and " +
      "configure an AI_PROVIDER / MISTRAL_API_KEY to enable it.";
    aiEls.notice.hidden = false;
    aiEls.input.disabled = true;
    aiEls.send.disabled = true;
  }
  return aiState.status;
}

function aiOpen() {
  aiEls.panel.hidden = false;
  aiEnsureStatus();
  aiEls.input.focus();
}

function aiClose() {
  aiEls.panel.hidden = true;
}

function aiBubble(text, who) {
  const div = document.createElement("div");
  div.className = "ai-msg " + (who === "user" ? "ai-msg-user" : "ai-msg-bot");
  div.textContent = text;
  aiEls.messages.appendChild(div);
  aiEls.messages.scrollTop = aiEls.messages.scrollHeight;
  return div;
}

function aiThinking() {
  const div = aiBubble("Thinking…", "bot");
  div.classList.add("ai-thinking");
  return div;
}

function aiRefreshData() {
  // Mirror AI-made changes into the open views (tasks/notes/events).
  if (typeof loadTasks === "function") loadTasks();
  if (typeof loadEvents === "function") loadEvents();
  if (state.view === "notes" && typeof refreshNotes === "function") {
    refreshNotes({ selectFirst: false });
  }
}

function aiProposalCard(action) {
  const card = document.createElement("div");
  card.className = "ai-proposal";

  const label = document.createElement("p");
  label.className = "ai-proposal-text";
  label.textContent = action.summary;
  card.appendChild(label);

  const row = document.createElement("div");
  row.className = "ai-proposal-actions";

  const okBtn = document.createElement("button");
  okBtn.className = "btn btn-primary btn-sm";
  okBtn.type = "button";
  okBtn.textContent = "Confirm";
  const noBtn = document.createElement("button");
  noBtn.className = "btn btn-sm";
  noBtn.type = "button";
  noBtn.textContent = "Cancel";
  row.appendChild(okBtn);
  row.appendChild(noBtn);
  card.appendChild(row);

  async function settle(path, okText) {
    okBtn.disabled = true;
    noBtn.disabled = true;
    try {
      const res = await api(`/ai/actions/${action.action_id}/${path}`, { method: "POST" });
      aiBubble(okText(res), "bot");
      aiRefreshData();
    } catch (err) {
      aiBubble("⚠️ " + err.message, "bot");
      okBtn.disabled = false;
      noBtn.disabled = false;
    }
  }

  okBtn.addEventListener("click", () =>
    settle("confirm", (r) => "✅ " + (r.summary || "Done — the deletion was executed."))
  );
  noBtn.addEventListener("click", () =>
    settle("cancel", () => "Cancelled — nothing was deleted.")
  );

  aiEls.messages.appendChild(card);
  aiEls.messages.scrollTop = aiEls.messages.scrollHeight;
}

async function aiSend(event) {
  event.preventDefault();
  if (aiState.busy) return;
  const text = aiEls.input.value.trim();
  if (!text) return;
  aiEls.input.value = "";
  aiBubble(text, "user");
  aiState.busy = true;
  aiEls.send.disabled = true;
  const thinking = aiThinking();
  // Abort if the server never answers — otherwise aiState.busy would stick
  // and every later Enter press would silently do nothing.
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);
  try {
    const res = await api("/ai/chat", {
      method: "POST",
      body: { message: text },
      signal: controller.signal,
    });
    thinking.remove();
    for (const action of res.actions || []) {
      if (action.status === "proposed" && action.action_id != null) {
        aiProposalCard(action);
      } else if (action.summary) {
        const icon = action.status === "cancelled" ? "↩️" : "✅";
        aiBubble(`${icon} ${action.summary}`, "bot");
      }
    }
    aiBubble(res.reply || "…", "bot");
    if ((res.actions || []).some((a) => a.status === "executed")) aiRefreshData();
  } catch (err) {
    thinking.remove();
    aiBubble(
      err.name === "AbortError"
        ? "⚠️ The assistant took too long to answer — please try again."
        : "⚠️ " + err.message,
      "bot"
    );
  } finally {
    clearTimeout(timeout);
    aiState.busy = false;
    aiEls.send.disabled = false;
    aiEls.input.focus();
  }
}

aiEls.btn.addEventListener("click", aiOpen);
aiEls.close.addEventListener("click", aiClose);
aiEls.form.addEventListener("submit", aiSend);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !aiEls.panel.hidden) aiClose();
});

