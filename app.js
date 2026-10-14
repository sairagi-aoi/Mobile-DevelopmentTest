(() => {
  "use strict";

  const STORAGE_KEY = "tiny-tasks:v1";
  const MAX_TITLE_LENGTH = 200;
  const PRIORITY_LABELS = { high: "高", medium: "中", low: "低" };
  const model = window.TaskModel;
  const elements = Object.fromEntries([
    "today", "remaining-count", "progress-label", "progress", "summary-message",
    "storage-error", "task-count", "task-list", "empty-state", "empty-title",
    "empty-copy", "list-hint", "add-form", "new-task", "add-error", "edit-dialog",
    "edit-form", "edit-title", "edit-error", "close-edit", "cancel-edit",
    "delete-task", "delete-confirmation", "cancel-delete", "confirm-delete", "announcement",
    "edit-heading", "edit-notes", "edit-parent", "root-drop-zone", "drag-status",
    "project-views", "project-count", "project-limit-hint", "add-project", "edit-project",
    "tasks-heading", "view-description", "add-project-select", "edit-project-select",
    "project-dialog", "project-form", "project-heading", "project-name", "project-error",
    "close-project", "project-cancel", "delete-project", "project-delete-confirmation",
    "cancel-project-delete", "confirm-project-delete",
  ].map((id) => [id, document.getElementById(id)]));

  let tasks = [];
  let projects = [];
  let view = "inbox";
  let filter = "all";
  let editingId = null;
  let editingProjectId = null;
  let returnFocusId = null;
  let announcementTimer;
  let dragController;

  function readState() {
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
      ({ tasks, projects } = readState());
      showError("storage-error", "");
    } catch {
      showError("storage-error", "保存データを読み込めません。ブラウザーの保存設定・サイトデータを確認してください。元のデータは上書きしません。");
    }
    render();
  }

  function updateState(change, message, invalidMessage = "このタスクは別の画面で削除されました。編集を閉じてください。") {
    let latest;
    try {
      // 別タブで更新されたデータも、変更の直前に読み込む。
      latest = readState();
    } catch {
      showError("storage-error", "保存データを読み込めないため、変更できません。ブラウザーの保存設定・サイトデータを確認してください。");
      return false;
    }
    const next = change(latest);
    if (next === null) {
      ({ tasks, projects } = latest);
      render();
      showError(elements["project-dialog"].open ? "project-error" : elements["edit-dialog"].open ? "edit-error" : "storage-error", invalidMessage);
      announce(invalidMessage);
      return false;
    }
    try {
      const normalized = model.parse(JSON.stringify({ version: 4, ...next }));
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 4, ...normalized }));
      ({ tasks, projects } = normalized);
    } catch {
      showError("storage-error", "保存できませんでした。変更は反映していません。ブラウザーの保存設定や空き容量を確認して、もう一度お試しください。");
      return false;
    }
    showError("storage-error", "");
    render();
    announce(message);
    return true;
  }

  function updateTasks(change, message, invalidMessage) {
    return updateState((latest) => {
      const next = change(latest.tasks, latest.projects);
      return next === null ? null : { projects: latest.projects, tasks: next };
    }, message, invalidMessage);
  }

  function projectName(id) {
    return id === null ? "Inbox" : projects.find((project) => project.id === id)?.name || "削除されたプロジェクト";
  }

  function projectOptions(select, value = select.value) {
    const options = [new Option("Inbox", "")];
    for (const project of projects) options.push(new Option(project.name, project.id));
    // 別タブで削除された追加先を、黙ってInboxへ変更しない。
    if (value && !projects.some((project) => project.id === value)) {
      const missing = new Option("削除されたプロジェクト", value);
      missing.disabled = true;
      options.push(missing);
    }
    select.replaceChildren(...options);
    select.value = value;
  }

  function renderProjects() {
    if (!["inbox", "all"].includes(view) && !projects.some((project) => project.id === view)) view = "inbox";
    const fragment = document.createDocumentFragment();
    const views = [{ id: "inbox", name: "Inbox", projectId: null }, { id: "all", name: "すべてのタスク" },
      ...projects.map((project) => ({ ...project, projectId: project.id }))];
    for (const entry of views) {
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.view = entry.id;
      button.setAttribute("aria-label", entry.name);
      button.setAttribute("aria-pressed", String(view === entry.id));
      const name = document.createElement("span");
      name.className = "view-name";
      name.textContent = entry.name;
      const count = document.createElement("span");
      count.className = "view-count";
      count.setAttribute("aria-hidden", "true");
      count.textContent = tasks.filter((task) => entry.id === "all" || task.projectId === entry.projectId).length;
      button.append(name, count);
      fragment.append(button);
    }
    elements["project-views"].replaceChildren(fragment);
    elements["project-count"].textContent = `${projects.length} / ${model.maxProjects}`;
    elements["add-project"].disabled = projects.length >= model.maxProjects;
    elements["project-limit-hint"].textContent = projects.length >= model.maxProjects
      ? "5個まで作成済みです。削除すると新しく作成できます。" : "プロジェクトは5個まで作成できます。Inboxは数に含みません。";
    elements["tasks-heading"].textContent = view === "all" ? "すべてのタスク" : projectName(view === "inbox" ? null : view);
    elements["view-description"].textContent = view === "all" ? "Inboxとすべてのプロジェクトのタスク。"
      : view === "inbox" ? "プロジェクト未指定のタスクを、まずここに。" : "このプロジェクトのタスク。";
    elements["edit-project"].hidden = ["inbox", "all"].includes(view);
    projectOptions(elements["add-project-select"]);
    if (elements["edit-dialog"].open) projectOptions(elements["edit-project-select"]);
  }

  function render() {
    renderProjects();
    const completed = tasks.filter((task) => task.completed).length;
    const remaining = tasks.length - completed;
    elements["remaining-count"].textContent = remaining;
    elements["progress-label"].textContent = `${completed} / ${tasks.length} 完了`;
    elements.progress.max = Math.max(tasks.length, 1);
    elements.progress.value = completed;
    elements["summary-message"].textContent = tasks.length === 0
      ? "小さな一歩から、はじめよう。"
      : remaining === 0 ? "すべて完了。おつかれさまでした。" : "ひとつ終わるたび、少し身軽に。";

    const scoped = tasks.filter((task) => view === "all" || task.projectId === (view === "inbox" ? null : view));
    const visible = scoped.filter((task) => filter === "all"
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

    const empty = scoped.length === 0
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
    node.innerHTML = '<div class="task-item"><label class="task-check-label"><input type="checkbox" class="task-checkbox"></label><button type="button" class="task-edit"><span class="project-caption"></span><span class="parent-caption"></span><span class="priority-badge"><svg class="priority-flag" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 14V2m0 0h10l-3 3 3 3H3"/></svg><span class="sr-only">優先度：</span><span class="priority-text"></span></span><span class="task-title"></span><span class="context-label">親タスク</span><span class="note-preview"></span></button><button type="button" class="child-button"><span aria-hidden="true">＋</span></button><button type="button" class="drag-handle" aria-describedby="drag-help"><span aria-hidden="true">⠿</span></button></div>';
    const row = node.querySelector(".task-item");
    row.dataset.id = task.id;
    row.dataset.priority = task.priority;
    row.dataset.projectId = task.projectId || "";
    const projectCaption = node.querySelector(".project-caption");
    projectCaption.textContent = projectName(task.projectId);
    projectCaption.hidden = view !== "all";
    row.classList.toggle("is-completed", task.completed);
    const checkbox = node.querySelector(".task-checkbox");
    checkbox.checked = task.completed;
    checkbox.setAttribute("aria-label", `${task.title}を${task.completed ? "未完了に戻す" : "完了にする"}`);
    node.querySelector(".task-edit").setAttribute("aria-label", `${task.title}を編集`);
    const badge = node.querySelector(".priority-badge");
    badge.hidden = task.priority === "none";
    badge.id = `priority-${task.id}`;
    node.querySelector(".priority-text").textContent = PRIORITY_LABELS[task.priority] || "";
    if (!badge.hidden) node.querySelector(".task-edit").setAttribute("aria-describedby", badge.id);
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
    elements["edit-form"].elements.priority.value = child ? "none" : task.priority;
    elements["edit-notes"].value = child ? "" : task.notes;
    projectOptions(elements["edit-project-select"], task.projectId || "");
    parentOptions(child ? id : task.parentId || "");
    elements["edit-title"].removeAttribute("aria-invalid");
    showError("edit-error", "");
    elements["delete-confirmation"].hidden = true;
    elements["delete-task"].hidden = child;
    elements["edit-dialog"].showModal();
    elements["edit-title"].focus();
    elements["edit-title"].select();
  }

  function parentOptions(value = "") {
    const projectId = elements["edit-project-select"].value || null;
    const excluded = editingId === null ? new Set() : model.descendantIds(tasks, editingId);
    const options = [new Option("親なし（独立したタスク）", "")];
    for (const { task: parent, depth } of model.flatten(tasks)) {
      if (parent.projectId === projectId && !excluded.has(parent.id)) options.push(new Option(`${"—".repeat(Math.min(depth, 4))}${parent.title}`, parent.id));
    }
    elements["edit-parent"].replaceChildren(...options);
    elements["edit-parent"].value = value;
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
    const projectId = elements["add-project-select"].value || null;
    const saved = updateTasks((latest, latestProjects) => {
      if (projectId !== null && !latestProjects.some((project) => project.id === projectId)) return null;
      return [{ id, title, notes: "", parentId: null, priority: "none", projectId, completed: false, createdAt: Date.now() }, ...latest];
    }, "タスクを追加しました。", "追加先のプロジェクトが別の画面で削除されました。追加先を選び直してください。");
    if (!saved) {
      showError("add-error", "追加できませんでした。追加先のプロジェクト・ブラウザーの保存設定や空き容量を確認してください。");
      return;
    }
    elements["new-task"].value = "";
    elements["add-project-select"].value = "";
    view = projectId === null ? "inbox" : projectId;
    if (filter === "completed") filter = "all";
    render();
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
    button.addEventListener("click", () => { dragController?.cancel(); filter = button.dataset.filter; render(); });
  });

  elements["project-views"].addEventListener("click", (event) => {
    const button = event.target.closest("[data-view]");
    if (!button) return;
    dragController?.cancel();
    view = button.dataset.view;
    render();
  });
  elements["edit-project-select"].addEventListener("change", () => {
    parentOptions();
    showError("edit-error", "");
  });

  elements["edit-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const title = getTitle(elements["edit-title"], "edit-error");
    if (title === null) return;
    const priority = elements["edit-form"].elements.priority.value;
    if (!model.priorities.includes(priority)) {
      showError("edit-error", "優先度を選んでください。");
      return;
    }
    const notes = elements["edit-notes"].value;
    if (notes.length > 10000) { showError("edit-error", "メモは10,000文字以内で入力してください。"); return; }
    const parentId = elements["edit-parent"].value || null;
    const projectId = elements["edit-project-select"].value || null;
    const id = editingId || newId();
    const creating = editingId === null;
    const saved = updateTasks((latest, latestProjects) => {
      if (projectId !== null && !latestProjects.some((project) => project.id === projectId)) return null;
      if (parentId !== null && !latest.some((task) => task.id === parentId && task.projectId === projectId)) return null;
      if (creating) {
        const next = [{ id, title, notes, priority, projectId, parentId: null, completed: false, createdAt: Date.now() }, ...latest];
        return parentId === null ? next : model.move(next, id, parentId, "child");
      }
      const current = latest.find((task) => task.id === id);
      if (!current) return null;
      const transferred = model.setProject(latest, id, projectId);
      const next = transferred.map((task) => task.id === id ? { ...task, title, notes, priority } : task);
      return parentId === next.find((task) => task.id === id).parentId ? next : model.move(next, id, parentId, parentId === null ? "root" : "child");
    }, creating ? "子タスクを追加しました。" : "タスクを更新しました。",
    "更新できません。タスク・親・プロジェクトが別の画面で削除されていないか確認してください。自分自身や子孫を親にすることはできません。");
    if (saved) {
      if (creating) returnFocusId = id;
      if (view !== "all") view = projectId === null ? "inbox" : projectId;
      if (creating && filter === "completed") filter = "all";
      render();
    }
    if (saved) elements["edit-dialog"].close();
    else showError("edit-error", "保存できませんでした。保存設定・親子関係・所属プロジェクトを確認してください。タスクや親、プロジェクトが別の画面で削除された場合は更新できません。");
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

  function openProject(id = null) {
    const project = projects.find((item) => item.id === id);
    if (id !== null && !project) return;
    if (id === null && projects.length >= model.maxProjects) return;
    editingProjectId = id;
    elements["project-heading"].textContent = project ? "プロジェクトを編集" : "プロジェクトを作成";
    elements["project-name"].value = project?.name || "";
    elements["project-name"].removeAttribute("aria-invalid");
    elements["delete-project"].hidden = !project;
    elements["project-delete-confirmation"].hidden = true;
    showError("project-error", "");
    elements["project-dialog"].showModal();
    elements["project-name"].focus();
    elements["project-name"].select();
  }

  elements["add-project"].addEventListener("click", () => openProject());
  elements["edit-project"].addEventListener("click", () => openProject(view));
  elements["project-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    const name = elements["project-name"].value.trim();
    if (!name || name.length > 50) {
      showError("project-error", "プロジェクト名は1〜50文字で入力してください。");
      elements["project-name"].setAttribute("aria-invalid", "true");
      elements["project-name"].focus();
      return;
    }
    const creating = editingProjectId === null;
    const id = editingProjectId || newId();
    const duplicate = (list) => list.some((project) => project.id !== id && project.name.toLowerCase() === name.toLowerCase());
    if (duplicate(projects)) {
      showError("project-error", "同じ名前のプロジェクトがあります。別の名前を入力してください。");
      return;
    }
    const saved = updateState((latest) => {
      if (duplicate(latest.projects) || (creating && latest.projects.length >= model.maxProjects)) return null;
      if (!creating && !latest.projects.some((project) => project.id === id)) return null;
      const nextProjects = creating ? [...latest.projects, { id, name, createdAt: Date.now() }]
        : latest.projects.map((project) => project.id === id ? { ...project, name } : project);
      return { ...latest, projects: nextProjects };
    }, creating ? "プロジェクトを作成しました。" : "プロジェクト名を変更しました。",
    "保存できません。プロジェクトは5個までです。同じ名前や、別の画面で削除されたプロジェクトも確認してください。");
    if (saved) {
      view = id;
      render();
      elements["project-dialog"].close();
    } else if (elements["project-error"].hidden) {
      showError("project-error", "保存できませんでした。ブラウザーの保存設定や空き容量を確認してください。");
    }
  });
  ["close-project", "project-cancel"].forEach((id) => {
    elements[id].addEventListener("click", () => elements["project-dialog"].close());
  });
  elements["project-dialog"].addEventListener("close", () => {
    editingProjectId = null;
    const target = !elements["edit-project"].hidden ? elements["edit-project"]
      : elements["add-project"].disabled ? elements["project-views"].querySelector('[data-view="inbox"]') : elements["add-project"];
    target.focus();
  });
  elements["project-dialog"].addEventListener("click", (event) => {
    if (event.target !== elements["project-dialog"]) return;
    const bounds = elements["project-dialog"].getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom) elements["project-dialog"].close();
  });
  elements["delete-project"].addEventListener("click", () => {
    elements["delete-project"].hidden = true;
    elements["project-delete-confirmation"].hidden = false;
    elements["cancel-project-delete"].focus();
  });
  elements["cancel-project-delete"].addEventListener("click", () => {
    elements["delete-project"].hidden = false;
    elements["project-delete-confirmation"].hidden = true;
    elements["delete-project"].focus();
  });
  elements["confirm-project-delete"].addEventListener("click", () => {
    const saved = updateState((latest) => model.removeProject(latest, editingProjectId),
      "プロジェクトを削除し、タスクをInboxへ戻しました。", "このプロジェクトは別の画面で削除されました。");
    if (saved) {
      view = "inbox";
      render();
      elements["project-dialog"].close();
    } else {
      showError("project-error", "削除できませんでした。ブラウザーの保存設定や、別の画面での変更を確認してください。");
    }
  });
  elements["project-name"].addEventListener("input", () => {
    elements["project-name"].removeAttribute("aria-invalid");
    showError("project-error", "");
  });
  elements["add-project-select"].addEventListener("change", () => showError("add-error", ""));

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
