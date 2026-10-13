(() => {
  "use strict";

  const STORAGE_KEY = "tiny-tasks:v1";
  const MAX_TITLE_LENGTH = 200;
  const elements = Object.fromEntries([
    "today", "remaining-count", "progress-label", "progress", "summary-message",
    "storage-error", "task-count", "task-list", "empty-state", "empty-title",
    "empty-copy", "list-hint", "add-form", "new-task", "add-error", "edit-dialog",
    "edit-form", "edit-title", "edit-error", "close-edit", "cancel-edit",
    "delete-task", "delete-confirmation", "cancel-delete", "confirm-delete", "announcement",
  ].map((id) => [id, document.getElementById(id)]));

  let tasks = [];
  let filter = "all";
  let editingId = null;
  let returnFocusId = null;
  let announcementTimer;

  function readTasks() {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return [];
    const data = JSON.parse(raw);
    if (!data || data.version !== 1 || !Array.isArray(data.tasks)) {
      throw new Error("Invalid storage format");
    }
    const ids = new Set();
    for (const task of data.tasks) {
      if (!task || typeof task.id !== "string" || !task.id || ids.has(task.id)
        || typeof task.title !== "string" || !task.title.trim()
        || task.title.length > MAX_TITLE_LENGTH || typeof task.completed !== "boolean"
        || !Number.isFinite(task.createdAt) || task.createdAt < 0) {
        throw new Error("Invalid task data");
      }
      ids.add(task.id);
    }
    return data.tasks;
  }

  function showError(id, message) {
    elements[id].textContent = message;
    elements[id].hidden = !message;
  }

  function announce(message) {
    clearTimeout(announcementTimer);
    elements.announcement.textContent = "";
    announcementTimer = setTimeout(() => { elements.announcement.textContent = message; }, 50);
  }

  function loadTasks() {
    try {
      tasks = readTasks();
      showError("storage-error", "");
    } catch {
      showError("storage-error", "保存データを読み込めません。ブラウザーの保存設定・サイトデータを確認してください。元のデータは上書きしません。");
    }
    render();
  }

  function updateTasks(change, message) {
    let latest;
    try {
      // 別タブで更新されたデータも、変更の直前に読み込む。
      latest = readTasks();
    } catch {
      showError("storage-error", "保存データを読み込めないため、変更できません。ブラウザーの保存設定・サイトデータを確認してください。");
      return false;
    }
    const next = change(latest);
    if (next === null) {
      tasks = latest;
      render();
      showError("edit-error", "このタスクは別の画面で削除されました。編集を閉じてください。");
      announce("このタスクは別の画面で削除されました。");
      return false;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, tasks: next }));
    } catch {
      showError("storage-error", "保存できませんでした。変更は反映していません。ブラウザーの保存設定や空き容量を確認して、もう一度お試しください。");
      return false;
    }
    tasks = next;
    showError("storage-error", "");
    render();
    announce(message);
    return true;
  }

  function render() {
    const completed = tasks.filter((task) => task.completed).length;
    const remaining = tasks.length - completed;
    elements["remaining-count"].textContent = remaining;
    elements["progress-label"].textContent = `${completed} / ${tasks.length} 完了`;
    elements.progress.max = Math.max(tasks.length, 1);
    elements.progress.value = completed;
    elements["summary-message"].textContent = tasks.length === 0
      ? "小さな一歩から、はじめよう。"
      : remaining === 0 ? "すべて完了。おつかれさまでした。" : "ひとつ終わるたび、少し身軽に。";

    const visible = tasks.filter((task) => filter === "all"
      || (filter === "completed" ? task.completed : !task.completed));
    elements["task-count"].textContent = `${visible.length}件`;
    const fragment = document.createDocumentFragment();
    for (const task of visible) {
      const item = document.createElement("li");
      item.className = `task-item${task.completed ? " is-completed" : ""}`;
      item.dataset.id = task.id;

      const label = document.createElement("label");
      label.className = "task-check-label";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "task-checkbox";
      checkbox.checked = task.completed;
      checkbox.setAttribute("aria-label", `${task.title}を${task.completed ? "未完了に戻す" : "完了にする"}`);
      label.append(checkbox);

      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "task-edit";
      edit.setAttribute("aria-label", `${task.title}を編集`);
      const title = document.createElement("span");
      title.className = "task-title";
      title.textContent = task.title;
      const indicator = document.createElement("span");
      indicator.className = "edit-indicator";
      indicator.textContent = "›";
      indicator.setAttribute("aria-hidden", "true");
      edit.append(title, indicator);
      item.append(label, edit);
      fragment.append(item);
    }
    elements["task-list"].replaceChildren(fragment);
    elements["empty-state"].hidden = visible.length > 0;
    elements["list-hint"].hidden = visible.length === 0;

    const empty = tasks.length === 0
      ? ["やることを、ひとつ。", "下の入力欄から追加してみましょう。\n小さなことでも、大丈夫。"]
      : filter === "completed"
        ? ["完了したタスクは、まだありません。", "終わったら、丸いチェックをタップ。"]
        : ["すべて、できました。", "ひと息ついて、次の一歩へ。"];
    elements["empty-title"].textContent = empty[0];
    elements["empty-copy"].textContent = empty[1];
    document.querySelectorAll("[data-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.filter === filter));
    });
  }

  function getTitle(input, errorId) {
    const title = input.value.trim();
    if (!title || title.length > MAX_TITLE_LENGTH) {
      showError(errorId, !title ? "タスク名を入力してください。" : "タスク名は200文字以内で入力してください。");
      input.setAttribute("aria-invalid", "true");
      input.focus();
      return null;
    }
    showError(errorId, "");
    input.removeAttribute("aria-invalid");
    return title;
  }

  function focusTask(id, selector = ".task-edit") {
    const item = [...elements["task-list"].children].find((node) => node.dataset.id === id);
    (item?.querySelector(selector) || document.querySelector(`[data-filter="${filter}"]`)).focus();
  }

  function openEdit(id) {
    const task = tasks.find((item) => item.id === id);
    if (!task) return;
    editingId = id;
    returnFocusId = id;
    elements["edit-title"].value = task.title;
    elements["edit-title"].removeAttribute("aria-invalid");
    showError("edit-error", "");
    elements["delete-confirmation"].hidden = true;
    elements["delete-task"].hidden = false;
    elements["edit-dialog"].showModal();
    elements["edit-title"].focus();
    elements["edit-title"].select();
  }

  elements["add-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const title = getTitle(elements["new-task"], "add-error");
    if (title === null) return;
    const id = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const saved = updateTasks((latest) => [
      { id, title, completed: false, createdAt: Date.now() }, ...latest,
    ], "タスクを追加しました。");
    if (!saved) {
      showError("add-error", "追加できませんでした。ブラウザーの保存設定や空き容量を確認してください。");
      return;
    }
    elements["new-task"].value = "";
    if (filter === "completed") {
      filter = "all";
      render();
    }
    // キーボードを閉じて、追加したタスクを確認しやすくする。
    elements["new-task"].blur();
    focusTask(id);
  });

  elements["task-list"].addEventListener("change", (event) => {
    if (!event.target.matches(".task-checkbox")) return;
    const id = event.target.closest(".task-item").dataset.id;
    const checked = event.target.checked;
    const saved = updateTasks((latest) => latest.some((task) => task.id === id)
      ? latest.map((task) => task.id === id ? { ...task, completed: checked } : task)
      : null, checked ? "タスクを完了しました。" : "タスクを未完了に戻しました。");
    if (!saved) event.target.checked = !checked;
    focusTask(id, ".task-checkbox");
  });

  elements["task-list"].addEventListener("click", (event) => {
    const button = event.target.closest(".task-edit");
    if (button) openEdit(button.closest(".task-item").dataset.id);
  });

  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => { filter = button.dataset.filter; render(); });
  });

  elements["edit-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const title = getTitle(elements["edit-title"], "edit-error");
    if (title === null) return;
    const saved = updateTasks((latest) => latest.some((task) => task.id === editingId)
      ? latest.map((task) => task.id === editingId ? { ...task, title } : task)
      : null, "タスクを更新しました。");
    if (saved) elements["edit-dialog"].close();
    else showError("edit-error", "保存できませんでした。保存設定を確認してください。別の画面で削除されたタスクは更新できません。");
  });

  ["close-edit", "cancel-edit"].forEach((id) => {
    elements[id].addEventListener("click", () => elements["edit-dialog"].close());
  });
  elements["edit-dialog"].addEventListener("close", () => {
    editingId = null;
    focusTask(returnFocusId);
  });
  elements["edit-dialog"].addEventListener("click", (event) => {
    if (event.target !== elements["edit-dialog"]) return;
    const bounds = elements["edit-dialog"].getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom) elements["edit-dialog"].close();
  });

  elements["delete-task"].addEventListener("click", () => {
    elements["delete-task"].hidden = true;
    elements["delete-confirmation"].hidden = false;
    elements["cancel-delete"].focus();
  });
  elements["cancel-delete"].addEventListener("click", () => {
    elements["delete-task"].hidden = false;
    elements["delete-confirmation"].hidden = true;
    elements["delete-task"].focus();
  });
  elements["confirm-delete"].addEventListener("click", () => {
    const saved = updateTasks((latest) => latest.filter((task) => task.id !== editingId), "タスクを削除しました。");
    if (saved) elements["edit-dialog"].close();
    else showError("edit-error", "削除できませんでした。ブラウザーの保存設定や空き容量を確認してください。");
  });

  ["new-task", "edit-title"].forEach((id) => {
    elements[id].addEventListener("input", () => {
      elements[id].removeAttribute("aria-invalid");
      showError(id === "new-task" ? "add-error" : "edit-error", "");
    });
  });
  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_KEY || event.key === null) loadTasks();
  });
  function updateDate() {
    const now = new Date();
    elements.today.textContent = new Intl.DateTimeFormat("ja-JP", {
      month: "long", day: "numeric", weekday: "long",
    }).format(now);
    elements.today.dateTime = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { updateDate(); loadTasks(); }
  });
  updateDate();
  loadTasks();
})();
