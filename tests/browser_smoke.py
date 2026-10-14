"""Run real browser checks without adding dependencies to the app itself."""

import functools
import json
import os
from pathlib import Path
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]
STORAGE_KEY = "tiny-tasks:v1"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class TaskAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=str(ROOT))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        options = {"headless": True}
        if os.environ.get("CHROMIUM_PATH"):
            options["executable_path"] = os.environ["CHROMIUM_PATH"]
        cls.browser = cls.playwright.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.context = self.browser.new_context(**self.playwright.devices["iPhone 13"])
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.url)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "Unexpected JavaScript errors")

    def add(self, title, page=None):
        page = page or self.page
        page.get_by_label("新しいタスク", exact=True).fill(title)
        page.get_by_role("button", name="タスクを追加", exact=True).click()

    def stored(self):
        return self.page.evaluate("key => JSON.parse(localStorage.getItem(key))", STORAGE_KEY)

    def child(self, parent, title, notes=""):
        self.page.get_by_role("button", name=f"{parent}に子タスクを追加", exact=True).click()
        self.page.get_by_label("やること", exact=True).fill(title)
        self.page.get_by_label("メモ", exact=True).fill(notes)
        self.page.get_by_role("button", name="保存する", exact=True).click()

    def task(self, title):
        return next(task for task in self.stored()["tasks"] if task["title"] == title)

    def prepare_drag(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.evaluate("window.scrollTo(0, document.querySelector('.tasks-section').offsetTop - 100)")

    def mouse_drag(self, source, target, mode="child"):
        self.prepare_drag()
        handle = self.page.get_by_role("button", name=f"{source}を移動", exact=True).bounding_box()
        row = self.page.locator(f'.task-item[data-id="{self.task(target)["id"]}"]').bounding_box()
        x, y = handle["x"] + 22, handle["y"] + 22
        ratio = {"before": .12, "after": .88, "child": .5}[mode]
        self.page.mouse.move(x, y)
        self.page.mouse.down()
        self.page.mouse.move(x - 10, y)
        self.page.mouse.move(row["x"] + row["width"] / 2, row["y"] + row["height"] * ratio, steps=8)
        self.page.mouse.up()
        expect(self.page.locator(".drag-ghost")).to_have_count(0)

    def touch_start(self, source):
        self.prepare_drag()
        handle = self.page.get_by_role("button", name=f"{source}を移動", exact=True).bounding_box()
        session = self.context.new_cdp_session(self.page)
        x, y = handle["x"] + 22, handle["y"] + 22
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x - 10, "y": y, "id": 1}]})
        expect(self.page.locator(".drag-ghost")).to_have_count(1)
        return session

    def touch_move(self, session, x, y):
        session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y, "id": 1}]})

    def test_legacy_data_migration_preserves_ids_and_completion(self):
        legacy = {"version": 1, "tasks": [
            {"id": "old-a", "title": "既存のタスク", "completed": True, "createdAt": 100},
            {"id": "old-b", "title": "既存の未完了", "completed": False, "createdAt": 200},
        ]}
        self.page.evaluate("([key, data]) => localStorage.setItem(key, JSON.stringify(data))", [STORAGE_KEY, legacy])
        self.page.reload()
        expect(self.page.locator(".task-title")).to_have_text(["既存のタスク", "既存の未完了"])
        self.assertEqual(self.stored(), legacy)
        self.page.get_by_role("button", name="既存のタスクを編集", exact=True).click()
        self.page.get_by_label("メモ", exact=True).fill("以前のタスクにもメモ")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.stored()["version"], 3)
        task = self.task("既存のタスク")
        self.assertEqual((task["id"], task["createdAt"], task["completed"], task["parentId"]), ("old-a", 100, True, None))
        self.assertEqual(task["notes"], "以前のタスクにもメモ")
        self.assertEqual(task["priority"], "none")
        self.page.reload()
        expect(self.page.get_by_role("checkbox", name="既存のタスクを未完了に戻す")).to_be_checked()

    def test_priority_colors_persist_on_completion_and_can_be_removed(self):
        self.page.set_viewport_size({"width": 430, "height": 932})
        self.add("優先度を変えるタスク")
        row = self.page.locator(".task-item").first
        colors = set()
        for value, label, text in (("high", "高（赤）", "高"), ("medium", "中（オレンジ）", "中"), ("low", "低（青）", "低")):
            self.page.get_by_role("button", name="優先度を変えるタスクを編集", exact=True).tap()
            self.page.get_by_text(label, exact=True).tap()
            color = self.page.locator(f'.priority-option[data-priority="{value}"]').evaluate("el => getComputedStyle(el).backgroundColor")
            self.page.get_by_role("button", name="保存する", exact=True).tap()
            self.assertEqual(self.task("優先度を変えるタスク")["priority"], value)
            self.page.reload()
            expect(row).to_have_attribute("data-priority", value)
            expect(row.locator(".priority-text")).to_have_text(text)
            self.assertEqual(row.evaluate("el => getComputedStyle(el).backgroundColor"), color)
            colors.add(color)
        self.assertEqual(len(colors), 3)
        self.page.get_by_role("checkbox", name="優先度を変えるタスクを完了にする", exact=True).tap()
        self.page.get_by_role("button", name="完了", exact=True).tap()
        self.page.reload()
        self.assertEqual(row.evaluate("el => getComputedStyle(el).backgroundColor"), color)
        expect(row.locator(".priority-badge")).to_be_visible()
        self.page.get_by_role("button", name="優先度を変えるタスクを編集", exact=True).tap()
        expect(self.page.get_by_role("radio", name="低（青）", exact=True)).to_be_checked()
        self.page.get_by_text("なし", exact=True).tap()
        self.page.get_by_role("button", name="保存する", exact=True).tap()
        self.page.reload()
        self.assertEqual(self.task("優先度を変えるタスク")["priority"], "none")
        expect(row.locator(".priority-badge")).to_be_hidden()
        self.assertNotIn(row.evaluate("el => getComputedStyle(el).backgroundColor"), colors)

    def test_version_two_migration_preserves_hierarchy_notes_and_order(self):
        legacy = {"version": 2, "tasks": [
            {"id": "parent", "title": "親", "notes": "親のメモ", "parentId": None, "completed": True, "createdAt": 100},
            {"id": "child", "title": "子", "notes": "子のメモ\n2行目", "parentId": "parent", "completed": False, "createdAt": 200},
        ]}
        self.page.evaluate("([key, data]) => localStorage.setItem(key, JSON.stringify(data))", [STORAGE_KEY, legacy])
        self.page.reload()
        self.assertEqual(self.stored(), legacy)
        expect(self.page.locator(".task-title")).to_have_text(["親", "子"])
        expect(self.page.locator('.task-item[data-priority="none"]')).to_have_count(2)
        self.page.get_by_role("button", name="子を編集", exact=True).click()
        self.page.get_by_text("高（赤）", exact=True).tap()
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.stored()["version"], 3)
        for original, saved in zip(legacy["tasks"], self.stored()["tasks"]):
            self.assertEqual({key: saved[key] for key in original}, original)
        self.assertEqual(self.task("親")["priority"], "none")
        self.assertEqual(self.task("子")["priority"], "high")

    def test_parent_child_priorities_and_touch_move_preserve_colors(self):
        self.add("親A")
        self.add("親B")
        for title, label in (("親A", "高（赤）"), ("親B", "低（青）")):
            self.page.get_by_role("button", name=f"{title}を編集", exact=True).click()
            self.page.get_by_text(label, exact=True).tap()
            self.page.get_by_role("button", name="保存する", exact=True).click()
        self.page.get_by_role("button", name="親Aに子タスクを追加", exact=True).click()
        expect(self.page.get_by_role("radio", name="なし", exact=True)).to_be_checked()
        self.page.get_by_label("やること", exact=True).fill("子")
        self.page.get_by_text("中（オレンジ）", exact=True).tap()
        self.page.get_by_role("button", name="保存する", exact=True).click()
        child = self.page.locator(f'.task-item[data-id="{self.task("子")["id"]}"]')
        color = child.evaluate("el => getComputedStyle(el).backgroundColor")
        session = self.touch_start("子")
        self.assertEqual(self.page.locator(".drag-ghost").evaluate("el => getComputedStyle(el).backgroundColor"), color)
        target = self.page.locator(f'.task-item[data-id="{self.task("親B")["id"]}"]').bounding_box()
        self.touch_move(session, target["x"] + target["width"] / 2, target["y"] + target["height"] / 2)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        session.detach()
        self.page.reload()
        self.assertEqual(self.task("子")["parentId"], self.task("親B")["id"])
        self.assertEqual([self.task(title)["priority"] for title in ("親A", "親B", "子")], ["high", "low", "medium"])
        self.assertEqual(child.evaluate("el => getComputedStyle(el).backgroundColor"), color)

    def test_priority_cancel_and_save_failure_preserve_previous_value(self):
        self.add("変更を保存しないタスク")
        original = self.stored()
        self.page.get_by_role("button", name="変更を保存しないタスクを編集", exact=True).click()
        self.page.get_by_text("高（赤）", exact=True).tap()
        self.page.get_by_role("button", name="キャンセル", exact=True).click()
        self.assertEqual(self.stored(), original)
        self.page.get_by_role("button", name="変更を保存しないタスクを編集", exact=True).click()
        expect(self.page.get_by_role("radio", name="なし", exact=True)).to_be_checked()
        self.page.get_by_text("低（青）", exact=True).tap()
        self.page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('Full', 'QuotaExceededError'); }; }")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#edit-error")).to_be_visible()
        expect(self.page.get_by_role("radio", name="低（青）", exact=True)).to_be_checked()
        self.assertEqual(self.stored(), original)
        expect(self.page.locator(".task-item")).to_have_attribute("data-priority", "none")

    def test_invalid_priority_data_is_not_overwritten(self):
        base = {"id": "a", "title": "A", "notes": "", "parentId": None, "completed": False, "createdAt": 100}
        for extra in ({}, {"priority": "urgent"}, {"priority": 1}, {"priority": None}):
            with self.subTest(extra=extra):
                raw = json.dumps({"version": 3, "tasks": [{**base, **extra}]})
                self.page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [STORAGE_KEY, raw])
                self.page.reload()
                expect(self.page.locator("#storage-error")).to_contain_text("保存データを読み込めません")
                self.add("上書きしない")
                self.assertEqual(self.page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY), raw)

    def test_child_grandchild_notes_and_cancel(self):
        self.add("旅行")
        notes = '切符を買う\n<img src=x onerror="window.injected=true">\n  持ち物も確認  '
        self.child("旅行", "準備", notes)
        self.child("準備", "荷造り", "充電器を忘れない")
        self.assertEqual(self.task("準備")["parentId"], self.task("旅行")["id"])
        self.assertEqual(self.task("荷造り")["parentId"], self.task("準備")["id"])
        expect(self.page.get_by_role("list", name="旅行の子タスク", exact=True)).to_be_visible()
        self.page.get_by_role("button", name="準備を編集", exact=True).click()
        expect(self.page.get_by_label("メモ", exact=True)).to_have_value(notes)
        self.page.get_by_label("メモ", exact=True).fill("キャンセルするメモ")
        self.page.get_by_role("button", name="キャンセル", exact=True).click()
        self.page.reload()
        self.assertEqual(self.task("準備")["notes"], notes)
        expect(self.page.locator("#task-list img")).to_have_count(0)
        self.assertIsNone(self.page.evaluate("window.injected"))

    def test_parent_selection_moves_subtree_and_detaches(self):
        self.add("親A")
        self.add("親B")
        self.child("親A", "子", "メモを保持")
        self.child("子", "孫")
        self.page.get_by_role("button", name="子を編集", exact=True).click()
        self.page.get_by_label("親タスク", exact=True).select_option(self.task("親B")["id"])
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.task("子")["parentId"], self.task("親B")["id"])
        self.assertEqual(self.task("孫")["parentId"], self.task("子")["id"])
        self.page.get_by_role("button", name="子を編集", exact=True).click()
        self.page.get_by_label("親タスク", exact=True).select_option("")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.page.reload()
        self.assertIsNone(self.task("子")["parentId"])
        self.assertEqual(self.task("子")["notes"], "メモを保持")
        self.assertEqual(self.task("孫")["parentId"], self.task("子")["id"])

    def test_completion_is_independent_and_filters_keep_context(self):
        self.add("親")
        self.child("親", "子")
        self.page.get_by_role("checkbox", name="親を完了にする", exact=True).click()
        self.assertFalse(self.task("子")["completed"])
        self.page.get_by_role("button", name="未完了", exact=True).click()
        expect(self.page.locator(".task-title")).to_have_text(["親", "子"])
        expect(self.page.locator("#task-count")).to_have_text("1件")
        expect(self.page.locator(".context-label").first).to_be_visible()
        expect(self.page.get_by_role("button", name="子を移動", exact=True)).to_be_disabled()
        self.page.get_by_role("button", name="完了", exact=True).click()
        expect(self.page.locator(".task-title")).to_have_text(["親"])

    def test_deleting_parent_promotes_children_without_losing_notes(self):
        self.add("祖父")
        self.child("祖父", "親")
        self.child("親", "子1", "残すメモ")
        self.child("親", "子2")
        self.child("子1", "孫")
        self.page.get_by_role("button", name="親を編集", exact=True).click()
        self.page.get_by_role("button", name="このタスクを削除", exact=True).click()
        self.page.get_by_role("button", name="削除する", exact=True).click()
        self.page.reload()
        expect(self.page.locator(".task-title")).to_have_text(["祖父", "子1", "孫", "子2"])
        self.assertEqual(self.task("子1")["parentId"], self.task("祖父")["id"])
        self.assertEqual(self.task("子2")["parentId"], self.task("祖父")["id"])
        self.assertEqual(self.task("子1")["notes"], "残すメモ")

    def test_mouse_drag_reorders_and_moves_entire_subtree(self):
        self.add("A")
        self.add("B")
        self.add("C")
        self.mouse_drag("A", "C", "before")
        expect(self.page.locator(".task-title")).to_have_text(["A", "C", "B"])
        self.mouse_drag("A", "B", "after")
        expect(self.page.locator(".task-title")).to_have_text(["C", "B", "A"])
        self.child("B", "Bの子", "移動しても残る")
        self.mouse_drag("B", "C", "child")
        expect(self.page.locator(".task-title")).to_have_text(["C", "B", "Bの子", "A"])
        self.assertEqual(self.task("B")["parentId"], self.task("C")["id"])
        self.page.reload()
        self.assertEqual(self.task("Bの子")["parentId"], self.task("B")["id"])
        self.assertEqual(self.task("Bの子")["notes"], "移動しても残る")

    def test_real_touch_drag_reparents_and_returns_to_root(self):
        self.add("親A")
        self.add("親B")
        self.child("親A", "子")
        session = self.touch_start("子")
        target = self.page.locator(f'.task-item[data-id="{self.task("親B")["id"]}"]').bounding_box()
        self.touch_move(session, target["x"] + target["width"] / 2, target["y"] + target["height"] / 2)
        expect(self.page.locator("#drag-status")).to_contain_text("親Bの子タスクにする")
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        self.assertEqual(self.task("子")["parentId"], self.task("親B")["id"])
        session.detach()
        session = self.touch_start("子")
        zone = self.page.locator("#root-drop-zone").bounding_box()
        self.touch_move(session, zone["x"] + zone["width"] / 2, zone["y"] + zone["height"] / 2)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        session.detach()
        self.page.reload()
        self.assertIsNone(self.task("子")["parentId"])

    def test_self_descendant_and_outside_drops_are_rejected(self):
        self.add("親")
        self.child("親", "子")
        original = self.stored()
        self.mouse_drag("親", "子", "child")
        self.assertEqual(self.stored(), original)
        self.mouse_drag("親", "親", "child")
        self.assertEqual(self.stored(), original)
        self.page.get_by_role("button", name="親を編集", exact=True).click()
        self.assertEqual(self.page.locator("#edit-parent option").count(), 1)
        self.page.get_by_role("button", name="キャンセル", exact=True).click()
        session = self.touch_start("子")
        self.touch_move(session, 2, 200)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        session.detach()
        self.assertEqual(self.stored(), original)
        expect(self.page.locator(".drag-ghost")).to_have_count(0)

    def test_touch_autoscroll_and_cancellation(self):
        for index in range(15):
            self.add(f"タスク{index}")
        self.prepare_drag()
        source = self.page.locator(".task-title").first.text_content()
        session = self.touch_start(source)
        before = self.page.evaluate("scrollY")
        original = self.stored()
        bottom = self.page.locator(".composer").bounding_box()["y"]
        self.touch_move(session, 200, bottom - 8)
        self.page.wait_for_function("before => scrollY > before + 20", arg=before)
        session.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        session.detach()
        self.assertEqual(self.stored(), original)
        expect(self.page.locator(".drag-ghost")).to_have_count(0)
        expect(self.page.locator("#root-drop-zone")).to_be_hidden()

    def test_keyboard_moves_order_and_parent(self):
        self.add("A")
        self.add("B")
        self.add("C")
        handle = self.page.get_by_role("button", name="Bを移動", exact=True)
        handle.focus()
        handle.press("ArrowUp")
        expect(self.page.locator(".task-title")).to_have_text(["B", "C", "A"])
        handle.press("ArrowDown")
        expect(self.page.locator(".task-title")).to_have_text(["C", "B", "A"])
        handle.press("ArrowRight")
        self.assertEqual(self.task("B")["parentId"], self.task("C")["id"])
        handle.press("ArrowLeft")
        self.assertIsNone(self.task("B")["parentId"])
        expect(handle).to_be_focused()

    def test_drag_and_notes_save_failure_preserve_data(self):
        self.add("A")
        self.add("B")
        original = self.stored()
        self.page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('Full', 'QuotaExceededError'); }; }")
        self.mouse_drag("A", "B", "child")
        self.assertEqual(self.stored(), original)
        expect(self.page.locator("#storage-error")).to_contain_text("保存できませんでした")
        self.page.get_by_role("button", name="Aを編集", exact=True).click()
        self.page.get_by_label("メモ", exact=True).fill("保存に失敗しても入力を残す\n2行目")
        self.page.get_by_label("親タスク", exact=True).select_option(self.task("B")["id"])
        self.page.get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.get_by_label("メモ", exact=True)).to_have_value("保存に失敗しても入力を残す\n2行目")
        self.assertEqual(self.stored(), original)

    def test_invalid_hierarchy_is_not_overwritten(self):
        base = {"id": "a", "title": "A", "completed": False, "createdAt": 100, "notes": "", "parentId": None}
        cases = [
            [{**base, "parentId": "missing"}],
            [{**base, "parentId": "a"}],
            [{**base, "parentId": "b"}, {**base, "id": "b", "parentId": "a"}],
            [base, base],
            [{**base, "notes": 123}],
        ]
        for tasks in cases:
            with self.subTest(tasks=tasks):
                raw = json.dumps({"version": 2, "tasks": tasks})
                self.page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [STORAGE_KEY, raw])
                self.page.reload()
                expect(self.page.locator("#storage-error")).to_contain_text("保存データを読み込めません")
                self.add("上書きしない")
                self.assertEqual(self.page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY), raw)

    def test_deep_hierarchy_and_memo_limit_on_small_screen(self):
        self.add("ルート")
        parent = "ルート"
        for index in range(7):
            title = f"子タスク{index}"
            self.child(parent, title)
            parent = title
        self.page.get_by_role("button", name=f"{parent}を編集", exact=True).click()
        self.page.get_by_label("メモ", exact=True).fill("あ" * 10001)
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(len(self.task(parent)["notes"]), 10000)
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        for button in self.page.locator(".task-edit, .child-button, .drag-handle").all():
            self.assertGreaterEqual(button.bounding_box()["width"], 44)
        self.page.get_by_role("button", name=f"{parent}を編集", exact=True).click()
        self.assertEqual(self.page.locator("#edit-notes").evaluate("el => getComputedStyle(el).fontSize"), "16px")
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_task_lifecycle_and_persistence(self):
        expect(self.page.locator("#empty-state")).to_be_visible()
        self.add("  牛乳を買う  ")
        self.add("本を読む")
        expect(self.page.locator(".task-title")).to_have_text(["本を読む", "牛乳を買う"])
        expect(self.page.locator("#remaining-count")).to_have_text("2")
        expect(self.page.locator("#empty-state")).to_be_hidden()

        self.page.get_by_role("button", name="牛乳を買うを編集", exact=True).click()
        self.page.get_by_label("やること", exact=True).fill("牛乳とパンを買う")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#edit-dialog")).not_to_be_visible()
        expect(self.page.get_by_role("button", name="牛乳とパンを買うを編集")).to_be_focused()

        self.page.get_by_role("checkbox", name="牛乳とパンを買うを完了にする", exact=True).click()
        expect(self.page.get_by_role("checkbox", name="牛乳とパンを買うを未完了に戻す")).to_be_checked()
        expect(self.page.locator("#remaining-count")).to_have_text("1")
        expect(self.page.locator("#progress-label")).to_have_text("1 / 2 完了")
        self.page.reload()
        expect(self.page.get_by_role("checkbox", name="牛乳とパンを買うを未完了に戻す")).to_be_checked()
        self.page.get_by_role("button", name="未完了", exact=True).click()
        expect(self.page.locator(".task-title")).to_have_text(["本を読む"])
        self.page.get_by_role("button", name="完了", exact=True).click()
        expect(self.page.locator(".task-title")).to_have_text(["牛乳とパンを買う"])
        self.page.get_by_role("checkbox", name="牛乳とパンを買うを未完了に戻す").click()
        expect(self.page.locator("#empty-title")).to_have_text("完了したタスクは、まだありません。")
        expect(self.page.get_by_role("button", name="完了", exact=True)).to_be_focused()
        self.assertEqual(len(self.stored()["tasks"]), 2)

    def test_edit_cancel_close_and_escape(self):
        self.add("散歩する")
        for action in ("キャンセル", "編集を閉じる", "Escape"):
            self.page.get_by_role("button", name="散歩するを編集").click()
            expect(self.page.get_by_label("やること", exact=True)).to_be_focused()
            self.page.get_by_label("やること", exact=True).fill("保存しない変更")
            if action == "Escape":
                self.page.keyboard.press("Escape")
            else:
                self.page.get_by_role("button", name=action, exact=True).click()
            expect(self.page.locator("#edit-dialog")).not_to_be_visible()
            expect(self.page.locator(".task-title")).to_have_text(["散歩する"])
        self.page.reload()
        expect(self.page.locator(".task-title")).to_have_text(["散歩する"])

    def test_deletion_confirmation_and_persistence(self):
        self.add("削除するタスク")
        self.page.get_by_role("button", name="削除するタスクを編集").click()
        self.page.get_by_role("button", name="このタスクを削除", exact=True).click()
        self.page.get_by_role("button", name="やめる", exact=True).click()
        self.assertEqual(len(self.stored()["tasks"]), 1)
        self.page.get_by_role("button", name="このタスクを削除", exact=True).click()
        self.page.get_by_role("button", name="削除する", exact=True).click()
        expect(self.page.locator("#edit-dialog")).not_to_be_visible()
        self.page.reload()
        expect(self.page.locator("#empty-state")).to_be_visible()
        self.assertEqual(self.stored()["tasks"], [])

    def test_validation_and_keyboard_submission(self):
        self.add("   ")
        expect(self.page.locator("#add-error")).to_have_text("タスク名を入力してください。")
        expect(self.page.locator(".task-item")).to_have_count(0)
        self.page.get_by_label("新しいタスク", exact=True).fill("キーボードから追加")
        self.page.get_by_label("新しいタスク", exact=True).press("Enter")
        expect(self.page.locator(".task-title")).to_have_text(["キーボードから追加"])
        expect(self.page.locator("#add-error")).to_be_hidden()
        self.page.get_by_role("button", name="キーボードから追加を編集").click()
        self.page.get_by_label("やること", exact=True).fill("   ")
        self.page.get_by_role("button", name="保存する").click()
        expect(self.page.locator("#edit-error")).to_be_visible()
        expect(self.page.locator("#edit-dialog")).to_be_visible()
        self.assertEqual(self.stored()["tasks"][0]["title"], "キーボードから追加")

    def test_200_character_limit_and_safe_text_rendering(self):
        title = "あ" * 201
        self.add(title)
        self.assertEqual(self.stored()["tasks"][0]["title"], "あ" * 200)
        malicious_title = '<img src=x onerror="window.testInjection=true">'
        self.add(malicious_title)
        expect(self.page.locator(".task-title").first).to_have_text(malicious_title)
        expect(self.page.locator("#task-list img")).to_have_count(0)
        self.assertIsNone(self.page.evaluate("window.testInjection"))

    def test_add_from_completed_filter_becomes_visible(self):
        self.page.get_by_role("button", name="完了", exact=True).click()
        self.add("新しいタスク")
        expect(self.page.locator(".task-title")).to_have_text(["新しいタスク"])
        expect(self.page.get_by_role("button", name="すべて", exact=True)).to_have_attribute("aria-pressed", "true")

    def test_save_failure_preserves_data_and_input(self):
        self.add("保存済み")
        original = self.stored()
        self.page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('Full', 'QuotaExceededError'); }; }")
        self.add("保存に失敗するタスク")
        expect(self.page.locator("#storage-error")).to_contain_text("保存できませんでした")
        expect(self.page.locator("#add-error")).to_be_visible()
        expect(self.page.get_by_label("新しいタスク", exact=True)).to_have_value("保存に失敗するタスク")
        expect(self.page.locator(".task-title")).to_have_text(["保存済み"])
        self.page.get_by_role("checkbox", name="保存済みを完了にする").click()
        expect(self.page.get_by_role("checkbox", name="保存済みを完了にする")).not_to_be_checked()
        self.page.get_by_role("button", name="保存済みを編集").click()
        self.page.get_by_label("やること", exact=True).fill("保存できない変更")
        self.page.get_by_role("button", name="保存する").click()
        expect(self.page.locator("#edit-error")).to_be_visible()
        expect(self.page.locator("#edit-dialog")).to_be_visible()
        self.assertEqual(self.stored(), original)
        self.page.get_by_role("button", name="このタスクを削除", exact=True).click()
        self.page.get_by_role("button", name="削除する", exact=True).click()
        expect(self.page.locator("#edit-error")).to_contain_text("削除できませんでした")
        self.assertEqual(self.stored(), original)
        self.page.get_by_role("button", name="キャンセル", exact=True).click()
        self.page.reload()
        self.add("再試行")
        expect(self.page.locator("#storage-error")).to_be_hidden()

    def test_corrupt_data_is_not_overwritten(self):
        for raw in ("{invalid", json.dumps({"version": 4, "tasks": []}),
                    json.dumps({"version": 1, "tasks": [{"id": "invalid"}]})):
            with self.subTest(raw=raw):
                self.page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [STORAGE_KEY, raw])
                self.page.reload()
                expect(self.page.locator("#storage-error")).to_contain_text("保存データを読み込めません")
                self.add("上書きしない")
                expect(self.page.locator(".task-item")).to_have_count(0)
                self.assertEqual(self.page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY), raw)

    def test_storage_unavailable(self):
        self.context.add_init_script("Storage.prototype.getItem = () => { throw new DOMException('Blocked', 'SecurityError'); }")
        self.page.reload()
        expect(self.page.locator("#storage-error")).to_be_visible()
        self.add("読み込めないときのタスク")
        expect(self.page.locator(".task-item")).to_have_count(0)
        expect(self.page.get_by_label("新しいタスク", exact=True)).to_have_value("読み込めないときのタスク")

    def test_multiple_tabs_and_stale_editor(self):
        self.add("最初のタスク")
        second = self.context.new_page()
        second.goto(self.url)
        self.add("別タブのタスク", page=second)
        expect(self.page.locator(".task-title")).to_have_text(["別タブのタスク", "最初のタスク"])
        self.page.get_by_role("button", name="最初のタスクを編集").click()
        second.get_by_role("button", name="最初のタスクを編集").click()
        second.get_by_role("button", name="このタスクを削除", exact=True).click()
        second.get_by_role("button", name="削除する", exact=True).click()
        expect(self.page.locator(".task-item")).to_have_count(1)
        self.page.get_by_label("やること", exact=True).fill("削除されたタスクを編集")
        self.page.get_by_role("button", name="保存する").click()
        expect(self.page.locator("#edit-error")).to_contain_text("別の画面で削除")
        self.assertEqual(len(self.stored()["tasks"]), 1)
        second.evaluate("localStorage.clear()")
        expect(self.page.locator(".task-item")).to_have_count(0)
        second.close()

    def test_layout_touch_targets_and_long_list(self):
        self.page.set_viewport_size({"width": 390, "height": 664})
        copy = self.page.locator("#empty-copy").bounding_box()
        footer = self.page.locator(".composer").bounding_box()
        self.assertLessEqual(copy["y"] + copy["height"], footer["y"])
        self.add("短い画面でも見えるタスク")
        self.page.evaluate("window.scrollTo(0, 0)")
        first = self.page.locator(".task-item").first.bounding_box()
        self.assertLessEqual(first["y"] + first["height"], footer["y"])
        self.add("あ" * 200)
        for width, height in ((320, 568), (375, 667), (390, 844), (844, 390), (1280, 800)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({"width": width, "height": height})
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                self.assertEqual(self.page.locator("#new-task").evaluate("el => getComputedStyle(el).fontSize"), "16px")
                for selector in (".add-button", ".filters button", ".task-check-label", ".task-edit"):
                    for box in self.page.locator(selector).all():
                        bounds = box.bounding_box()
                        self.assertGreaterEqual(bounds["height"], 44)
                        self.assertGreaterEqual(bounds["width"], 44)
        self.page.set_viewport_size({"width": 390, "height": 844})
        for index in range(12):
            self.add(f"タスク {index}")
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        last = self.page.locator(".task-item").last.bounding_box()
        footer = self.page.locator(".composer").bounding_box()
        self.assertLessEqual(last["y"] + last["height"], footer["y"])
        self.page.locator(".task-edit").last.click()
        expect(self.page.locator("#edit-dialog")).to_be_visible()
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
