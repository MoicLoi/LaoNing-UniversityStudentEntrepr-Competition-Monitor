# -*- coding: utf-8 -*-
"""离线单元测试：不联网，只验证解析与差量比对逻辑。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor  # noqa: E402


class TestHtmlToText(unittest.TestCase):
    def test_strips_tags_and_entities(self):
        html = '<p style="font-weight:400"><strong>一、竞赛规程</strong></p><p>辽宁&nbsp;省<br/>沈阳市</p>'
        text = monitor.html_to_text(html)
        self.assertIn("一、竞赛规程", text)
        self.assertIn("辽宁 省", text)
        self.assertIn("沈阳市", text)
        self.assertNotIn("<", text)

    def test_script_and_style_removed(self):
        html = "<style>p{color:red}</style><script>alert(1)</script><p>正文</p>"
        self.assertEqual(monitor.html_to_text(html), "正文")

    def test_empty(self):
        self.assertEqual(monitor.html_to_text(None), "")
        self.assertEqual(monitor.html_to_text(""), "")


class TestStableUrl(unittest.TestCase):
    def test_strips_ticket(self):
        self.assertEqual(
            monitor.stable_url("/sys/common/static?ticket=abc123"),
            "/sys/common/static",
        )

    def test_keeps_plain_path(self):
        self.assertEqual(monitor.stable_url("/logo.png"), "/logo.png")

    def test_none(self):
        self.assertIsNone(monitor.stable_url(None))


class TestCompareItem(unittest.TestCase):
    def _base(self):
        return {
            "name": "示例大赛",
            "startTime": "2026-10-26 00:00:00",
            "endTime": "2026-11-10 23:59:59",
            "itemCount": 100,
            "memberNum": 300,
            "awardPublicStatus": "0",
            "certificatePublishStatus": 0,
            "tabs": [2, 3, 4],
            "files": [
                {"name": "方案.docx", "size": 1024, "type": "docx", "url": "/sys/common/static?ticket=OLD"},
            ],
            "detail_hash": "aaaaaaaaaaaaaaaa",
            "detail_chars": 100,
        }

    def test_no_change(self):
        a, b = self._base(), self._base()
        self.assertEqual(monitor.compare_item(a, b, "正文", "正文"), [])

    def test_ticket_only_change_is_ignored(self):
        a = self._base()
        b = self._base()
        b["files"][0]["url"] = "/sys/common/static?ticket=NEW"
        # 封面同理
        a["coverUrl"] = "/sys/common/static"
        b["coverUrl"] = "/sys/common/static"
        self.assertEqual(monitor.compare_item(a, b, "正文", "正文"), [])

    def test_item_count_change(self):
        a = self._base()
        b = self._base()
        b["itemCount"] = 101
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "stats")
        self.assertEqual(events[0]["field"], "itemCount")

    def test_award_public_status_is_status_event(self):
        a = self._base()
        b = self._base()
        b["awardPublicStatus"] = "1"
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertEqual(events[0]["type"], "status")
        self.assertEqual(events[0]["field"], "awardPublicStatus")

    def test_endtime_change(self):
        a = self._base()
        b = self._base()
        b["endTime"] = "2026-11-11 23:59:59"
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertEqual([e["field"] for e in events], ["endTime"])

    def test_tab_change(self):
        a = self._base()
        b = self._base()
        b["tabs"] = [3, 4]
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertEqual(events[0]["field"], "tabs")

    def test_file_added(self):
        a = self._base()
        b = self._base()
        b["files"].append({"name": "报名表.xlsx", "size": 2048, "type": "xlsx", "url": "/x?ticket=1"})
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertEqual(events[0]["type"], "files")
        self.assertIn("报名表.xlsx", events[0]["note"])

    def test_file_removed(self):
        a = self._base()
        b = self._base()
        b["files"] = []
        events = monitor.compare_item(a, b, "正文", "正文")
        self.assertIn("移除", events[0]["note"])

    def test_detail_changed_produces_diff(self):
        a = self._base()
        b = self._base()
        b["detail_hash"] = "bbbbbbbbbbbbbbbb"
        b["detail_chars"] = 120
        events = monitor.compare_item(a, b, "第一行\n第二行", "第一行\n第二行改了")
        self.assertEqual(events[0]["type"], "detail")
        self.assertTrue(events[0]["diff"])
        self.assertTrue(any(d.startswith("+") for d in events[0]["diff"]))


class TestDiffText(unittest.TestCase):
    def test_line_diff(self):
        lines = monitor.diff_text_lines("a\nb\nc", "a\nB\nc")
        self.assertIn("- b", lines)
        self.assertIn("+ B", lines)

    def test_hunk_cap(self):
        # 构造 4 处独立改动，验证只保留前 max_hunks 处
        old = "a\nX1\nb\nX2\nc\nX3\nd\nX4"
        new = "a\nY1\nb\nY2\nc\nY3\nd\nY4"
        lines = monitor.diff_text_lines(old, new, max_hunks=2)
        self.assertTrue(any("更多差异略" in x for x in lines))


class TestResolveYears(unittest.TestCase):
    def test_explicit_list(self):
        self.assertEqual(monitor.resolve_years({"years": [2025, "2026"]}), ["2025", "2026"])

    def test_offsets(self):
        import datetime
        year = datetime.datetime.now().year
        got = monitor.resolve_years({"years": "auto", "year_offsets": [0, 1]})
        self.assertEqual(got, [str(year), str(year + 1)])


if __name__ == "__main__":
    unittest.main()
