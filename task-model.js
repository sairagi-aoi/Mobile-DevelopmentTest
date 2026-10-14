(() => {
  "use strict";

  const priorities = ["none", "high", "medium", "low"];
  const maxProjects = 5;

  function childrenByParent(tasks) {
    const groups = new Map();
    for (const task of tasks) {
      if (!groups.has(task.parentId)) groups.set(task.parentId, []);
      groups.get(task.parentId).push(task);
    }
    return groups;
  }

  function flatten(tasks) {
    const groups = childrenByParent(tasks);
    const stack = [...(groups.get(null) || [])].reverse().map((task) => ({ task, depth: 0 }));
    const result = [];
    while (stack.length) {
      const entry = stack.pop();
      result.push(entry);
      const children = groups.get(entry.task.id) || [];
      for (let index = children.length - 1; index >= 0; index--) {
        stack.push({ task: children[index], depth: entry.depth + 1 });
      }
    }
    return result;
  }

  function descendantIds(tasks, id) {
    const groups = childrenByParent(tasks);
    const ids = new Set();
    const pending = [id];
    while (pending.length) {
      const current = pending.pop();
      if (ids.has(current)) continue;
      ids.add(current);
      for (const child of groups.get(current) || []) pending.push(child.id);
    }
    return ids;
  }

  function parse(raw) {
    if (raw === null) return { projects: [], tasks: [] };
    const data = JSON.parse(raw);
    if (!data || ![1, 2, 3, 4].includes(data.version) || !Array.isArray(data.tasks)
      || (data.version === 4 && (!Array.isArray(data.projects) || data.projects.length > maxProjects))) {
      throw new Error("Invalid storage format");
    }
    const projectIds = new Set();
    const projectNames = new Set();
    const projects = data.version < 4 ? [] : data.projects.map((project) => {
      if (!project || typeof project.id !== "string" || !project.id.trim() || ["inbox", "all"].includes(project.id)
        || projectIds.has(project.id)
        || typeof project.name !== "string" || !project.name.trim() || project.name.trim().length > 50
        || !Number.isFinite(project.createdAt) || project.createdAt < 0) {
        throw new Error("Invalid project data");
      }
      const name = project.name.trim();
      const normalizedName = name.toLowerCase();
      if (projectNames.has(normalizedName)) throw new Error("Duplicate project name");
      projectIds.add(project.id);
      projectNames.add(normalizedName);
      return { id: project.id, name, createdAt: project.createdAt };
    });
    const ids = new Set();
    const tasks = data.tasks.map((task) => {
      if (!task || typeof task.id !== "string" || !task.id || ids.has(task.id)
        || typeof task.title !== "string" || !task.title.trim() || task.title.length > 200
        || typeof task.completed !== "boolean" || !Number.isFinite(task.createdAt) || task.createdAt < 0
        || (data.version >= 2 && (typeof task.notes !== "string" || task.notes.length > 10000
          || !(task.parentId === null || typeof task.parentId === "string")))
        || (data.version >= 3 && !priorities.includes(task.priority))
        || (data.version === 4 && !(task.projectId === null || projectIds.has(task.projectId)))) {
        throw new Error("Invalid task data");
      }
      ids.add(task.id);
      return {
        id: task.id, title: task.title, completed: task.completed, createdAt: task.createdAt,
        notes: data.version === 1 ? "" : task.notes,
        parentId: data.version === 1 ? null : task.parentId,
        priority: data.version < 3 ? "none" : task.priority,
        projectId: data.version < 4 ? null : task.projectId,
      };
    });
    const byId = new Map(tasks.map((task) => [task.id, task]));
    const validated = new Set();
    for (const task of tasks) {
      let current = task.id;
      const path = new Set();
      while (current !== null && !validated.has(current)) {
        if (!byId.has(current) || path.has(current)) throw new Error("Invalid task hierarchy");
        path.add(current);
        const currentTask = byId.get(current);
        const parent = byId.get(currentTask.parentId);
        if (parent && parent.projectId !== currentTask.projectId) throw new Error("Invalid task project hierarchy");
        current = currentTask.parentId;
      }
      for (const id of path) validated.add(id);
    }
    return { projects, tasks: flatten(tasks).map(({ task }) => task) };
  }

  function move(tasks, id, targetId, mode) {
    if (!["before", "after", "child", "root"].includes(mode)) return null;
    const source = tasks.find((task) => task.id === id);
    const target = tasks.find((task) => task.id === targetId);
    if (!source || (mode !== "root" && !target)) return null;
    const movingIds = descendantIds(tasks, id);
    if (mode !== "root" && movingIds.has(targetId)) return null;
    const parentId = mode === "root" ? null : mode === "child" ? target.id : target.parentId;
    const projectId = mode === "root" ? source.projectId : target.projectId;
    const ordered = flatten(tasks).map(({ task }) => task);
    const moving = ordered.filter((task) => movingIds.has(task.id))
      .map((task) => ({ ...task, projectId, ...(task.id === id ? { parentId } : {}) }));
    const rest = ordered.filter((task) => !movingIds.has(task.id));
    let index = rest.length;
    if (mode === "before") index = rest.findIndex((task) => task.id === targetId);
    if (mode === "after" || mode === "child") {
      const targetIds = descendantIds(rest, targetId);
      index = rest.reduce((last, task, position) => targetIds.has(task.id) ? position + 1 : last, 0);
    }
    return [...rest.slice(0, index), ...moving, ...rest.slice(index)];
  }

  function remove(tasks, id) {
    const source = tasks.find((task) => task.id === id);
    if (!source) return null;
    return tasks.filter((task) => task.id !== id)
      .map((task) => task.parentId === id ? { ...task, parentId: source.parentId } : task);
  }

  function setProject(tasks, id, projectId) {
    const source = tasks.find((task) => task.id === id);
    if (!source || !(projectId === null || typeof projectId === "string")) return null;
    const movingIds = descendantIds(tasks, id);
    const parent = tasks.find((task) => task.id === source.parentId);
    const parentId = parent && parent.projectId !== projectId ? null : source.parentId;
    return tasks.map((task) => movingIds.has(task.id)
      ? { ...task, projectId, ...(task.id === id ? { parentId } : {}) } : task);
  }

  function removeProject(state, id) {
    if (!state.projects.some((project) => project.id === id)) return null;
    return {
      projects: state.projects.filter((project) => project.id !== id),
      tasks: state.tasks.map((task) => task.projectId === id ? { ...task, projectId: null } : task),
    };
  }

  window.TaskModel = { parse, flatten, descendantIds, move, remove, setProject, removeProject, priorities, maxProjects };
})();
