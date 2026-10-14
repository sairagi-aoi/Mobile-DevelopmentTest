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

    def project(self, name):
        return next(project for project in self.stored()["projects"] if project["name"] == name)

    def create_project(self, name, page=None):
        page = page or self.page
        page.get_by_role("button", name="プロジェクトを作成", exact=True).click()
        page.get_by_label("プロジェクト名", exact=True).fill(name)
        page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        expect(page.locator("#project-dialog")).not_to_be_visible()

    def view(self, view):
        self.page.locator(f'#project-views [data-view="{view}"]').click()

    def add_to_project(self, title, project_id):
        self.page.locator("#add-project-select").select_option(project_id)
        self.add(title)

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
        self.assertEqual(self.stored()["version"], 4)
        self.assertEqual(self.stored()["projects"], [])
        task = self.task("既存のタスク")
        self.assertEqual((task["id"], task["createdAt"], task["completed"], task["parentId"]), ("old-a", 100, True, None))
        self.assertEqual(task["notes"], "以前のタスクにもメモ")
        self.assertEqual(task["priority"], "none")
        self.assertIsNone(task["projectId"])
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
        self.assertEqual(self.stored()["version"], 4)
        self.assertEqual(self.stored()["projects"], [])
        for original, saved in zip(legacy["tasks"], self.stored()["tasks"]):
            self.assertEqual({key: saved[key] for key in original}, original)
            self.assertIsNone(saved["projectId"])
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

    def test_version_three_migration_keeps_priority_and_moves_existing_tasks_to_inbox(self):
        legacy = {"version": 3, "tasks": [
            {"id": "parent", "title": "以前の親", "notes": "親のメモ", "parentId": None,
             "completed": True, "createdAt": 100, "priority": "high"},
            {"id": "child", "title": "以前の子", "notes": "子のメモ", "parentId": "parent",
             "completed": False, "createdAt": 200, "priority": "low"},
        ]}
        self.page.evaluate("([key, data]) => localStorage.setItem(key, JSON.stringify(data))", [STORAGE_KEY, legacy])
        self.page.reload()
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator(".task-title")).to_have_text(["以前の親", "以前の子"])
        self.assertEqual(self.stored(), legacy, "Merely reading old data must not rewrite it")
        self.page.get_by_role("button", name="以前の子を編集", exact=True).click()
        expect(self.page.locator("#edit-project-select")).to_have_value("")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.stored()["version"], 4)
        self.assertEqual(self.stored()["projects"], [])
        for original, saved in zip(legacy["tasks"], self.stored()["tasks"]):
            self.assertEqual({key: saved[key] for key in original}, original)
            self.assertIsNone(saved["projectId"])

    def test_projects_limit_rename_delete_and_safe_names(self):
        unsafe_name = '<img src=x onerror="window.projectInjected=true">'
        for name in (unsafe_name, "仕事", "家事", "趣味", "旅行"):
            self.create_project(name)
        self.assertEqual(len(self.stored()["projects"]), 5)
        expect(self.page.get_by_role("button", name="プロジェクトを作成", exact=True)).to_be_disabled()
        expect(self.page.locator("#project-views img")).to_have_count(0)
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_be_visible()
        self.assertIsNone(self.page.evaluate("window.projectInjected"))
        travel = self.project("旅行")
        expect(self.page.locator(f'#project-views [data-view="{travel["id"]}"]')).to_have_attribute("aria-pressed", "true")
        self.page.locator("#edit-project").click()
        self.page.get_by_label("プロジェクト名", exact=True).fill("次の旅行")
        self.page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.project("次の旅行")["id"], travel["id"])
        self.page.locator("#edit-project").click()
        self.page.get_by_role("button", name="このプロジェクトを削除", exact=True).click()
        self.page.locator("#project-dialog").get_by_role("button", name="削除する", exact=True).click()
        self.assertEqual(len(self.stored()["projects"]), 4)
        expect(self.page.get_by_role("button", name="プロジェクトを作成", exact=True)).to_be_enabled()
        self.create_project("新しいプロジェクト")
        self.assertEqual(len(self.stored()["projects"]), 5)
        self.page.reload()
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_have_attribute("aria-pressed", "true")
        self.assertEqual(self.project("新しいプロジェクト")["name"], "新しいプロジェクト")

    def test_project_name_validation_and_cancel(self):
        self.create_project("仕事")
        original = self.stored()
        self.page.get_by_role("button", name="プロジェクトを作成", exact=True).click()
        self.page.get_by_label("プロジェクト名", exact=True).fill("   ")
        self.page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#project-dialog")).to_be_visible()
        self.assertEqual(self.stored(), original)
        self.page.get_by_label("プロジェクト名", exact=True).fill("仕事")
        self.page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#project-dialog")).to_be_visible()
        self.assertEqual(self.stored(), original)
        self.page.locator("#project-dialog").get_by_role("button", name="キャンセル", exact=True).click()
        expect(self.page.locator("#project-dialog")).not_to_be_visible()
        self.page.locator("#edit-project").click()
        self.page.get_by_label("プロジェクト名", exact=True).fill("保存しない名前")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.stored(), original)

    def test_inbox_is_default_and_explicit_destination_changes_visible_section(self):
        expect(self.page.locator("#add-project-select")).to_have_value("")
        self.add("Inboxのタスク")
        self.assertIsNone(self.task("Inboxのタスク")["projectId"])
        self.create_project("仕事")
        work_id = self.project("仕事")["id"]
        expect(self.page.locator(".task-item")).to_have_count(0)
        expect(self.page.locator("#add-project-select")).to_have_value("")
        self.add_to_project("仕事のタスク", work_id)
        expect(self.page.locator(".task-title")).to_have_text(["仕事のタスク"])
        self.assertEqual(self.task("仕事のタスク")["projectId"], work_id)
        expect(self.page.locator("#add-project-select")).to_have_value("")
        self.add("プロジェクト表示からInboxへ")
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator(".task-title")).to_have_text(["プロジェクト表示からInboxへ", "Inboxのタスク"])
        expect(self.page.locator("#remaining-count")).to_have_text("3")
        self.view("all")
        self.assertEqual(set(self.page.locator(".task-title").all_text_contents()),
                         {"仕事のタスク", "Inboxのタスク", "プロジェクト表示からInboxへ"})
        self.page.reload()
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_have_attribute("aria-pressed", "true")
        expect(self.page.locator("#add-project-select")).to_have_value("")

    def test_project_change_moves_subtree_and_filters_parent_choices(self):
        self.add("Inboxの親")
        self.create_project("仕事")
        work_id = self.project("仕事")["id"]
        self.add_to_project("仕事の親", work_id)
        self.child("仕事の親", "子", "移動しても残るメモ")
        self.child("子", "孫")
        for title in ("仕事の親", "子", "孫"):
            self.assertEqual(self.task(title)["projectId"], work_id)
        self.create_project("家事")
        home_id = self.project("家事")["id"]
        self.add_to_project("家事の親", home_id)
        self.view(work_id)
        self.page.get_by_role("button", name="子を編集", exact=True).click()
        work_options = self.page.locator("#edit-parent option").evaluate_all("els => els.map(el => el.value)")
        self.assertEqual(set(work_options), {"", self.task("仕事の親")["id"]})
        self.page.get_by_label("所属プロジェクト", exact=True).select_option(home_id)
        expect(self.page.locator("#edit-parent")).to_have_value("")
        home_options = self.page.locator("#edit-parent option").evaluate_all("els => els.map(el => el.value)")
        self.assertEqual(set(home_options), {"", self.task("家事の親")["id"]})
        self.page.get_by_label("親タスク", exact=True).select_option(self.task("家事の親")["id"])
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.task("子")["parentId"], self.task("家事の親")["id"])
        self.assertEqual(self.task("孫")["parentId"], self.task("子")["id"])
        self.assertEqual([self.task(title)["projectId"] for title in ("子", "孫")], [home_id, home_id])
        self.assertEqual(self.task("子")["notes"], "移動しても残るメモ")
        self.assertEqual(self.task("仕事の親")["projectId"], work_id)
        self.view(home_id)
        self.page.get_by_role("button", name="子を編集", exact=True).click()
        self.page.get_by_label("所属プロジェクト", exact=True).select_option("")
        expect(self.page.locator("#edit-parent")).to_have_value("")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.view("inbox")
        expect(self.page.get_by_role("button", name="子を編集", exact=True)).to_be_visible()
        self.assertIsNone(self.task("子")["parentId"])
        self.assertIsNone(self.task("子")["projectId"])
        self.assertIsNone(self.task("孫")["projectId"])

    def test_new_child_can_change_project_and_drop_original_parent(self):
        self.create_project("仕事")
        work_id = self.project("仕事")["id"]
        self.add_to_project("親", work_id)
        self.page.get_by_role("button", name="親に子タスクを追加", exact=True).click()
        expect(self.page.locator("#edit-project-select")).to_have_value(work_id)
        expect(self.page.locator("#edit-parent")).to_have_value(self.task("親")["id"])
        self.page.get_by_label("やること", exact=True).fill("Inboxに追加")
        self.page.get_by_label("所属プロジェクト", exact=True).select_option("")
        expect(self.page.locator("#edit-parent")).to_have_value("")
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.assertIsNone(self.task("Inboxに追加")["parentId"])
        self.assertIsNone(self.task("Inboxに追加")["projectId"])

    def test_delete_project_preserves_all_task_details_and_hierarchy_in_inbox(self):
        self.create_project("旅行")
        project_id = self.project("旅行")["id"]
        self.add_to_project("準備", project_id)
        self.child("準備", "予約", "予約番号123")
        self.child("予約", "確認")
        self.page.get_by_role("button", name="予約を編集", exact=True).click()
        self.page.get_by_text("高（赤）", exact=True).tap()
        self.page.get_by_role("button", name="保存する", exact=True).click()
        self.page.get_by_role("checkbox", name="予約を完了にする", exact=True).click()
        original_tasks = self.stored()["tasks"]
        self.page.locator("#edit-project").click()
        self.page.get_by_role("button", name="このプロジェクトを削除", exact=True).click()
        self.page.locator("#project-dialog").get_by_role("button", name="削除する", exact=True).click()
        self.assertEqual(self.stored()["projects"], [])
        self.assertEqual(self.stored()["tasks"], [{**task, "projectId": None} for task in original_tasks])
        self.page.reload()
        expect(self.page.locator(".task-title")).to_have_text(["準備", "予約", "確認"])
        expect(self.page.get_by_role("checkbox", name="予約を未完了に戻す", exact=True)).to_be_checked()

    def test_real_touch_cross_project_drag_moves_whole_subtree_and_root_keeps_project(self):
        self.create_project("仕事")
        work_id = self.project("仕事")["id"]
        self.add_to_project("移動する親", work_id)
        self.child("移動する親", "一緒に移動する子", "残すメモ")
        self.create_project("家事")
        home_id = self.project("家事")["id"]
        self.add_to_project("移動先", home_id)
        self.view("all")
        session = self.touch_start("移動する親")
        target = self.page.locator(f'.task-item[data-id="{self.task("移動先")["id"]}"]').bounding_box()
        self.touch_move(session, target["x"] + target["width"] / 2, target["y"] + target["height"] / 2)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        session.detach()
        self.assertEqual(self.task("移動する親")["parentId"], self.task("移動先")["id"])
        self.assertEqual([self.task(title)["projectId"] for title in ("移動する親", "一緒に移動する子")], [home_id, home_id])
        session = self.touch_start("移動する親")
        zone = self.page.locator("#root-drop-zone").bounding_box()
        self.touch_move(session, zone["x"] + zone["width"] / 2, zone["y"] + zone["height"] / 2)
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        session.detach()
        self.assertIsNone(self.task("移動する親")["parentId"])
        self.assertEqual(self.task("移動する親")["projectId"], home_id)
        self.assertEqual(self.task("一緒に移動する子")["notes"], "残すメモ")
        self.page.reload()
        self.view(home_id)
        expect(self.page.locator(".task-title")).to_have_text(["移動先", "移動する親", "一緒に移動する子"])

    def test_keyboard_project_roots_ignore_hidden_project_siblings(self):
        self.add("Inboxのルート")
        self.create_project("仕事")
        work_id = self.project("仕事")["id"]
        self.add_to_project("仕事A", work_id)
        self.create_project("家事")
        home_id = self.project("家事")["id"]
        self.add_to_project("家事のルート", home_id)
        self.add_to_project("仕事B", work_id)
        project_ids = {task["id"]: task["projectId"] for task in self.stored()["tasks"]}
        expect(self.page.locator(".task-title")).to_have_text(["仕事B", "仕事A"])
        handle = self.page.get_by_role("button", name="仕事Bを移動", exact=True)
        handle.focus()
        handle.press("ArrowDown")
        expect(self.page.locator(".task-title")).to_have_text(["仕事A", "仕事B"])
        first = self.page.get_by_role("button", name="仕事Aを移動", exact=True)
        first.focus()
        first.press("ArrowUp")
        first.press("ArrowRight")
        expect(self.page.locator(".task-title")).to_have_text(["仕事A", "仕事B"])
        self.assertIsNone(self.task("仕事A")["parentId"])
        handle.focus()
        handle.press("ArrowUp")
        expect(self.page.locator(".task-title")).to_have_text(["仕事B", "仕事A"])
        for view, title in (("inbox", "Inboxのルート"), (home_id, "家事のルート")):
            self.view(view)
            isolated = self.page.get_by_role("button", name=f"{title}を移動", exact=True)
            isolated.focus()
            for key in ("ArrowUp", "ArrowDown", "ArrowRight"):
                isolated.press(key)
            expect(self.page.locator(".task-title")).to_have_text([title])
            self.assertIsNone(self.task(title)["parentId"])
        self.assertEqual({task["id"]: task["projectId"] for task in self.stored()["tasks"]}, project_ids)

    def test_stale_deleted_project_destination_keeps_add_and_edit_inputs(self):
        self.create_project("削除されるプロジェクト")
        project_id = self.project("削除されるプロジェクト")["id"]
        self.add_to_project("残すタスク", project_id)
        self.page.locator("#add-project-select").select_option(project_id)
        self.page.get_by_label("新しいタスク", exact=True).fill("追加先を失った入力")
        self.page.get_by_role("button", name="残すタスクを編集", exact=True).click()
        self.page.get_by_label("メモ", exact=True).fill("保存前のメモ")
        second = self.context.new_page()
        second.goto(self.url)
        second.locator(f'#project-views [data-view="{project_id}"]').click()
        second.locator("#edit-project").click()
        second.get_by_role("button", name="このプロジェクトを削除", exact=True).click()
        second.locator("#project-dialog").get_by_role("button", name="削除する", exact=True).click()
        expect(self.page.locator('#project-views [data-view="inbox"]')).to_have_attribute("aria-pressed", "true")
        after_delete = self.stored()
        self.page.get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#edit-error")).to_be_visible()
        expect(self.page.get_by_label("メモ", exact=True)).to_have_value("保存前のメモ")
        self.assertEqual(self.stored(), after_delete)
        self.page.get_by_role("button", name="キャンセル", exact=True).click()
        self.page.get_by_role("button", name="タスクを追加", exact=True).click()
        expect(self.page.locator("#add-error")).to_be_visible()
        expect(self.page.get_by_label("新しいタスク", exact=True)).to_have_value("追加先を失った入力")
        self.assertEqual(self.stored(), after_delete)
        second.close()

    def test_project_save_failure_preserves_projects_tasks_and_form(self):
        self.create_project("元のプロジェクト")
        project_id = self.project("元のプロジェクト")["id"]
        self.add_to_project("元のタスク", project_id)
        original = self.stored()
        self.page.evaluate("() => { Storage.prototype.setItem = () => { throw new DOMException('Full', 'QuotaExceededError'); }; }")
        self.page.get_by_role("button", name="プロジェクトを作成", exact=True).click()
        self.page.get_by_label("プロジェクト名", exact=True).fill("保存に失敗するプロジェクト")
        self.page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        expect(self.page.locator("#project-dialog")).to_be_visible()
        expect(self.page.get_by_label("プロジェクト名", exact=True)).to_have_value("保存に失敗するプロジェクト")
        self.assertEqual(self.stored(), original)
        self.page.locator("#project-dialog").get_by_role("button", name="キャンセル", exact=True).click()
        self.page.locator("#edit-project").click()
        self.page.get_by_label("プロジェクト名", exact=True).fill("保存に失敗する名前")
        self.page.locator("#project-dialog").get_by_role("button", name="保存する", exact=True).click()
        self.assertEqual(self.stored(), original)
        self.page.get_by_role("button", name="このプロジェクトを削除", exact=True).click()
        self.page.locator("#project-dialog").get_by_role("button", name="削除する", exact=True).click()
        expect(self.page.locator("#project-dialog")).to_be_visible()
        self.assertEqual(self.stored(), original)

    def test_invalid_project_data_is_not_overwritten(self):
        project = {"id": "p", "name": "仕事", "createdAt": 100}
        task = {"id": "a", "title": "A", "notes": "", "parentId": None, "completed": False,
                "createdAt": 100, "priority": "none", "projectId": "p"}
        cases = [
            {"tasks": [task]},
            {"projects": [project] * 2, "tasks": [task]},
            {"projects": [project, {**project, "id": "q"}], "tasks": [task]},
            {"projects": [{"id": f"p{i}", "name": f"プロジェクト{i}", "createdAt": 100} for i in range(6)], "tasks": []},
            {"projects": [{**project, "name": "   "}], "tasks": []},
            {"projects": [project], "tasks": [{**task, "projectId": "missing"}]},
            {"projects": [project], "tasks": [{key: value for key, value in task.items() if key != "projectId"}]},
            {"projects": [project], "tasks": [task, {**task, "id": "b", "parentId": "a", "projectId": None}]},
        ]
        for data in cases:
            with self.subTest(data=data):
                raw = json.dumps({"version": 4, **data})
                self.page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [STORAGE_KEY, raw])
                self.page.reload()
                expect(self.page.locator("#storage-error")).to_contain_text("保存データを読み込めません")
                self.add("上書きしない")
                self.assertEqual(self.page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY), raw)

    def test_project_controls_fit_small_screens_with_long_names(self):
        self.create_project("長" * 50)
        self.create_project("仕事")
        self.create_project("家事")
        self.create_project("趣味")
        self.create_project("旅行")
        for width, height in ((320, 568), (430, 932), (932, 430)):
            with self.subTest(width=width, height=height):
                self.page.set_viewport_size({"width": width, "height": height})
                self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                for selector in ("#project-views button", "#add-project", "#edit-project", "#add-project-select"):
                    for control in self.page.locator(selector).all():
                        bounds = control.bounding_box()
                        self.assertGreaterEqual(bounds["height"], 44)
                        self.assertGreaterEqual(bounds["width"], 44)
                self.assertEqual(self.page.locator("#add-project-select").evaluate("el => getComputedStyle(el).fontSize"), "16px")
        self.page.set_viewport_size({"width": 320, "height": 568})
        self.page.locator("#edit-project").click()
        self.assertEqual(self.page.locator("#project-name").evaluate("el => getComputedStyle(el).fontSize"), "16px")
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

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
        for raw in ("{invalid", json.dumps({"version": 5, "tasks": []}),
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
