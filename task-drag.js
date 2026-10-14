(() => {
  "use strict";

  // Pointer Eventsを使い、マウスとiPhoneのタッチを同じ操作として扱う。
  window.TaskDrag = { install({ list, rootZone, status, getTasks, move, announce }) {
    let drag = null;

    function cancel() {
      if (!drag) return;
      const current = drag;
      drag = null;
      cancelAnimationFrame(current.frame);
      current.ghost?.remove();
      current.node.classList.remove("is-dragging");
      list.querySelectorAll("[data-drop]").forEach((item) => delete item.dataset.drop);
      rootZone.hidden = true;
      rootZone.classList.remove("is-target");
      status.hidden = true;
      document.body.classList.remove("is-dragging-task");
      if (current.handle.hasPointerCapture(current.pointerId)) current.handle.releasePointerCapture(current.pointerId);
    }

    function findDrop(x, y) {
      const hit = document.elementFromPoint(x, y);
      if (hit?.closest("#root-drop-zone")) return { targetId: null, mode: "root" };
      const row = hit?.closest(".task-item");
      if (!row || !list.contains(row) || drag.blocked.has(row.dataset.id)) return null;
      const rect = row.getBoundingClientRect();
      const ratio = (y - rect.top) / rect.height;
      return { targetId: row.dataset.id, mode: ratio < .25 ? "before" : ratio > .75 ? "after" : "child" };
    }

    function draw() {
      if (!drag?.active) return;
      drag.ghost.style.left = `${drag.x - drag.offsetX}px`;
      drag.ghost.style.top = `${drag.y - drag.offsetY}px`;
      list.querySelectorAll("[data-drop]").forEach((row) => delete row.dataset.drop);
      rootZone.classList.remove("is-target");
      drag.drop = findDrop(drag.x, drag.y);
      let message;
      if (drag.drop?.mode === "root") {
        rootZone.classList.add("is-target");
        message = "独立したタスクとして、末尾へ移動";
      } else if (drag.drop) {
        const row = [...list.querySelectorAll(".task-item")].find((item) => item.dataset.id === drag.drop.targetId);
        row.dataset.drop = drag.drop.mode;
        if (drag.drop.mode === "after") row.parentElement.dataset.drop = "after";
        const title = getTasks().find((task) => task.id === drag.drop.targetId)?.title || "タスク";
        const action = { before: "の前へ移動", after: "の後ろへ移動", child: "の子タスクにする" }[drag.drop.mode];
        message = `${title}${action}`;
      } else {
        message = "移動先へドラッグ。子タスクも一緒に移動します";
      }
      if (status.textContent !== message) status.textContent = message;
    }

    function tick() {
      if (!drag?.active) return;
      const bottom = document.querySelector(".composer").getBoundingClientRect().top;
      const speed = drag.y < 90 ? -Math.min(14, (90 - drag.y) / 3)
        : drag.y > bottom - 45 ? Math.min(14, (drag.y - bottom + 45) / 3) : 0;
      if (speed) window.scrollBy(0, speed);
      draw();
      drag.frame = requestAnimationFrame(tick);
    }

    list.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (!handle || handle.disabled || !event.isPrimary || event.button !== 0) return;
      cancel();
      const row = handle.closest(".task-item");
      const bounds = row.getBoundingClientRect();
      event.preventDefault();
      handle.focus({ preventScroll: true });
      drag = {
        id: row.dataset.id, pointerId: event.pointerId, handle, row,
        node: row.parentElement, blocked: window.TaskModel.descendantIds(getTasks(), row.dataset.id),
        startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY,
        offsetX: event.clientX - bounds.left, offsetY: event.clientY - bounds.top, active: false,
      };
      handle.setPointerCapture(event.pointerId);
    });

    list.addEventListener("pointermove", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      drag.x = event.clientX;
      drag.y = event.clientY;
      if (!drag.active && Math.hypot(drag.x - drag.startX, drag.y - drag.startY) >= 6) {
        drag.active = true;
        const bounds = drag.row.getBoundingClientRect();
        drag.ghost = drag.row.cloneNode(true);
        drag.ghost.className = "task-item drag-ghost";
        drag.ghost.setAttribute("aria-hidden", "true");
        drag.ghost.inert = true;
        drag.ghost.style.width = `${bounds.width}px`;
        document.body.append(drag.ghost);
        drag.node.classList.add("is-dragging");
        document.body.classList.add("is-dragging-task");
        rootZone.hidden = false;
        status.hidden = false;
        tick();
      }
      if (drag.active) { event.preventDefault(); draw(); }
    });

    list.addEventListener("pointerup", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      const { id, active } = drag;
      const drop = active ? findDrop(event.clientX, event.clientY) : null;
      cancel();
      if (drop) move(id, drop.targetId, drop.mode);
      else if (active) announce("移動をキャンセルしました。");
    });
    ["pointercancel", "lostpointercapture"].forEach((name) => list.addEventListener(name, cancel));
    window.addEventListener("blur", cancel);
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && drag) { cancel(); announce("移動をキャンセルしました。"); }
    });

    list.addEventListener("keydown", (event) => {
      const handle = event.target.closest(".drag-handle");
      if (!handle || handle.disabled || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const tasks = getTasks();
      const source = tasks.find((task) => task.id === handle.closest(".task-item").dataset.id);
      const siblings = tasks.filter((task) => task.parentId === source.parentId);
      const index = siblings.findIndex((task) => task.id === source.id);
      if (event.key === "ArrowUp" && index > 0) move(source.id, siblings[index - 1].id, "before");
      if (event.key === "ArrowDown" && index < siblings.length - 1) move(source.id, siblings[index + 1].id, "after");
      if (event.key === "ArrowRight" && index > 0) move(source.id, siblings[index - 1].id, "child");
      if (event.key === "ArrowLeft" && source.parentId !== null) move(source.id, source.parentId, "after");
    });
    return { cancel };
  } };
})();
