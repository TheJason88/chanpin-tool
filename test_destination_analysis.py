import ast
from pathlib import Path
import unittest

import pandas as pd

import delivery_destination_analysis as analysis
import delivery_workflow
import delivery_runtime
import delivery_match_adapter
import delivery_audit_backfill
import tool_common


def batch(batch_no, trip, volume=50, cost=500, supplier="A", kind="FBA", code="ONT8", **extra):
    return {
        "仓库": "LA", "统计周期": "2026-08", "批次出库时间": "2026-08-10",
        "批次签收时间": "2026-08-12", "批次号": batch_no, "批次号集合": batch_no,
        "车次号": trip, "是否有真实车次号": bool(trip), "标准运输类型": "FTL",
        "是否FTL发车": bool(trip), "整车批次数": 1, "批次车份额": 1,
        "出库体积": volume, "整车出库体积": volume, "出库卡板数": 10,
        "原始派送成本": cost, "派送成本": cost, "派送卡车": supplier,
        "主产品类型": kind, "批次目的地类型": kind, "批次目的仓点": code,
        "FBA仓点代码集合": code if kind == "FBA" else "",
        "FBX代码集合": code if kind == "FBX平台仓" else "", "平台名称": "谷仓" if kind == "FBX平台仓" else "",
        "FBA出库体积": volume if kind == "FBA" else 0, "FBX出库体积": volume if kind == "FBX平台仓" else 0,
        "车型标准值": "53尺大车", "装车类型标准值": "卡板", "派送时效": 2,
        "备注": "", "出库类型": "正常", "业务场景": "FBA派送", "专线线路": "LA-NJ",
        **extra,
    }


