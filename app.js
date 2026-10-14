(() => {
  "use strict";

  const STORAGE_KEY = "tiny-tasks:v1";
  const MAX_TITLE_LENGTH = 200;
  const model = window.TaskModel;
  const elements = Object.fromEntries([
    "today", "remaining-count", "progress-label", "progress", "summary-message",
    "storage-error", "task-count", "task-list", "empty-state", "empty-title",
    "empty-copy", "list-hint", "add-form", "new-task", "add-error", "edit-dialog",
    "edit-form", "edit-title", "edit-error", "close-edit", "cancel-edit",
    "delete-task", "delete-confirmation", "cancel-delete", "confirm-delete", "announcement",
    "edit-heading", "edit-notes", "edit-parent", "root-drop-zone", "drag-status",
  ].map((id) => [id, document.getElementById(id)]));

  let tasks = [];
  let filter = "all";
  let editingId = null;
  let returnFocusId = null;
  let announcementTimer;
  let dragController;

  function readTasks() {
    return model.parse(localStorage.getItem(STORAGE_KEY));
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
    dragController?.cancel();
    try {
      tasks = readTasks();
      showError("storage-error", "");
    } catch {
      showError("storage-error", "保存データを読み込めません。ブラウザーの保存設定・サイトデータを確認してください。元のデータは上書きしません。");
    }
    render();
  }

  function updateTasks(change, message, invalidMessage = "このタスクは別の画面で削除されました。編集を閉じてください。") {
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
      showError(elements["edit-dialog"].open ? "edit-error" : "storage-error", invalidMessage);
      announce(invalidMessage);
      return false;
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 2, tasks: next }));
    } catch {
      showError("storage-error", "保存できませんでした。変更は反映していません。ブラウザーの保存設定や空き容量を確認して、もう一度お試しください。");
      return false;
    }
    tasks = model.flatten(next).map(({ task }) => task);
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
    const shown = new Set(visible.map((task) => task.id));
    const matching = new Set(shown);
    const byId = new Map(tasks.map((task) => [task.id, task]));
    // 絞り込み時も、該当する子タスクの親を残して関係を分かりやすくする。
    for (const task of visible) {
      let parentId = task.parentId;
      while (parentId !== null && !shown.has(parentId)) {
        shown.add(parentId);
        parentId = byId.get(parentId).parentId;
      }
    }
    const fragment = document.createDocumentFragment();
    const childLists = new Map([[null, fragment]]);
    for (const { task, depth } of model.flatten(tasks)) {
      if (!shown.has(task.id)) continue;
      const node = makeTaskNode(task, !matching.has(task.id));
      const children = document.createElement("ul");
      children.className = `task-children${depth >= 2 ? " is-deep" : ""}`;
      children.setAttribute("aria-label", `${task.title}の子タスク`);
      children.hidden = !tasks.some((child) => child.parentId === task.id && shown.has(child.id));
      node.append(children);
      childLists.set(task.id, children);
      childLists.get(task.parentId).append(node);
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

  function makeTaskNode(task, context) {
    const node = document.createElement("li");
    node.className = "task-node";
    node.dataset.id = task.id;
    // 固定の構造のみHTMLで作り、ユーザーの入力はすべてtextContentで表示する。
    node.innerHTML = '<div class="task-item"><label class="task-check-label"><input type="checkbox" class="task-checkbox"></label><button type="button" class="task-edit"><span class="parent-caption"></span><span class="task-title"></span><span class="context-label">親タスク</span><span class="note-preview"></span></button><button type="button" class="child-button"><span aria-hidden="true">＋</span></button><button type="button" class="drag-handle" aria-describedby="drag-help"><span aria-hidden="true">⠿</span></button></div>';
    const row = node.querySelector(".task-item");
    row.dataset.id = task.id;
    row.classList.toggle("is-completed", task.completed);
    const checkbox = node.querySelector(".task-checkbox");
    checkbox.checked = task.completed;
    checkbox.setAttribute("aria-label", `${task.title}を${task.completed ? "未完了に戻す" : "完了にする"}`);
    node.querySelector(".task-edit").setAttribute("aria-label", `${task.title}を編集`);
    node.querySelector(".task-title").textContent = task.title;
    node.querySelector(".context-label").hidden = !context;
    const caption = node.querySelector(".parent-caption");
    caption.hidden = task.parentId === null;
    caption.textContent = task.parentId === null ? "" : `親：${tasks.find((parent) => parent.id === task.parentId).title}`;
    const note = node.querySelector(".note-preview");
    note.textContent = task.notes.slice(0, 140);
    note.hidden = !task.notes.trim();
    node.querySelector(".child-button").setAttribute("aria-label", `${task.title}に子タスクを追加`);
    const handle = node.querySelector(".drag-handle");
    handle.setAttribute("aria-label", `${task.title}を移動`);
    handle.disabled = filter !== "all";
    handle.title = filter === "all" ? "ドラッグして移動" : "移動するには「すべて」を選んでください";
    return node;
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
    const item = [...elements["task-list"].querySelectorAll(".task-item")].find((node) => node.dataset.id === id);
    (item?.querySelector(selector) || document.querySelector(`[data-filter="${filter}"]`)).focus();
  }

  function openEdit(id, child = false) {
    const task = tasks.find((item) => item.id === id);
    if (!task) return;
    editingId = child ? null : id;
    returnFocusId = id;
    elements["edit-heading"].textContent = child ? "子タスクを追加" : "タスクを編集";
    elements["edit-title"].value = child ? "" : task.title;
    elements["edit-notes"].value = child ? "" : task.notes;
    const excluded = child ? new Set() : model.descendantIds(tasks, id);
    const options = [new Option("親なし（独立したタスク）", "")];
    for (const { task: parent, depth } of model.flatten(tasks)) {
      if (!excluded.has(parent.id)) options.push(new Option(`${"—".repeat(Math.min(depth, 4))}${parent.title}`, parent.id));
    }
    elements["edit-parent"].replaceChildren(...options);
    elements["edit-parent"].value = child ? id : task.parentId || "";
    elements["edit-title"].removeAttribute("aria-invalid");
    showError("edit-error", "");
    elements["delete-confirmation"].hidden = true;
    elements["delete-task"].hidden = child;
    elements["edit-dialog"].showModal();
    elements["edit-title"].focus();
    elements["edit-title"].select();
  }

  function newId() {
    return typeof crypto.randomUUID === "function"
      ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function moveTask(id, targetId, mode) {
    const saved = updateTasks((latest) => model.move(latest, id, targetId, mode),
      "タスクを移動しました。", "移動できません。自分自身や子孫には移動できません。移動先が削除されていないか確認してください。");
    focusTask(id, ".drag-handle");
    return saved;
  }

  elements["add-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const title = getTitle(elements["new-task"], "add-error");
    if (title === null) return;
    const id = newId();
    const saved = updateTasks((latest) => [
      { id, title, notes: "", parentId: null, completed: false, createdAt: Date.now() }, ...latest,
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
    const childButton = event.target.closest(".child-button");
    if (childButton) { openEdit(childButton.closest(".task-item").dataset.id, true); return; }
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
    const notes = elements["edit-notes"].value;
    if (notes.length > 10000) { showError("edit-error", "メモは10,000文字以内で入力してください。"); return; }
    const parentId = elements["edit-parent"].value || null;
    const id = editingId || newId();
    const creating = editingId === null;
    const saved = updateTasks((latest) => {
      if (parentId !== null && !latest.some((task) => task.id === parentId)) return null;
      if (creating) {
        const next = [{ id, title, notes, parentId: null, completed: false, createdAt: Date.now() }, ...latest];
        return parentId === null ? next : model.move(next, id, parentId, "child");
      }
      const current = latest.find((task) => task.id === id);
      if (!current) return null;
      const next = latest.map((task) => task.id === id ? { ...task, title, notes } : task);
      return parentId === current.parentId ? next : model.move(next, id, parentId, parentId === null ? "root" : "child");
    }, creating ? "子タスクを追加しました。" : "タスクを更新しました。",
    "更新できません。タスクや親が別の画面で削除されていないか確認してください。自分自身や子孫を親にすることはできません。");
    if (saved && creating) {
      returnFocusId = id;
      if (filter === "completed") { filter = "all"; render(); }
    }
    if (saved) elements["edit-dialog"].close();
    else showError("edit-error", "保存できませんでした。保存設定や親子関係を確認してください。タスクや親が別の画面で削除された場合は更新できません。");
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
    const saved = updateTasks((latest) => model.remove(latest, editingId), "タスクを削除しました。子タスクは残しました。");
    if (saved) elements["edit-dialog"].close();
    else showError("edit-error", "削除できませんでした。ブラウザーの保存設定や空き容量を確認してください。");
  });

  ["new-task", "edit-title", "edit-notes", "edit-parent"].forEach((id) => {
    elements[id].addEventListener("input", () => {
      elements[id].removeAttribute("aria-invalid");
      showError(id === "new-task" ? "add-error" : "edit-error", "");
    });
  });
  dragController = window.TaskDrag.install({
    list: elements["task-list"], rootZone: elements["root-drop-zone"], status: elements["drag-status"],
    getTasks: () => tasks, move: moveTask, announce,
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
