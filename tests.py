#!/usr/bin/env python3
"""Bong-watcher 纯函数单测：python3 tests.py"""
import unittest

from watcher import (
    infer_modules,
    parse_merged_log,
    parse_pr_number,
    scrape_modules,
    tally_lines,
)

MODS = [
    {"id": "server/npc", "layer": "server", "path": "server/src/npc/", "title": "NPC"},
    {"id": "server/npc-brain", "layer": "server", "path": "server/src/npc/brain/", "title": "Brain"},
    {"id": "client/hud", "layer": "client", "path": "client/src/main/java/hud/", "title": "HUD"},
]


class ParsePrNumber(unittest.TestCase):
    def test_happy(self):
        self.assertEqual(parse_pr_number("修复 xx (#1234)"), 1234)

    def test_trailing_space(self):
        self.assertEqual(parse_pr_number("fix (#7)  "), 7)

    def test_no_number(self):
        self.assertIsNone(parse_pr_number("普通 commit 无 PR 号"))

    def test_number_not_at_end(self):
        self.assertIsNone(parse_pr_number("(#12) 在开头不算"))

    def test_empty_and_none(self):
        self.assertIsNone(parse_pr_number(""))
        self.assertIsNone(parse_pr_number(None))


class InferModules(unittest.TestCase):
    def test_exact_prefix(self):
        self.assertEqual(infer_modules(["server/src/npc/mod.rs"], MODS), ["server/npc"])

    def test_longest_prefix_wins(self):
        self.assertEqual(
            infer_modules(["server/src/npc/brain/scorers.rs"], MODS), ["server/npc-brain"]
        )

    def test_fallback_top_dir(self):
        self.assertEqual(infer_modules(["docs/plan-x.md"], MODS), ["dir:docs"])

    def test_fallback_rootfile(self):
        self.assertEqual(infer_modules(["CLAUDE.md"], MODS), ["dir:CLAUDE.md"])

    def test_dedup_keeps_order(self):
        files = ["server/src/npc/a.rs", "docs/a.md", "server/src/npc/b.rs"]
        self.assertEqual(infer_modules(files, MODS), ["server/npc", "dir:docs"])

    def test_empty_inputs(self):
        self.assertEqual(infer_modules([], MODS), [])
        self.assertEqual(infer_modules(None, MODS), [])
        self.assertEqual(infer_modules(["a/b.rs"], []), ["dir:a"])


class ScrapeModules(unittest.TestCase):
    HTML = (
        "junk // === DATA:START ===\n"
        'const MODULES = [\n'
        '{ id:"server/npc", layer:"server", name:"npc", path:"server/src/npc/",'
        ' title:"NPC AI", summary:"x", components:[] },\n'
        '{ id:"client/hud", layer:"client", name:"hud", path:"client/src/hud/",'
        ' title:"HUD 渲染", summary:"y" },\n'
        "];\n// === DATA:END === junk"
    )

    def test_scrape(self):
        mods = scrape_modules(self.HTML)
        self.assertEqual(
            [m["id"] for m in mods], ["server/npc", "client/hud"],
            "应按出现顺序刮出两个模块条目",
        )
        self.assertEqual(mods[0]["path"], "server/src/npc/")
        self.assertEqual(mods[1]["title"], "HUD 渲染")
        self.assertEqual(mods[1]["layer"], "client")

    def test_no_markers_still_scrapes(self):
        html = self.HTML.replace("=== DATA:START ===", "").replace("=== DATA:END ===", "")
        self.assertEqual(len(scrape_modules(html)), 2)

    def test_empty(self):
        self.assertEqual(scrape_modules(""), [])

    def test_json_style_keys(self):
        """真实 DATA 块是 JSON 风格（带引号键、多行、嵌套数组）——必须能刮出。"""
        html = (
            "/* === DATA:START === */\n"
            "const MODULES = [\n  {\n"
            '    "id": "agent/schema",\n'
            '    "layer": "agent",\n'
            '    "name": "schema",\n'
            '    "path": "agent/packages/schema/src/",\n'
            '    "title": "IPC Schema 包",\n'
            '    "summary": "x",\n'
            '    "tags": [\n      "typescript",\n      "typebox"\n    ]\n'
            "  }\n];\n/* === DATA:END === */"
        )
        mods = scrape_modules(html)
        self.assertEqual(len(mods), 1, "JSON 风格键的条目应被刮出")
        self.assertEqual(mods[0]["id"], "agent/schema")
        self.assertEqual(mods[0]["path"], "agent/packages/schema/src/")
        self.assertEqual(mods[0]["layer"], "agent")


class TallyLines(unittest.TestCase):
    def test_counts_desc(self):
        out = tally_lines("a\nb\na\n\n a \n")
        self.assertEqual(out[0], {"name": "a", "count": 3})
        self.assertEqual(out[1], {"name": "b", "count": 1})

    def test_strip_email(self):
        out = tally_lines(
            "Claude Fable 5 <noreply@anthropic.com>\nClaude Fable 5 <x@y>\n",
            strip_email=True,
        )
        self.assertEqual(out, [{"name": "Claude Fable 5", "count": 2}])

    def test_empty(self):
        self.assertEqual(tally_lines(""), [])
        self.assertEqual(tally_lines(None), [])

    def test_tie_sorted_by_name(self):
        out = tally_lines("b\na\n")
        self.assertEqual([r["name"] for r in out], ["a", "b"])


class ParseMergedLog(unittest.TestCase):
    RAW = (
        "abcdef1234567890\t修复 甲 (#100)\t2026-07-09\n"
        "server/src/npc/mod.rs\n"
        "docs/plan-a.md\n"
        "\n"
        "fedcba0987654321\t无 PR 号的普通提交\t2026-07-08\n"
        "scripts/x.sh\n"
        "\n"
        "1111222233334444\t修复 乙 (#99)\t2026-07-08\n"
    )

    def test_parse(self):
        prs = parse_merged_log(self.RAW)
        self.assertEqual([p["number"] for p in prs], [100, 99],
                         "无 PR 号的 commit 应被跳过")
        self.assertEqual(prs[0]["title"], "修复 甲")
        self.assertEqual(prs[0]["files"],
                         ["server/src/npc/mod.rs", "docs/plan-a.md"])
        self.assertEqual(prs[0]["sha"], "abcdef12")
        self.assertEqual(prs[1]["files"], [], "尾部无文件列表的 PR files 应为空")

    def test_orphan_files_ignored(self):
        prs = parse_merged_log("loose/file.rs\nanother.md\n")
        self.assertEqual(prs, [])

    def test_empty(self):
        self.assertEqual(parse_merged_log(""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
