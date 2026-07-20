import unittest

import pandas as pd

import delivery_audit_backfill
import delivery_match_adapter
import delivery_runtime
import delivery_workflow


class MultiUnloadAverageTests(unittest.TestCase):
    def setUp(self):
        self.rows = pd.DataFrame(
            [
                {
                    "仓库": "LA", "统计周期": "2026-W28", "专线线路": "LA-NJ",
                    "是否FTL发车": True, "出库体积": 50, "出库卡板数": 2,
                    "派送成本": 500, "派送时效": 2, "匹配备注集合": "正常",
                    "批次号集合": "A", "车次号": "T1", "出库类型": "调拨",
                    "调入仓库": "NJ", "业务场景": "仓间调拨",
                },
                {
                    "仓库": "LA", "统计周期": "2026-W28", "专线线路": "LA-NJ",
                    "是否FTL发车": True, "出库体积": 60, "出库卡板数": 4,
                    "派送成本": 600, "派送时效": 20, "匹配备注集合": "里外两卸",
                    "批次号集合": "B", "车次号": "T2", "出库类型": "调拨",
                    "调入仓库": "NJ", "业务场景": "仓间调拨",
                },
            ]
        )

    def test_linehaul_totals_include_both_but_averages_exclude_marked_row(self):
        report = delivery_audit_backfill._build_linehaul_sheet(self.rows)
        row = report.loc[report["专线线路"] == "LA-NJ"].iloc[0]
        self.assertEqual(row["车次数"], 2)
        self.assertEqual(row["总出库体积"], 110)
        self.assertEqual(row["总派送成本"], 1100)
        self.assertEqual(row["平均整车价"], 500)
        self.assertEqual(row["每方平均价"], 10)
        self.assertEqual(row["平均每车出库体积"], 50)
        self.assertEqual(row["平均派送时效"], 2)
        self.assertEqual(row["P80派送时效"], 2)

    def test_transfer_totals_include_both_but_averages_exclude_marked_row(self):
        report = delivery_runtime._build_transfer_report(self.rows)
        row = report.iloc[0]
        self.assertEqual(row["车次数"], 2)
        self.assertEqual(row["总出库体积"], 110)
        self.assertEqual(row["总派送成本"], 1100)
        self.assertEqual(row["平均整车价"], 500)
        self.assertEqual(row["每方平均价"], 10)
        self.assertEqual(row["平均每车出库体积"], 50)

    def test_either_marker_is_sufficient(self):
        for marker in ("里", "外", "里外"):
            rows = self.rows.copy()
            rows.loc[1, "匹配备注集合"] = marker
            report = delivery_audit_backfill._build_linehaul_sheet(rows)
            row = report.loc[report["专线线路"] == "LA-NJ"].iloc[0]
            self.assertEqual(row["平均派送时效"], 2)

    def test_stage1_and_stage2_keep_all_trip_remarks_as_last_column(self):
        detail = pd.DataFrame([
            {
                "原始行号": 2, "仓库": "LA", "标准运输类型": "FTL", "车次号": "T1", "批次号": "A",
                "出库时间": "2026-07-01", "签收时间": "2026-07-03", "出库体积": 10, "出库卡板数": 2,
                "派送成本": 100, "FBA/FBX": "FBX", "备注": "正常批次",
            },
            {
                "原始行号": 3, "仓库": "LA", "标准运输类型": "FTL", "车次号": "T1", "批次号": "B",
                "出库时间": "2026-07-01", "签收时间": "2026-07-03", "出库体积": 30, "出库卡板数": 4,
                "派送成本": 600, "FBA/FBX": "FBX", "备注": "里仓两卸",
            },
        ])
        stage1 = delivery_workflow.build_cleaned_batches_from_detail(detail)
        self.assertEqual(stage1.columns[-1], "备注")
        self.assertIn("正常批次", stage1.iloc[0]["备注"])
        self.assertIn("里仓两卸", stage1.iloc[0]["备注"])

        delivery_runtime.bootstrap(delivery_workflow)
        stage2 = delivery_workflow.prepare_stage2_for_report(stage1, pd.DataFrame(), "按周统计")
        self.assertEqual(stage2.columns[-1], "同车次备注集合")
        self.assertIn("里仓两卸", stage2.iloc[0]["同车次备注集合"])

        exported = delivery_match_adapter._finalize_sheet(stage2, "明细")
        self.assertEqual(exported.columns[-1], "同车次备注集合")
        self.assertIn("里仓两卸", exported.iloc[0]["同车次备注集合"])

    def test_stage1_remark_marker_excludes_entire_trip_from_linehaul_averages(self):
        rows = self.rows.copy()
        rows["同车次备注集合"] = ["正常", "外仓两卸"]
        rows["匹配备注集合"] = ""
        report = delivery_audit_backfill._build_linehaul_sheet(rows)
        row = report.loc[report["专线线路"] == "LA-NJ"].iloc[0]
        self.assertEqual(row["车次数"], 2)
        self.assertEqual(row["总出库体积"], 110)
        self.assertEqual(row["平均每车出库体积"], 50)
        self.assertEqual(row["平均派送时效"], 2)
        self.assertEqual(row["P80派送时效"], 2)

    def test_under_45_cbm_trip_is_totals_only_and_45_is_eligible(self):
        rows = self.rows.copy()
        rows["匹配备注集合"] = "正常"
        rows["同车次备注集合"] = "正常"
        rows[["出库体积", "派送成本", "派送时效"]] = rows[["出库体积", "派送成本", "派送时效"]].astype(float)
        rows.loc[0, ["出库体积", "派送成本", "派送时效"]] = [45, 450, 2]
        rows.loc[1, ["出库体积", "派送成本", "派送时效"]] = [44.99, 899.8, 20]

        linehaul = delivery_audit_backfill._build_linehaul_sheet(rows)
        linehaul_row = linehaul.loc[linehaul["专线线路"] == "LA-NJ"].iloc[0]
        self.assertEqual(linehaul_row["车次数"], 2)
        self.assertEqual(linehaul_row["总出库体积"], 89.99)
        self.assertEqual(linehaul_row["总派送成本"], 1349.8)
        self.assertEqual(linehaul_row["平均整车价"], 450)
        self.assertEqual(linehaul_row["每方平均价"], 10)
        self.assertEqual(linehaul_row["平均每车出库体积"], 45)
        self.assertEqual(linehaul_row["平均派送时效"], 2)
        self.assertEqual(linehaul_row["P80派送时效"], 2)

        transfer = delivery_runtime._build_transfer_report(rows).iloc[0]
        self.assertEqual(transfer["车次数"], 2)
        self.assertAlmostEqual(transfer["总出库体积"], 89.99, places=2)
        self.assertEqual(transfer["平均整车价"], 450)
        self.assertEqual(transfer["每方平均价"], 10)
        self.assertEqual(transfer["平均每车出库体积"], 45)


if __name__ == "__main__":
    unittest.main()
