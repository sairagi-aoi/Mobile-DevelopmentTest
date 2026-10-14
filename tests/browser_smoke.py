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
        for raw in ("{invalid", json.dumps({"version": 2, "tasks": []}),
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
