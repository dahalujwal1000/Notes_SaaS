"use strict";

/* ============================================================
   Notes — frontend logic.
   Talks to the FastAPI backend on the same origin: JWT auth,
   notes CRUD, debounced autosave, Notion-style to-dos.
   All user data is rendered via textContent (XSS-safe).
   ============================================================ */

const API = window.location.origin;
const TOKEN_KEY = "notes_token";
const EMAIL_KEY = "notes_email";

const $ = (id) => document.getElementById(id);

const els = {
  authView: $("auth-view"), appView: $("app-view"),
  authTitle: $("auth-title"), authSub: $("auth-sub"),
  authEmail: $("auth-email"), authPassword: $("auth-password"),
  authError: $("auth-error"), authSubmit: $("auth-submit"),
  authSwitchText: $("auth-switch-text"), authSwitchBtn: $("auth-switch-btn"),
  authForm: $("auth-form"),
  noteList: $("note-list"), searchInput: $("search-input"),
  newNoteBtn: $("new-note-btn"), logoutBtn: $("logout-btn"),
  userEmail: $("user-email"),
  editorEmpty: $("editor-empty"), editorPane: $("editor-pane"),
  noteTitle: $("note-title"), noteContent: $("note-content"),
  notePreview: $("note-preview"), todoProgress: $("todo-progress"),
  saveStatus: $("save-status"), modeToggle: $("mode-toggle"),
  deleteBtn: $("delete-btn"), mobileMenu: $("mobile-menu"),
  sidebar: $("sidebar"),
};

const state = {
  token: localStorage.getItem(TOKEN_KEY) || null,
  email: localStorage.getItem(EMAIL_KEY) || null,
  notes: [],
  selectedId: null,
  mode: "edit",     // "edit" | "preview"
  isSignup: false,
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

  // Expired/invalid token mid-session: drop to the login screen.
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
    ? "A few seconds to your first note."
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
  await refreshNotes();
}

/* ---------------- notes list ---------------- */

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

/* ---------------- editor ---------------- */

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

/* ---------------- search ---------------- */

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

/* ---------------- to-dos & preview ---------------- */

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

/* ---------------- wiring & init ---------------- */

els.authForm.addEventListener("submit", handleAuth);
els.authSwitchBtn.addEventListener("click", () => setAuthMode(!state.isSignup));
els.logoutBtn.addEventListener("click", logout);
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
els.mobileMenu.addEventListener("click", () => els.sidebar.classList.toggle("open"));

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


