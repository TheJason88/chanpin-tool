import unittest

import pandas as pd

import delivery_destination_analysis as analysis
import delivery_audit_backfill
import delivery_match_adapter
import delivery_runtime
import delivery_workflow
import tool_common


class LtlTimingTests(unittest.TestCase):
    def test_legacy_duration_recovery_and_existing_validity_rules(self):
        base = {
            "标准运输类型": "LTL", "车次号": "", "是否有真实车次号": False,
            "仓库": "LA", "派送区域": "Local", "出库体积": 10,
            "批次出库时间": pd.Timestamp("2026-08-01"),
            "派送时效": 0, "是否有效时效": False,
        }
        rows = []
        for name, end, extra in [
            ("valid", "2026-08-03", {}),
            ("boundary", "2026-08-04", {"派送时效": float("nan")}),
            ("over", "2026-08-04 00:00:01", {}),
            ("zero", "2026-08-01", {}),
            ("negative", "2026-07-31", {}),
            ("missing_end", None, {}),
            ("missing_start", "2026-08-03", {"批次出库时间": pd.NaT}),
            ("zero_volume", "2026-08-03", {"出库体积": 0}),
            ("negative_volume", "2026-08-03", {"出库体积": -10}),
            ("missing_volume", "2026-08-03", {"出库体积": float("nan")}),
            ("marked_inside", "2026-08-03", {"备注": "里"}),
            ("marked_outside", "2026-08-03", {"备注": "外仓"}),
            ("ftl_no_trip", "2026-08-03", {"标准运输类型": "FTL", "派送时效": 2}),
            ("unknown", "2026-08-03", {"标准运输类型": "其他", "派送时效": 2}),
        ]:
            rows.append({**base, "批次号": name, "批次签收时间": pd.Timestamp(end) if end else pd.NaT, **extra})
        source = pd.DataFrame(rows)
        snapshot = source.copy(deep=True)
        cleaned = delivery_runtime._clean_delivery_time_columns(source)
        sample = delivery_workflow.timing_sample_rows(cleaned)
        self.assertEqual(sample["批次号"].tolist(), ["valid", "boundary"])
        self.assertEqual(sample["派送时效"].tolist(), [2, 3])
        self.assertTrue(sample["是否有效时效"].all())
        self.assertTrue(cleaned.iloc[2:7]["派送时效"].isna().all())
        pd.testing.assert_frame_equal(source, snapshot)

    def test_final_transport_type_overrides_raw_ltl_evidence(self):
        rows = pd.DataFrame([{
            "标准运输类型": "FTL", "运输类型": "LTL", "装车类型标准值": "散板",
            "批次出库时间": "2026-08-01", "批次签收时间": "2026-08-03",
            "派送时效": 1, "出库体积": 70, "车次号": "REAL",
        }])
        cleaned = delivery_runtime._clean_delivery_time_columns(rows)
        self.assertEqual(cleaned.iloc[0]["派送时效"], 1)
        self.assertEqual(len(delivery_workflow.timing_sample_rows(cleaned)), 1)
        cleaned["车次号"] = ""
        self.assertTrue(delivery_workflow.timing_sample_rows(cleaned).empty)

    def test_ltl_region_thresholds_keep_existing_boundaries(self):
        for warehouse, region, limit in [
            ("LA", "Local", 3), ("LA", "中短途", 7), ("LA", "美中", 10),
            ("LA", "美东", 15), ("LA", "美南", 15),
            ("NJ", "中距离", 6), ("NJ", "远距离", 10),
            ("SAV", "中距离", 6), ("DAL", "远距离", 10), ("LA", "未知", 30),
        ]:
            with self.subTest(warehouse=warehouse, region=region):
                start = pd.Timestamp("2026-08-01")
                rows = pd.DataFrame([{
                    "仓库": warehouse, "派送区域": region, "标准运输类型": "LTL",
                    "批次出库时间": start, "批次签收时间": start + pd.Timedelta(days=limit, seconds=extra),
                    "派送时效": float("nan"),
                } for extra in [0, 1]])
                cleaned = delivery_runtime._clean_delivery_time_columns(rows)
                self.assertEqual(cleaned.iloc[0]["派送时效"], limit)
                self.assertTrue(pd.isna(cleaned.iloc[1]["派送时效"]))

    def test_stage1_stage2_and_legacy_excel_roundtrip(self):
        delivery_runtime.bootstrap(delivery_workflow)
        raw = pd.DataFrame([{
            "仓库": "LA", "派送方式": "卡车派送", "运输类型": transport,
            "车次号": trip, "批次号": name, "创建时间": "2026-07-01",
            "出库时间": "2026-08-01", "签收时间": end, "目的地": "Amazon-ONT8",
            "车型": "53尺大车", "装车类型": loading, "出库体积": volume,
            "出库卡板数": 5, "派送成本": 300, "派送卡车": "Carrier",
        } for name, transport, trip, end, volume, loading in [
            ("F", "FTL", "T", "2026-08-02", 60, "地板"),
            ("L", "LTL", "", "2026-08-04", 20, "散板"),
        ]])
        cleaned, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches(
            [("synthetic.xlsx", raw)], "LA")
        ltl = cleaned["标准运输类型"].eq("LTL")
        self.assertEqual(ltl.sum(), 1)
        self.assertEqual(cleaned.loc[ltl, "派送时效"].iloc[0], 3)
        self.assertFalse(cleaned.loc[ltl, "是否有真实车次号"].iloc[0])
        self.assertEqual(cleaned["派送成本"].sum(), 800)  # Only FTL floor adds $200.
        fresh_reports = delivery_match_adapter.build_split_stage2_report(
            delivery_workflow, cleaned, pd.DataFrame(), "按月统计")
        legacy = cleaned.copy(deep=True)
        legacy.loc[ltl, "派送时效"] = float("nan")
        legacy.loc[ltl, "是否有效时效"] = False
        workbook = tool_common.write_sheets_to_excel({"派送一_清洗后批次数据": legacy})
        reread = pd.read_excel(workbook, sheet_name="派送一_清洗后批次数据")
        reports = delivery_match_adapter.build_split_stage2_report(
            delivery_workflow, reread, pd.DataFrame(), "按月统计")
        matched = reports["派送二_匹配后批次数据"]
        self.assertFalse(matched.loc[matched["标准运输类型"].eq("LTL"), "是否FTL发车"].iloc[0])
        timing = reports["派送时效"].iloc[0]
        self.assertEqual(timing["平均派送时效"], 1.5)
        self.assertEqual(timing["P80派送时效"], 3)
        self.assertEqual(timing["有效时效批次数"], 2)
        self.assertEqual(timing["有效时效方数"], 80)
        for name in ["派送时效", "FBA仓点分析", "FBA派送方式分析", "货量", "发车量",
                     "每方价格参考", "分类型价格参考", "调拨数据", "黄金标准数据"]:
            # Excel may read whole-number floats as integers; report values must agree.
            pd.testing.assert_frame_equal(reports[name], fresh_reports[name], check_dtype=False)
        pd.testing.assert_frame_equal(
            delivery_audit_backfill._build_linehaul_sheet(matched),
            delivery_audit_backfill._build_linehaul_sheet(fresh_reports["派送二_匹配后批次数据"]),
            check_dtype=False,
        )
        exported = tool_common.write_sheets_to_excel(reports)
        self.assertEqual(pd.read_excel(exported, sheet_name="派送时效").iloc[0]["有效时效方数"], 80)

    def test_ltl_fbx_included_internal_and_unknown_destinations_excluded(self):
        rows = pd.DataFrame([{
            "仓库": "LA", "统计周期": "2026-08", "批次目的地类型": kind,
            "批次目的仓点": code, "平台名称": "", "标准运输类型": "LTL",
            "车次号": "", "派送时效": 2, "出库体积": 10,
        } for kind, code in [("FBA", "ONT8"), ("FBX平台仓", "16号仓"),
                             ("仓间调拨", "IL合作仓"), ("其他", "未知仓")]])
        report = analysis.build_station_timing_report(rows)
        self.assertEqual(set(report["目的仓点"]), {"ONT8", "16号仓"})
        self.assertTrue(report["有效时效批次数"].eq(1).all())
        self.assertTrue(report["平均派送时效"].eq(2).all())


if __name__ == "__main__":
    unittest.main()
