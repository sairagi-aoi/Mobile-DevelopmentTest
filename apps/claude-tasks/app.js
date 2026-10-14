/*
 * Claude Tasks — iPhone向けシンプルタスク管理
 * Built with Claude Code. Data is stored only in this browser's localStorage.
 */
(() => {
  "use strict";

  // Codex版アプリ（tiny-tasks:v1）と同じオリジンで動いてもデータが衝突しないよう、専用キーを使う。
  const STORAGE_KEY = "claude-tasks:v1";
  const DATA_VERSION = 1;
  const MAX_TITLE = 200;
  const MAX_NOTE = 2000;
  const TOAST_MS = 5000;

  const $ = (id) => document.getElementById(id);
  const els = {
    stats: $("stats"),
    list: $("list"),
    empty: $("empty"),
    clearDone: $("clear-done"),
    composer: $("composer"),
    newTitle: $("new-title"),
    editor: $("editor"),
    editorForm: $("editor-form"),
    editorCancel: $("editor-cancel"),
    editorDelete: $("editor-delete"),
    editTitle: $("edit-title"),
    editNote: $("edit-note"),
    editDone: $("edit-done"),
    toast: $("toast"),
    toastText: $("toast-text"),
    toastAction: $("toast-action"),
    filters: Array.from(document.querySelectorAll(".filter")),
  };

  let tasks = [];
  let filter = "active";
  let editingId = null;
  let toastTimer = null;
  let undoHandler = null;
  let newTaskId = null;

  // ---------- Storage ----------

  function normalizeTask(raw) {
    if (!raw || typeof raw !== "object") return null;
    const title = typeof raw.title === "string" ? raw.title.trim().slice(0, MAX_TITLE) : "";
    if (!title || typeof raw.id !== "string") return null;
    const now = Date.now();
    return {
      id: raw.id,
      title,
      note: typeof raw.note === "string" ? raw.note.slice(0, MAX_NOTE) : "",
      done: raw.done === true,
      createdAt: Number.isFinite(raw.createdAt) ? raw.createdAt : now,
      updatedAt: Number.isFinite(raw.updatedAt) ? raw.updatedAt : now,
      completedAt: Number.isFinite(raw.completedAt) ? raw.completedAt : null,
    };
  }

  function readStorage() {
    let raw;
    try {
      raw = localStorage.getItem(STORAGE_KEY);
    } catch {
      return { tasks: [], error: "ストレージを利用できません。プライベートブラウズでは保存されない場合があります。" };
    }
    if (raw === null) return { tasks: [] };
    try {
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.tasks)) throw new Error("invalid shape");
      return { tasks: data.tasks.map(normalizeTask).filter(Boolean) };
    } catch {
      // 壊れたデータは消さずに退避してから、新しいリストで始める。
      const backupKey = `${STORAGE_KEY}:corrupt-${Date.now()}`;
      try {
        localStorage.setItem(backupKey, raw);
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* 退避できなくても起動は続ける */
      }
      return { tasks: [], error: `保存データを読み込めなかったため、${backupKey} に退避しました。` };
    }
  }

  function writeStorage(nextTasks) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: DATA_VERSION, tasks: nextTasks }));
  }

  /** 保存に成功した場合のみ状態を更新する。失敗時は画面も元のまま。 */
  function commit(nextTasks) {
    try {
      writeStorage(nextTasks);
    } catch {
      showToast("保存できませんでした。空き容量やブラウザーの設定を確認してください。");
      return false;
    }
    tasks = nextTasks;
    render();
    return true;
  }

  // ---------- Operations ----------

  function createId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function addTask(title) {
    const clean = title.trim().slice(0, MAX_TITLE);
    if (!clean) return false;
    const now = Date.now();
    const task = { id: createId(), title: clean, note: "", done: false, createdAt: now, updatedAt: now, completedAt: null };
    newTaskId = task.id;
    const ok = commit([task, ...tasks]);
    newTaskId = null;
    return ok;
  }

  function updateTask(id, changes) {
    const now = Date.now();
    return commit(tasks.map((task) => {
      if (task.id !== id) return task;
      const next = { ...task, ...changes, updatedAt: now };
      if (changes.done !== undefined && changes.done !== task.done) {
        next.completedAt = changes.done ? now : null;
      }
      return next;
    }));
  }

  function toggleTask(id) {
    const task = tasks.find((t) => t.id === id);
    if (task) updateTask(id, { done: !task.done });
  }

  function removeTasks(predicate, message) {
    const before = tasks;
    const removed = tasks.filter(predicate);
    if (removed.length === 0) return;
    if (commit(tasks.filter((t) => !predicate(t)))) {
      showToast(message, () => {
        // 削除後に追加されたタスクを残したまま、削除したものを元に戻す。
        const removedIds = new Set(removed.map((t) => t.id));
        const beforeIds = new Set(before.map((t) => t.id));
        const currentById = new Map(tasks.map((t) => [t.id, t]));
        const restored = before.flatMap((t) => {
          if (currentById.has(t.id)) return [currentById.get(t.id)];
          return removedIds.has(t.id) ? [t] : [];
        });
        const extra = tasks.filter((t) => !beforeIds.has(t.id));
        if (commit([...extra, ...restored])) showToast("元に戻しました");
      });
    }
  }

  // ---------- Rendering ----------

  function visibleTasks() {
    const active = tasks.filter((t) => !t.done);
    const done = tasks.filter((t) => t.done).sort((a, b) => (b.completedAt || 0) - (a.completedAt || 0));
    if (filter === "active") return active;
    if (filter === "done") return done;
    return [...active, ...done];
  }

  const CHECK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

  function renderTask(task) {
    const li = document.createElement("li");
    li.className = `task${task.done ? " is-done" : ""}${task.id === newTaskId ? " is-new" : ""}`;
    li.dataset.id = task.id;

    const check = document.createElement("button");
    check.type = "button";
    check.className = "check";
    check.dataset.action = "toggle";
    check.setAttribute("role", "checkbox");
    check.setAttribute("aria-checked", String(task.done));
    check.setAttribute("aria-label", `「${task.title}」を完了にする`);
    check.innerHTML = `<span class="check-mark">${CHECK_SVG}</span>`;

    const body = document.createElement("button");
    body.type = "button";
    body.className = "task-body";
    body.dataset.action = "edit";
    body.setAttribute("aria-label", `「${task.title}」を編集`);

    const title = document.createElement("span");
    title.className = "task-title";
    title.textContent = task.title;
    body.append(title);

    if (task.note) {
      const note = document.createElement("span");
      note.className = "task-note";
      note.textContent = task.note;
      body.append(note);
    }

    li.append(check, body);
    return li;
  }

  function render() {
    const items = visibleTasks();
    els.list.replaceChildren(...items.map(renderTask));

    const total = tasks.length;
    const doneCount = tasks.filter((t) => t.done).length;
    const activeCount = total - doneCount;
    els.stats.textContent = total === 0
      ? "タスクはまだありません"
      : `残り ${activeCount} 件 · 完了 ${doneCount} / ${total}`;

    els.empty.hidden = items.length > 0;
    if (items.length === 0) {
      els.empty.textContent = {
        active: total === 0 ? "下の入力欄からタスクを追加しましょう" : "すべて完了しました 🎉",
        done: "完了したタスクはありません",
        all: "下の入力欄からタスクを追加しましょう",
      }[filter];
    }

    els.clearDone.hidden = !(doneCount > 0 && filter !== "active");

    for (const button of els.filters) {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    }
  }

  // ---------- Toast ----------

  function showToast(message, onUndo) {
    clearTimeout(toastTimer);
    els.toastText.textContent = message;
    undoHandler = onUndo || null;
    els.toastAction.hidden = !undoHandler;
    els.toast.hidden = false;
    toastTimer = setTimeout(hideToast, TOAST_MS);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    els.toast.hidden = true;
    undoHandler = null;
  }

  // ---------- Editor ----------

  function openEditor(id) {
    const task = tasks.find((t) => t.id === id);
    if (!task) return;
    editingId = id;
    els.editTitle.value = task.title;
    els.editNote.value = task.note;
    els.editDone.checked = task.done;
    if (typeof els.editor.showModal === "function") {
      els.editor.showModal();
    } else {
      els.editor.setAttribute("open", "");
    }
  }

  function closeEditor() {
    editingId = null;
    if (els.editor.open) els.editor.close();
  }

  // ---------- Events ----------

  els.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    if (addTask(els.newTitle.value)) {
      els.newTitle.value = "";
      if (filter === "done") {
        filter = "active";
        render();
      }
    }
    // キーボードを閉じずに続けて入力できるようにする。
    els.newTitle.focus();
  });

  els.list.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const id = button.closest(".task").dataset.id;
    if (button.dataset.action === "toggle") toggleTask(id);
    if (button.dataset.action === "edit") openEditor(id);
  });

  for (const button of els.filters) {
    button.addEventListener("click", () => {
      filter = button.dataset.filter;
      render();
    });
  }

  els.clearDone.addEventListener("click", () => {
    const count = tasks.filter((t) => t.done).length;
    removeTasks((t) => t.done, `完了したタスク ${count} 件を削除しました`);
  });

  els.editorForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const title = els.editTitle.value.trim();
    if (!title) {
      els.editTitle.value = "";
      els.editTitle.reportValidity();
      return;
    }
    const ok = updateTask(editingId, {
      title: title.slice(0, MAX_TITLE),
      note: els.editNote.value.slice(0, MAX_NOTE).replace(/\s+$/, ""),
      done: els.editDone.checked,
    });
    if (ok) closeEditor();
  });

  els.editorCancel.addEventListener("click", closeEditor);
  els.editor.addEventListener("close", () => { editingId = null; });

  // 背景（::backdrop）をタップしたら閉じる。
  els.editor.addEventListener("click", (event) => {
    if (event.target === els.editor) closeEditor();
  });

  els.editorDelete.addEventListener("click", () => {
    const id = editingId;
    const task = tasks.find((t) => t.id === id);
    if (!task) return closeEditor();
    closeEditor();
    removeTasks((t) => t.id === id, `「${task.title}」を削除しました`);
  });

  els.toastAction.addEventListener("click", () => {
    const handler = undoHandler;
    hideToast();
    if (handler) handler();
  });

  // 同じアプリを開いている別タブの変更を反映する。
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    tasks = readStorage().tasks;
    if (editingId && !tasks.some((t) => t.id === editingId)) closeEditor();
    render();
  });

  // ---------- Init ----------

  const initial = readStorage();
  tasks = initial.tasks;
  render();
  if (initial.error) showToast(initial.error);
})();
