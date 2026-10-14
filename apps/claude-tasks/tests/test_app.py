"""Browser tests for Claude Tasks (iPhone viewport, real Chromium via Playwright)."""

import functools
import json
import os
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

APP_DIR = Path(__file__).resolve().parents[1]
STORAGE_KEY = "claude-tasks:v1"
CODEX_STORAGE_KEY = "tiny-tasks:v1"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class ClaudeTasksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=str(APP_DIR))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
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
        self.page = self.new_page()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], "Unexpected JavaScript errors")

    # ---------- helpers ----------

    def new_page(self):
        page = self.context.new_page()
        self.errors = getattr(self, "errors", [])
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(self.url)
        return page

    def add(self, title, page=None):
        page = page or self.page
        page.get_by_label("新しいタスク").fill(title)
        page.get_by_role("button", name="追加", exact=True).tap()

    def stored(self, page=None):
        page = page or self.page
        raw = page.evaluate(f"localStorage.getItem('{STORAGE_KEY}')")
        return json.loads(raw) if raw else None

    def titles(self, page=None):
        page = page or self.page
        return page.locator(".task-title").all_text_contents()

    def filter(self, name):
        self.page.get_by_role("button", name=name, exact=True).tap()

    def open_editor(self, title):
        self.page.get_by_role("button", name=f"「{title}」を編集").tap()
        expect(self.page.locator("#editor")).to_be_visible()

    # ---------- tests ----------

    def test_layout_is_mobile_friendly(self):
        page = self.page
        expect(page).to_have_title("Claude Tasks")
        expect(page.locator(".badge")).to_have_text("by Claude")
        width = page.evaluate("document.documentElement.scrollWidth")
        self.assertLessEqual(width, 390, "No horizontal overflow on iPhone width")
        font = page.locator("#new-title").evaluate("el => getComputedStyle(el).fontSize")
        self.assertEqual(font, "16px", "Inputs must be 16px to avoid iOS zoom")
        box = page.locator(".add").bounding_box()
        self.assertGreaterEqual(box["height"], 44)
        composer = page.locator("#composer").bounding_box()
        viewport_height = page.viewport_size["height"]
        self.assertAlmostEqual(composer["y"] + composer["height"], viewport_height, delta=1,
                               msg="Composer sits at the bottom, within thumb reach")
        expect(page.locator("#empty")).to_have_text("下の入力欄からタスクを追加しましょう")

    def test_add_task(self):
        self.add("牛乳を買う")
        self.add("  メールを返す  ")
        self.assertEqual(self.titles(), ["メールを返す", "牛乳を買う"])
        expect(self.page.get_by_label("新しいタスク")).to_have_value("")
        expect(self.page.locator("#stats")).to_have_text("残り 2 件 · 完了 0 / 2")
        data = self.stored()
        self.assertEqual(data["version"], 1)
        self.assertEqual([t["title"] for t in data["tasks"]], ["メールを返す", "牛乳を買う"])

    def test_blank_title_is_rejected(self):
        self.add("   ")
        self.assertEqual(self.titles(), [])
        self.assertIsNone(self.stored())

    def test_html_is_not_interpreted(self):
        self.add("<img src=x onerror=alert(1)>")
        self.assertEqual(self.titles(), ["<img src=x onerror=alert(1)>"])
        self.assertEqual(self.page.locator(".task img").count(), 0)

    def test_complete_and_uncomplete(self):
        self.add("洗濯")
        self.page.get_by_role("checkbox", name="「洗濯」を完了にする").tap()
        # 未完了フィルターでは完了したタスクが消える
        self.assertEqual(self.titles(), [])
        expect(self.page.locator("#empty")).to_have_text("すべて完了しました 🎉")
        expect(self.page.locator("#stats")).to_have_text("残り 0 件 · 完了 1 / 1")
        self.assertTrue(self.stored()["tasks"][0]["done"])
        self.assertIsNotNone(self.stored()["tasks"][0]["completedAt"])

        self.filter("完了")
        self.assertEqual(self.titles(), ["洗濯"])
        checkbox = self.page.get_by_role("checkbox", name="「洗濯」を完了にする")
        expect(checkbox).to_have_attribute("aria-checked", "true")
        checkbox.tap()
        self.assertEqual(self.titles(), [])
        self.filter("未完了")
        self.assertEqual(self.titles(), ["洗濯"])
        self.assertFalse(self.stored()["tasks"][0]["done"])
        self.assertIsNone(self.stored()["tasks"][0]["completedAt"])

    def test_all_filter_lists_active_first(self):
        self.add("A")
        self.add("B")
        self.add("C")
        self.page.get_by_role("checkbox", name="「C」を完了にする").tap()
        self.filter("すべて")
        self.assertEqual(self.titles(), ["B", "A", "C"])

    def test_edit_task(self):
        self.add("資料作成")
        self.open_editor("資料作成")
        expect(self.page.locator("#edit-title")).to_have_value("資料作成")
        self.page.locator("#edit-title").fill("会議資料を作成")
        self.page.locator("#edit-note").fill("10時まで\nグラフを追加")
        self.page.get_by_role("button", name="保存", exact=True).tap()
        expect(self.page.locator("#editor")).to_be_hidden()
        self.assertEqual(self.titles(), ["会議資料を作成"])
        expect(self.page.locator(".task-note")).to_have_text("10時まで\nグラフを追加")
        task = self.stored()["tasks"][0]
        self.assertEqual(task["title"], "会議資料を作成")
        self.assertEqual(task["note"], "10時まで\nグラフを追加")

    def test_edit_can_complete(self):
        self.add("掃除")
        self.open_editor("掃除")
        self.page.get_by_label("完了にする", exact=True).check()
        self.page.get_by_role("button", name="保存", exact=True).tap()
        self.assertTrue(self.stored()["tasks"][0]["done"])

    def test_edit_cancel_keeps_original(self):
        self.add("元のタイトル")
        self.open_editor("元のタイトル")
        self.page.locator("#edit-title").fill("変更後")
        self.page.get_by_role("button", name="キャンセル").tap()
        expect(self.page.locator("#editor")).to_be_hidden()
        self.assertEqual(self.titles(), ["元のタイトル"])
        self.assertEqual(self.stored()["tasks"][0]["title"], "元のタイトル")

    def test_edit_rejects_blank_title(self):
        self.add("残すタスク")
        self.open_editor("残すタスク")
        self.page.locator("#edit-title").fill("   ")
        self.page.get_by_role("button", name="保存", exact=True).tap()
        expect(self.page.locator("#editor")).to_be_visible()
        self.assertEqual(self.stored()["tasks"][0]["title"], "残すタスク")

    def test_delete_and_undo(self):
        self.add("1つ目")
        self.add("2つ目")
        self.open_editor("1つ目")
        self.page.get_by_role("button", name="このタスクを削除").tap()
        self.assertEqual(self.titles(), ["2つ目"])
        expect(self.page.locator("#toast-text")).to_have_text("「1つ目」を削除しました")
        self.assertEqual(len(self.stored()["tasks"]), 1)
        self.page.get_by_role("button", name="元に戻す").tap()
        self.assertEqual(self.titles(), ["2つ目", "1つ目"])
        self.assertEqual(len(self.stored()["tasks"]), 2)

    def test_clear_completed(self):
        for title in ["A", "B", "C"]:
            self.add(title)
        self.page.get_by_role("checkbox", name="「A」を完了にする").tap()
        self.page.get_by_role("checkbox", name="「C」を完了にする").tap()
        expect(self.page.locator("#clear-done")).to_be_hidden()
        self.filter("すべて")
        self.page.get_by_role("button", name="完了したタスクを削除").tap()
        self.assertEqual(self.titles(), ["B"])
        expect(self.page.locator("#toast-text")).to_have_text("完了したタスク 2 件を削除しました")
        self.assertEqual([t["title"] for t in self.stored()["tasks"]], ["B"])

    def test_data_persists_after_reload(self):
        self.add("永続化テスト")
        self.page.get_by_role("checkbox", name="「永続化テスト」を完了にする").tap()
        self.add("未完了のまま")
        self.page.reload()
        self.assertEqual(self.titles(), ["未完了のまま"])
        self.filter("完了")
        self.assertEqual(self.titles(), ["永続化テスト"])

    def test_does_not_touch_codex_app_storage(self):
        self.page.evaluate(
            f"localStorage.setItem('{CODEX_STORAGE_KEY}', JSON.stringify({{version: 2, tasks: []}}))"
        )
        self.add("Claudeのタスク")
        raw = self.page.evaluate(f"localStorage.getItem('{CODEX_STORAGE_KEY}')")
        self.assertEqual(json.loads(raw), {"version": 2, "tasks": []})

    def test_corrupt_storage_is_backed_up(self):
        self.page.evaluate(f"localStorage.setItem('{STORAGE_KEY}', '{{broken')")
        self.page.reload()
        expect(self.page.locator("#toast")).to_be_visible()
        expect(self.page.locator("#toast-text")).to_contain_text("退避しました")
        backups = self.page.evaluate(
            "Object.keys(localStorage).filter(k => k.startsWith('claude-tasks:v1:corrupt-'))"
        )
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.page.evaluate(f"localStorage.getItem('{backups[0]}')"), "{broken")
        self.add("復旧後のタスク")
        self.assertEqual(self.titles(), ["復旧後のタスク"])

    def test_save_failure_keeps_state(self):
        self.add("保存済み")
        self.page.evaluate(
            "() => { Storage.prototype.setItem = () => { throw new Error('QuotaExceededError'); }; }"
        )
        self.add("保存できない")
        expect(self.page.locator("#toast-text")).to_contain_text("保存できませんでした")
        self.assertEqual(self.titles(), ["保存済み"])
        expect(self.page.get_by_label("新しいタスク")).to_have_value("保存できない")

    def test_syncs_between_tabs(self):
        other = self.new_page()
        self.add("タブAで追加")
        expect(other.locator(".task-title")).to_have_text(["タブAで追加"])

    def test_long_title_wraps_without_overflow(self):
        self.add("あ" * 200)
        width = self.page.evaluate("document.documentElement.scrollWidth")
        self.assertLessEqual(width, 390)
        self.assertEqual(len(self.stored()["tasks"][0]["title"]), 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
