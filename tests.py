#!/usr/bin/env python3
"""Bong-watcher 纯函数单测：python3 tests.py"""
import json
import unittest
from pathlib import Path

from watcher import (
    collect_open_prs,
    next_sleep,
    pos_int_env,
    infer_modules,
    parse_merged_log,
    parse_pr_number,
    safe_open_prs,
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


class SafeOpenPrs(unittest.TestCase):
    def test_success_passthrough(self):
        prs, err = safe_open_prs([], lambda: [{"number": 1}])
        self.assertEqual((prs, err), ([{"number": 1}], None))

    def test_failure_keeps_prev(self):
        prev = [{"number": 7}]
        def boom():
            raise RuntimeError("gh: no token")
        prs, err = safe_open_prs(prev, boom)
        self.assertEqual(prs, prev, "失败时必须沿用上轮 open_prs")
        self.assertIn("no token", err)

    def test_failure_no_prev_gives_empty(self):
        prs, err = safe_open_prs(None, lambda: (_ for _ in ()).throw(ValueError("x")))
        self.assertEqual(prs, [])
        self.assertTrue(err.startswith("ValueError"))


class CollectOpenPrs(unittest.TestCase):
    LIST = [{"number": 5, "title": "t", "headRefName": "b", "createdAt": "d",
             "statusCheckRollup": [
                 {"name": "e2e", "conclusion": "SUCCESS"},
                 {"context": "CodeRabbit", "state": "pending"},
                 {"name": None, "context": None},
             ]}]

    def fake_gh(self, files_result):
        def gh(args):
            if args[:2] == ["pr", "list"]:
                import copy
                return copy.deepcopy(self.LIST)
            if args[:2] == ["pr", "view"]:
                if isinstance(files_result, Exception):
                    raise files_result
                return files_result
            raise AssertionError(f"unexpected gh args: {args}")
        return gh

    def test_happy_checks_and_modules(self):
        prs = collect_open_prs(MODS, gh=self.fake_gh(["server/src/npc/a.rs"]))
        self.assertEqual(prs[0]["checks"], [
            {"name": "e2e", "state": "SUCCESS"},
            {"name": "CodeRabbit", "state": "PENDING"},
            {"name": "?", "state": "PENDING"},
        ], "name/context 与 conclusion/state 双回退 + 全缺省兜底")
        self.assertEqual(prs[0]["modules"], ["server/npc"])

    def test_files_fetch_failure_fails_whole_round(self):
        with self.assertRaises(RuntimeError, msg="files 失败必须上抛，不得吞成空列表清空模块归属"):
            collect_open_prs(MODS, gh=self.fake_gh(RuntimeError("files api down")))

    def test_files_failure_preserves_old_snapshot_via_safe_wrapper(self):
        prev = [{"number": 5, "modules": ["server/npc"]}]
        prs, err = safe_open_prs(
            prev, lambda: collect_open_prs(MODS, gh=self.fake_gh(RuntimeError("x"))))
        self.assertEqual(prs, prev, "files 失败经 safe 包装必须完整沿用旧快照（含模块归属）")
        self.assertTrue(err)

    def test_list_failure_propagates(self):
        def gh(args):
            raise RuntimeError("gh auth missing")
        with self.assertRaises(RuntimeError):
            collect_open_prs(MODS, gh=gh)


class SafeOpenPrsSequences(unittest.TestCase):
    """跨轮状态转换契约：成功→失败沿用、失败→恢复清错、连续失败不丢数据。"""

    @staticmethod
    def _boom():
        raise RuntimeError("gh down")

    def test_success_then_failure_keeps_snapshot(self):
        r1, e1 = safe_open_prs([], lambda: [{"number": 1}])
        self.assertIsNone(e1)
        r2, e2 = safe_open_prs(r1, self._boom)
        self.assertEqual(r2, [{"number": 1}], "成功→失败必须沿用上轮快照")
        self.assertIn("gh down", e2)

    def test_failure_then_recovery_replaces_and_clears_error(self):
        r1, e1 = safe_open_prs(None, self._boom)
        self.assertEqual((r1, bool(e1)), ([], True), "首次失败=空列表+错误")
        r2, e2 = safe_open_prs(r1, lambda: [{"number": 2}])
        self.assertEqual(r2, [{"number": 2}], "恢复后必须替换为新列表")
        self.assertIsNone(e2, "恢复后错误字段必须清空")

    def test_consecutive_failures_preserve_data(self):
        r1, _ = safe_open_prs([{"number": 9}], self._boom)
        r2, e2 = safe_open_prs(r1, self._boom)
        self.assertEqual(r2, [{"number": 9}], "连续失败不得破坏已保存快照")
        self.assertTrue(e2)

    def test_failure_result_is_copy_not_alias(self):
        prev = [{"number": 3}]
        r1, _ = safe_open_prs(prev, self._boom)
        r1.append({"number": 4})
        self.assertEqual(prev, [{"number": 3}], "降级返回值须为副本，不得污染上轮快照")


class NextSleep(unittest.TestCase):
    def test_firstboot_error_uses_backoff(self):
        self.assertEqual(next_sleep("repo missing", False, 300, 15), 15,
                         "首启失败（从未有快照）必须短间隔重试")

    def test_error_after_snapshot_uses_refresh(self):
        self.assertEqual(next_sleep("gh down", True, 300, 15), 300,
                         "已有有效快照后的偶发失败不得高频重试")

    def test_recovered_uses_refresh(self):
        self.assertEqual(next_sleep(None, True, 300, 15), 300)

    def test_firstboot_ok_uses_refresh(self):
        self.assertEqual(next_sleep(None, False, 300, 15), 300)

    def test_empty_error_treated_as_ok(self):
        self.assertEqual(next_sleep("", False, 300, 15), 300)


class RealGhFixtures(unittest.TestCase):
    """真实 gh CLI 输出样本 pin（fixtures/ 由真机 gh 抓取）：
    走与生产一致的 JSON 结构，锁 rollup 变体解析契约。"""
    FIX = Path(__file__).resolve().parent / "fixtures"

    def gh_from_fixtures(self, args):
        if args[:2] == ["pr", "list"]:
            return json.loads((self.FIX / "gh_pr_list.json").read_text())
        if args[:2] == ["pr", "view"]:
            return json.loads((self.FIX / "gh_pr_view_files.json").read_text())
        raise AssertionError(f"unexpected gh args: {args}")

    def test_real_output_parses_with_full_contract(self):
        prs = collect_open_prs(MODS, gh=self.gh_from_fixtures)
        self.assertGreater(len(prs), 0, "真实 pr list 样本不应为空")
        for pr in prs:
            self.assertIsInstance(pr["number"], int)
            self.assertTrue(pr["title"])
            self.assertIsInstance(pr["checks"], list)
            for c in pr["checks"]:
                self.assertTrue(c["name"] and c["name"] != "?",
                                "真实 CheckRun 条目必须解析出 name")
                self.assertTrue(c["state"], "state 不得为空")
                self.assertEqual(c["state"], c["state"].upper())
            self.assertIsInstance(pr["modules"], list)

    def test_real_fixture_covers_multiple_conclusions(self):
        """真实样本至少覆盖 SUCCESS/FAILURE/NEUTRAL 三个完成态。"""
        prs = collect_open_prs(MODS, gh=self.gh_from_fixtures)
        states = {c["state"] for pr in prs for c in pr["checks"]}
        self.assertTrue({"SUCCESS", "FAILURE", "NEUTRAL"} <= states,
                        f"真实 fixture 应覆盖三个完成态，实际 {states}")

    def test_in_progress_empty_conclusion_falls_back_to_pending(self):
        """IN_PROGRESS 形态（conclusion 为空串 + status=IN_PROGRESS，键形取自
        2026-07-10 真机 gh 输出观测）必须回退 PENDING，不得抛异常/空 state。"""
        entry = {"__typename": "CheckRun", "name": "e2e", "conclusion": "",
                 "status": "IN_PROGRESS", "workflowName": "e2e",
                 "startedAt": "2026-07-10T00:00:00Z", "completedAt": None,
                 "detailsUrl": "https://example.invalid"}
        def gh(args):
            if args[:2] == ["pr", "list"]:
                return [{"number": 1, "title": "t", "headRefName": "b",
                         "createdAt": "d", "statusCheckRollup": [entry]}]
            return []
        prs = collect_open_prs(MODS, gh=gh)
        self.assertEqual(prs[0]["checks"], [{"name": "e2e", "state": "PENDING"}])


class PosIntEnv(unittest.TestCase):
    def setUp(self):
        import os
        self.env = os.environ

    def _with(self, val):
        import os
        os.environ["X_TEST_INTERVAL"] = val
        try:
            return pos_int_env("X_TEST_INTERVAL", 300)
        finally:
            del os.environ["X_TEST_INTERVAL"]

    def test_missing_uses_default(self):
        self.assertEqual(pos_int_env("X_TEST_ABSENT", 300), 300)

    def test_valid(self):
        self.assertEqual(self._with("60"), 60)

    def test_zero_falls_back(self):
        self.assertEqual(self._with("0"), 300, "零间隔=忙循环，必须回退默认")

    def test_negative_falls_back(self):
        self.assertEqual(self._with("-5"), 300, "负数会让 time.sleep 抛异常杀线程")

    def test_non_numeric_falls_back(self):
        self.assertEqual(self._with("abc"), 300)


class NextSleepClamp(unittest.TestCase):
    def test_never_below_one(self):
        self.assertEqual(next_sleep(None, True, 0, 0), 1,
                         "next_sleep 必须保证正值，杜绝忙循环/sleep 异常")
        self.assertEqual(next_sleep("e", False, 300, -3), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
