from io import BytesIO
import unittest

import pandas as pd
from openpyxl import load_workbook

import delivery_audit_backfill as backfill
import delivery_match_adapter
import delivery_report_export as export
import delivery_runtime
import delivery_workflow
import tool_common
from test_destination_analysis import batch


class CompactExportTests(unittest.TestCase):
    def reports(self):
        delivery_runtime.bootstrap(delivery_workflow)
        backfill.apply_linehaul_market_rules()
        backfill.apply_stage2_linehaul_sheet_patch()
        rows = pd.DataFrame([
            batch("A", "T1", 60, 600),
            batch("B", "", 20, 100, 标准运输类型="LTL", 是否FTL发车=False),
            batch("C", "T3", 80, 800, kind="FBX平台仓", code="16号仓"),
            batch("D", "T4", 80, 800, kind="仓间调拨", code="NJ", 调入仓库="NJ", 出库类型="调拨"),
        ])
        return delivery_match_adapter.build_split_stage2_report(delivery_workflow, rows, pd.DataFrame(), "按月统计")

    def test_compact_values_and_protected_tables(self):
        reports = self.reports()
        snapshots = {k: v.copy(deep=True) for k, v in reports.items()}
        business, audit = export.build_delivery_exports(reports)
        self.assertLessEqual(len(business), 9)
        self.assertEqual(list(audit), list(export.AUDIT_SHEETS) + ["规则说明"])
        self.assertNotIn("邮编异常审核", business)
        fba = business["FBA仓点总览"].iloc[0]
        old = reports["FBA仓点分析"].iloc[0]
        self.assertEqual(fba["总出库体积"], old["总出库体积"])
        self.assertEqual(fba["批次平均每方成本"], old["平均每方成本"])
        reference = reports["每方价格参考"].query("对象类型 == 'FBA'").iloc[0]
        self.assertEqual(fba["每方参考价（总成本÷总方数）"], reference["每方价格参考"])
        self.assertNotEqual(fba["每方参考价（总成本÷总方数）"], fba["批次平均每方成本"])
        timing = reports["派送时效"].query("目的地类型 == 'FBA'").iloc[0]
        for col in ["平均派送时效", "P80派送时效", "有效时效方数"]:
            self.assertEqual(fba[col], timing[col])
        for name in ["FBA派送方式分析", "调拨数据", "干线数据", "黄金标准数据"]:
            if name in business:
                if name == "干线数据":
                    self.assertTrue(business[name]["货量口径"].str.contains("有效FTL").all())
                    pd.testing.assert_frame_equal(business[name].drop(columns="货量口径"), reports[name])
                else:
                    pd.testing.assert_frame_equal(business[name], reports[name])
        for name in export.AUDIT_SHEETS:
            pd.testing.assert_frame_equal(audit[name], reports[name])
        for name in reports:
            pd.testing.assert_frame_equal(reports[name], snapshots[name])

    def test_load_factor_is_trip_weighted_and_capped(self):
        rows = pd.DataFrame([
            {
                "仓库": "LA", "统计周期": "1月", "车次号": "T1",
                "是否有真实车次号": True, "是否FTL发车": True,
                "车型标准值": "53尺大车", "装车类型标准值": "卡板", "出库体积": 62,
            },
            {
                "仓库": "LA", "统计周期": "1月", "车次号": "T2",
                "是否有真实车次号": True, "是否FTL发车": True,
                "车型标准值": "53尺大车", "装车类型标准值": "卡板", "出库体积": 30,
            },
            {
                "仓库": "LA", "统计周期": "1月", "车次号": "T3",
                "是否有真实车次号": True, "是否FTL发车": True,
                "车型标准值": "53尺大车", "装车类型标准值": "地板", "出库体积": 95,
            },
        ])
        business, _ = export.build_delivery_exports({"派送二_匹配后批次数据": rows})
        result = business["满载率与地板率"].iloc[0]
        self.assertAlmostEqual(result["大车卡板满载率"], 0.75)
        self.assertAlmostEqual(result["大车地板满载率"], 1.0)
        self.assertAlmostEqual(result["满载率"], (1.0 + 0.5 + 1.0) / 3)
        self.assertAlmostEqual(result["地板率"], 1 / 3)
        self.assertEqual(result["满载率加权车次"], 3)

    def test_excel_roundtrip_and_explicit_wrong_file_error(self):
        business, audit = export.build_delivery_exports(self.reports())
        out = tool_common.write_sheets_to_excel(business)
        wb = load_workbook(out)
        self.assertEqual(wb.sheetnames, list(business))
        self.assertEqual(wb["FBA仓点总览"].freeze_panes, "C2")
        self.assertTrue(wb["FBA仓点总览"].auto_filter.ref)
        out.seek(0)
        with self.assertRaisesRegex(ValueError, "审核明细"):
            backfill.read_stage1_or_stage2_with_audit_updates(out)
        main = audit["派送二_匹配后批次数据"]
        audit["邮编异常审核"] = pd.DataFrame([{
            "批次号集合": "A", "补充标准邮编": "07001", "补充目的州": "NJ",
        }])
        reread = backfill.read_stage1_or_stage2_with_audit_updates(tool_common.write_sheets_to_excel(audit))
        self.assertEqual(len(reread), len(main))
        self.assertIn("07001", str(reread.loc[reread["批次号集合"].eq("A"), "标准邮编集合"].iloc[0]))
        regenerated = delivery_match_adapter.build_split_stage2_report(delivery_workflow, reread, pd.DataFrame(), "按月统计")
        next_business, _ = export.build_delivery_exports(regenerated)
        self.assertEqual(next_business["FBA仓点总览"]["总出库体积"].sum(), business["FBA仓点总览"]["总出库体积"].sum())

    def test_platform_and_period_identity_never_cross_joins(self):
        rows = pd.DataFrame([{"仓库": "LA", "统计周期": month, "平台仓": platform, "FBX代码": "16号仓",
                              "出库体积": volume, "排名": 1, "占比": 1}
                             for month, platform, volume in [("8月", "A", 10), ("8月", "B", 20), ("9月", "A", 30)]])
        business, _ = export.build_delivery_exports({"FBX平台仓货量": rows})
        self.assertEqual(len(business["FBX仓点总览"]), 3)
        self.assertEqual(business["FBX仓点总览"]["总出库体积"].sum(), 60)
        self.assertNotIn("FBA仓点总览", business)
        self.assertNotIn("调拨数据", business)

    def test_duplicate_keys_fail_instead_of_multiplying_volume(self):
        rows = pd.DataFrame([{"仓库": "LA", "统计周期": "8月", "FBA仓点": "ONT8", "出库体积": 10}] * 2)
        business, _ = export.build_delivery_exports({"FBA货量排行": rows})
        self.assertEqual(len(business["FBA仓点总览"]), 1)
        self.assertEqual(business["FBA仓点总览"].iloc[0]["总出库体积"], 20)

    def test_fbx_aliases_and_unidentified_volume_reconcile(self):
        rank = pd.DataFrame([
            {"仓库": "LA", "统计周期": "8月", "平台仓": "TikTok", "FBX代码": "XD01_ONT1", "出库体积": 443, "派送卡车使用比例": "A 100.00%"},
            {"仓库": "LA", "统计周期": "8月", "平台仓": "TikTok", "FBX代码": "XD01_ONT1", "出库体积": 217, "派送卡车使用比例": "B 100.00%"},
            {"仓库": "LA", "统计周期": "8月", "平台仓": "运去哪仓", "FBX代码": "CPF", "出库体积": 40},
            {"仓库": "LA", "统计周期": "8月", "平台仓": "运去哪", "FBX代码": "CPF", "出库体积": 60},
        ])
        price = pd.DataFrame([{
            "仓库": "LA", "统计周期": "8月", "对象类型": "FBX平台仓", "平台": "TikTok",
            "仓点代码": "XD01_ONT1", "总出库体积": 100, "总出库卡板数": 10,
            "总派送成本": 1000, "每方价格参考": 10,
        }])
        timing = pd.DataFrame([{
            "仓库": "LA", "统计周期": "8月", "目的地类型": "FBX平台仓", "平台名称": "TikTok",
            "目的仓点": "XD01_ONT1", "平均派送时效": 2, "P80派送时效": 3,
            "有效时效批次数": 2, "有效时效方数": 100, "无效时效批次数": 0,
        }])
        volume = pd.DataFrame([
            {"仓库": "LA", "统计周期": "8月", "指标名称": "FBA比FBX方数", "维度类型": "产品类型", "维度值": "FBX", "数值": 1000, "单位": "CBM"},
        ])
        business, _ = export.build_delivery_exports({
            "FBX平台仓货量": rank, "货量": volume, "每方价格参考": price, "派送时效": timing,
        })
        result = business["FBX仓点总览"]
        xd = result[result["FBX仓点"].eq("XD01_ONT1")].iloc[0]
        self.assertEqual(xd["总出库体积"], 660)
        self.assertEqual(xd["每方参考价（总成本÷总方数）"], 10)
        self.assertEqual(xd["平均派送时效"], 2)
        self.assertIn("A 67.12%", xd["供应商货量占比"])
        self.assertEqual(len(result[result["FBX仓点"].eq("CPF")]), 1)
        self.assertAlmostEqual(result["总出库体积"].sum(), 1000)
        residual = result[result["记录类型"].eq("非平台/未知目的地汇总")].iloc[0]
        self.assertEqual(residual["总出库体积"], 240)

    def test_platform_specific_dispatch_moves_to_each_fbx_station(self):
        rank = pd.DataFrame([
            {"仓库": "LA", "统计周期": "8月", "平台仓": "A", "FBX代码": "92571", "出库体积": 10},
            {"仓库": "LA", "统计周期": "8月", "平台仓": "B", "FBX代码": "92571", "出库体积": 20},
        ])
        dispatch = pd.DataFrame([
            {"仓库": "LA", "统计周期": "8月", "指标名称": "目的仓点发车数", "维度类型": "目的仓点", "维度值": "92571", "平台名称": "A", "数值": 1, "单位": "车"},
            {"仓库": "LA", "统计周期": "8月", "指标名称": "目的仓点发车数", "维度类型": "目的仓点", "维度值": "92571", "平台名称": "B", "数值": 2, "单位": "车"},
        ])
        business, _ = export.build_delivery_exports({"FBX平台仓货量": rank, "发车量": dispatch})
        result = business["FBX仓点总览"].sort_values("平台名称")
        self.assertEqual(result["FTL折算发车数"].tolist(), [1, 2])
        self.assertNotIn("派送总览", business)

    def test_fractional_truck_count_and_distinct_prices_survive_excel(self):
        rows = pd.DataFrame([{"仓库": "LA", "统计周期": "8月", "成本计算类型": "大车卡板",
            "总出库体积": 10, "细分货量方数": 10, "车次数": 0.5,
            "整车价格": 200, "平均整车价": 200, "P80整车价": 250,
            "每方成本": 12, "每方平均价": 15}])
        business, _ = export.build_delivery_exports({"分类型价格参考": rows})
        result = pd.read_excel(tool_common.write_sheets_to_excel(business), sheet_name="分类价格参考")
        self.assertEqual(result.iloc[0]["车次数"], 0.5)
        self.assertEqual(result.iloc[0]["批次平均每方成本"], 15)
        self.assertEqual(result.iloc[0]["每方参考价（总成本÷总方数）"], 12)
        self.assertNotIn("整车价格", result)
        self.assertNotIn("细分货量方数", result)


if __name__ == "__main__":
    unittest.main()