class DestinationAnalysisTests(unittest.TestCase):
    def test_three_levels_all_suppliers_and_equal_weight_valid_batch_prices(self):
        rows = pd.DataFrame([
            batch("A", "TA", supplier="A"), batch("B", "TB", supplier="B"),
            batch("C", "TC", volume=10, cost=100, supplier="A", 标准运输类型="LTL"),
            batch("D", "TD", volume=90, cost=450, supplier="D", 标准运输类型="LTL"),
            batch("E", "TE", volume=10, cost=0, supplier="", 标准运输类型="LTL"),
        ])
        snapshot = rows.copy(deep=True)
        summary, methods = analysis.build_fba_destination_reports(rows)
        station = summary.iloc[0]
        self.assertEqual(station["总出库体积"], 210)
        self.assertEqual(station["平均每方成本"], 8.75)  # mean(10, 10, 10, 5), not 1550/200
        self.assertEqual(station["有效成本方数"], 200)
        self.assertEqual(station["成本覆盖率"], "95.24%")
        self.assertIn("LTL 52.38%", station["派送方式占比"])
        pallet = methods.loc[methods["派送方式"].eq("整车大车卡板")].iloc[0]
        self.assertEqual(pallet["供应商使用比例"], "A 50.00%；B 50.00%")
        self.assertEqual(pallet["供应商平均整车成本"], "A $500.00；B $500.00")
        ltl = methods.loc[methods["派送方式"].eq("LTL")].iloc[0]
        self.assertEqual(ltl["平均每方成本"], 7.5)
        self.assertIn("A 9.09%", ltl["供应商使用比例"])
        self.assertIn("供应商未知/冲突 9.09%", ltl["供应商使用比例"])
        self.assertIn("A $10.00/方", ltl["供应商平均每方成本"])
        self.assertTrue(pd.isna(ltl["平均整车成本"]))
        self.assertEqual(ltl["供应商平均整车成本"], "")
        self.assertEqual(methods["总出库体积"].sum(), station["总出库体积"])
        pd.testing.assert_frame_equal(rows, snapshot)

    def test_same_destination_batches_merge_for_truck_price_and_floor_fee_once(self):
        rows = pd.DataFrame([
            batch("A", "T", volume=20, cost=200, 批次车份额=1 / 3, 整车批次数=2, 整车出库体积=60, 装车类型标准值="地板"),
            batch("B", "T", volume=40, cost=600, 批次车份额=2 / 3, 整车批次数=2, 整车出库体积=60, 装车类型标准值="地板"),
        ])
        loaded = tool_common.apply_floor_loading_fee(rows)
        annotated = analysis.annotate_trip_context(loaded)
        self.assertTrue(annotated[analysis.METHOD].eq("整车大车地板").all())
        self.assertAlmostEqual(annotated["大车地板装车费"].sum(), 200)
        _, methods = analysis.build_fba_destination_reports(annotated)
        row = methods.iloc[0]
        self.assertEqual(row["有效整车样本数"], 1)
        self.assertAlmostEqual(row["平均整车成本"], 1000)
        self.assertEqual(row["供应商平均整车成本"], "A $800.00")
        self.assertEqual(row["供应商平均每方成本"], "A $12.50/方")
        self.assertAlmostEqual(row["平均每方成本"], ((200 + 200 / 3) / 20 + (600 + 400 / 3) / 40) / 2)

    def test_multi_stop_context_survives_fba_only_excel_and_rematching(self):
        rows = pd.DataFrame([
            batch("A", "T", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
            batch("B", "T", kind="FBX平台仓", code="16号仓", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
        ])
        marked = analysis.annotate_trip_context(rows)
        self.assertTrue(marked[analysis.METHOD].eq("多卸").all())
        workbook = tool_common.write_sheets_to_excel({"明细": marked.iloc[:1]})
        reread = pd.read_excel(workbook, sheet_name="明细")
        _, report = analysis.build_fba_destination_reports(reread)
        self.assertEqual(report.iloc[0]["派送方式"], "多卸")
        self.assertEqual(report.iloc[0]["总出库体积"], 50)
        self.assertEqual(report.iloc[0]["平均每方成本"], 10)
        self.assertTrue(pd.isna(report.iloc[0]["平均整车成本"]))
        # A matched correction replaces the old batch destination in context.
        corrected = marked.copy()
        corrected.loc[1, ["批次目的地类型", "主产品类型"]] = "FBA"
        corrected.loc[1, ["FBA仓点代码集合", "批次目的仓点"]] = "ONT8"
        self.assertTrue(analysis.annotate_trip_context(corrected)[analysis.METHOD].eq("整车大车卡板").all())

    def test_ltl_remains_ltl_three_destinations_and_incomplete_old_file_are_safe(self):
        rows = pd.DataFrame([
            batch(str(i), "T", code=code, 整车批次数=3, 批次车份额=1 / 3, 整车出库体积=150)
            for i, code in enumerate(["ONT8", "LAX9", "SBD1"])
        ])
        marked = analysis.annotate_trip_context(rows)
        self.assertTrue(marked[analysis.METHOD].eq("多卸").all())
        self.assertTrue(marked["车次目的地数"].eq(3).all())
        rows["标准运输类型"] = "LTL"
        self.assertTrue(analysis.annotate_trip_context(rows)[analysis.METHOD].eq("LTL").all())
        partial = pd.DataFrame([batch("A", "T", 整车批次数=2, 批次车份额=0.5)])
        _, report = analysis.build_fba_destination_reports(partial)
        self.assertEqual(report.iloc[0]["派送方式"], "待确认")
        self.assertTrue(pd.isna(report.iloc[0]["平均整车成本"]))

    def test_thresholds_keep_volume_but_filter_cost_samples_and_keep_golden_rule(self):
        rows = pd.DataFrame([
            batch("F60", "F60", volume=60, cost=600, 装车类型标准值="地板"),
            batch("F59", "F59", volume=59.99, cost=900, 装车类型标准值="地板"),
            batch("P40", "P40", volume=40, cost=400),
            batch("P39", "P39", volume=39.99, cost=900),
        ])
        _, report = analysis.build_fba_destination_reports(rows)
        floor = report.loc[report["派送方式"].eq("整车大车地板")].iloc[0]
        self.assertAlmostEqual(floor["总出库体积"], 119.99)
        self.assertEqual(floor["有效成本方数"], 60)
        self.assertEqual(floor["平均每方成本"], 10)
        self.assertEqual(floor["平均整车成本"], 600)
        pallet = report.loc[report["派送方式"].eq("整车大车卡板")].iloc[0]
        self.assertEqual(pallet["有效成本方数"], 40)
        self.assertEqual(pallet["平均整车成本"], 400)
        golden = delivery_match_adapter.build_golden_standard_batch_report(rows)
        self.assertEqual(len(golden), 1)  # Pallet 40 qualifies; floor 60 still not golden.

    def test_warehouse_period_and_supplier_conflicts_do_not_mix(self):
        rows = pd.DataFrame([
            batch("A", "T", cost=100, 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
            batch("B", "T", cost=900, supplier="B", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
            batch("C", "T", cost=300, 仓库="NJ"),
            batch("D", "U", cost=400, 统计周期="2026-09"),
        ])
        summary, methods = analysis.build_fba_destination_reports(rows)
        self.assertEqual(len(summary), 3)
        august = methods[(methods["仓库"] == "LA") & (methods["统计周期"] == "2026-08")].iloc[0]
        self.assertEqual(august["平均整车成本"], 1000)
        self.assertEqual(august["供应商平均整车成本"], "A 无有效样本；B 无有效样本")

    def test_cross_period_trip_does_not_generate_partial_whole_truck_price(self):
        rows = pd.DataFrame([
            batch("A", "T", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
            batch("B", "T", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100, 统计周期="2026-09"),
        ])
        _, methods = analysis.build_fba_destination_reports(rows)
        self.assertEqual(methods["总出库体积"].sum(), 100)
        self.assertTrue(methods["平均整车成本"].isna().all())

    def test_original_cost_zero_and_missing_are_not_silently_free_transport(self):
        rows = pd.DataFrame([
            batch("A", "A", volume=60, cost=0, 装车类型标准值="地板", 派送成本=200),
            batch("B", "B", volume=60, cost=None, 装车类型标准值="地板", 派送成本=800),
        ])
        summary, methods = analysis.build_fba_destination_reports(rows)
        self.assertEqual(summary.iloc[0]["总出库体积"], 120)
        self.assertEqual(summary.iloc[0]["有效成本方数"], 0)
        self.assertTrue(pd.isna(summary.iloc[0]["平均每方成本"]))
        self.assertEqual(methods.iloc[0]["供应商平均整车成本"], "A $800.00")
        self.assertEqual(methods.iloc[0]["原始成本缺失回退批次数"], 1)
        self.assertEqual(methods.iloc[0]["供应商成本覆盖率"], "A 50.00%")

    def test_trip_annotation_does_not_change_transfer_linehaul_or_golden_results(self):
        rows = pd.DataFrame([
            batch("A", "A", volume=80, 装车类型标准值="地板", 出库类型="调拨", 调入仓库="NJ", 业务场景="仓间调拨"),
            batch("B", "T", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
            batch("C", "T", 整车批次数=2, 批次车份额=0.5, 整车出库体积=100),
        ])
        before = rows.copy(deep=True)
        marked = analysis.annotate_trip_context(rows)
        analysis.build_fba_destination_reports(marked)
        for build in [delivery_runtime._build_transfer_report, delivery_audit_backfill._build_linehaul_sheet,
                      delivery_match_adapter.build_golden_standard_batch_report]:
            pd.testing.assert_frame_equal(build(before), build(marked))
        pd.testing.assert_frame_equal(rows, before)

    def test_timing_only_recognized_destinations_preserves_weighted_valid_samples(self):
        rows = pd.DataFrame([
            batch("A", "A", volume=10, 派送时效=1),
            batch("B", "B", volume=30, 派送时效=3),
            batch("C", "C", volume=100, 标准运输类型="LTL", 派送时效=20),
            batch("D", "", volume=100, 派送时效=20),
            batch("E", "E", kind="FBX平台仓", code="16号仓"),
            batch("F", "F", kind="仓间调拨", code="NJ"),
            batch("G", "G", kind="其他", code="12345"),
        ])
        report = analysis.build_station_timing_report(rows)
        self.assertEqual(set(report["目的仓点"]), {"ONT8", "16号仓"})
        ont8 = report.loc[report["目的仓点"].eq("ONT8")].iloc[0]
        self.assertEqual(ont8["平均派送时效"], 2.5)
        self.assertEqual(ont8["P80派送时效"], 3)
        self.assertEqual(ont8["有效时效批次数"], 2)
        self.assertEqual(ont8["无效时效批次数"], 2)

    def test_existing_file_pipeline_all_fba_fbx_and_excel_outputs(self):
        delivery_runtime.bootstrap(delivery_workflow)
        raw = pd.DataFrame([
            {"仓库": "LA", "派送方式": "卡车派送", "运输类型": "FTL", "车次号": "T",
             "批次号": name, "创建时间": "2026-07-01", "出库时间": "2026-08-01",
             "签收时间": "2026-08-03", "目的地": dest, "车型": "53尺大车",
             "装车类型": "地板", "出库体积": 30, "出库卡板数": 5,
             "派送成本": 300, "派送卡车": "Carrier"}
            for name, dest in [("A", "Amazon-ONT8"), ("B", "Amazon-ONT8")]
        ])
        cleaned, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches(
            [("input.xlsx", raw)], "LA")
        self.assertIn(analysis.TRIP_CONTEXT, cleaned)
        self.assertTrue(cleaned[analysis.METHOD].eq("整车大车地板").all())
        first = tool_common.write_sheets_to_excel({"派送一_清洗后批次数据": cleaned})
        reread = pd.read_excel(first, sheet_name="派送一_清洗后批次数据")
        reports = delivery_match_adapter.build_split_stage2_report(delivery_workflow, reread, pd.DataFrame(), "按月统计")
        self.assertEqual(reports["FBA仓点分析"].iloc[0]["统计周期"], "2026-08")
        self.assertEqual(reports["FBA派送方式分析"].iloc[0]["平均整车成本"], 800)
        self.assertEqual(reports["FBA派送方式分析"].iloc[0]["供应商平均整车成本"], "Carrier $600.00")
        self.assertEqual(reports["派送二_匹配后批次数据"]["派送成本"].sum(), 800)
        # Load only pure app helper definitions to exercise both UI branches.
        tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
        namespace = dict(pd=pd, processors=__import__("processors"), delivery_workflow=delivery_workflow,
                         delivery_match_adapter=delivery_match_adapter, delivery_destination_analysis=analysis,
                         tool_common=tool_common)
        names = {"_is_blank", "_text", "_numeric_value", "classify_delivery_destination_type", "filter_delivery_destination_type",
                 "rebuild_zip_audit_from_cleaned", "get_stage2_report_sheet_names", "_split_combined_report", "build_stage2_report_for_destination"}
        helper_tree = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        exec(compile(helper_tree, "app.py", "exec"), namespace)
        fba = namespace["build_stage2_report_for_destination"](reread, pd.DataFrame(), "按月统计", "FBA")
        pd.testing.assert_frame_equal(fba["FBA仓点分析"], reports["FBA仓点分析"])
        fbx = namespace["build_stage2_report_for_destination"](reread, pd.DataFrame(), "按月统计", "FBX")
        self.assertNotIn("FBA仓点分析", fbx)
        workbook = tool_common.write_sheets_to_excel(reports)
        exported = pd.read_excel(workbook, sheet_name="FBA派送方式分析")
        self.assertEqual(exported.iloc[0]["供应商使用比例"], "Carrier 100.00%")
        self.assertEqual(exported.iloc[0]["平均整车成本"], 800)


if __name__ == "__main__":
    unittest.main()
