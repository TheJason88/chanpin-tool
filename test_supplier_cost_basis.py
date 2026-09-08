import unittest

import pandas as pd

import delivery_audit_backfill
import delivery_match_adapter
import delivery_runtime
import processors
import tool_common


class SupplierCostBasisTests(unittest.TestCase):
    def test_original_cost_fallback_is_per_row_and_preserves_zero(self):
        for original, expected in [(9500, 9500), (None, 9700), (pd.NA, 9700),
                                   ("", 9700), ("invalid", 9700), ("9500", 9500),
                                   (0, None), (-1, None)]:
            with self.subTest(original=original):
                rows = pd.DataFrame([{
                    "仓库": "LA", "车次号": "T1", "派送卡车": "Carrier",
                    "派送成本": 9700, tool_common.BASE_DELIVERY_COST_COLUMN: original,
                }])
                snapshot = rows.copy(deep=True)
                result = processors.supplier_whole_truck_cost_summary(rows)
                self.assertEqual(result, ("", "") if expected is None else
                                 (f"Carrier ${expected:.2f}", "Carrier 100.00%"))
                pd.testing.assert_frame_equal(rows, snapshot)

        rows = pd.DataFrame([
            {"仓库": "LA", "车次号": "T1", "派送卡车": "Carrier",
             "派送成本": 9700, tool_common.BASE_DELIVERY_COST_COLUMN: 9500},
            {"仓库": "LA", "车次号": "T2", "派送卡车": "Carrier",
             "派送成本": 10000, tool_common.BASE_DELIVERY_COST_COLUMN: None},
        ])
        self.assertEqual(processors.supplier_whole_truck_cost_summary(rows)[0], "Carrier $9750.00")
        self.assertEqual(processors.supplier_whole_truck_cost_summary(
            rows.drop(columns=tool_common.BASE_DELIVERY_COST_COLUMN))[0], "Carrier $9850.00")
        self.assertEqual(processors.supplier_whole_truck_cost_summary(
            rows.iloc[:1].drop(columns="派送成本"))[0], "Carrier $9500.00")

    def test_reports_separate_supplier_cost_from_loaded_cost(self):
        rows = pd.DataFrame([{
            "仓库": "LA", "统计周期": "2026-07", "专线线路": "LA-NJ",
            "标准运输类型": "FTL", "是否有真实车次号": True, "是否FTL发车": True,
            "车次号": f"T{i}", "批次号集合": f"B{i}", "整车批次数": 1,
            "批次车份额": 1, "整车出库体积": 100, "出库体积": 100,
            "出库卡板数": 20, "派送成本": cost, "派送时效": 2,
            "派送卡车": "Carrier", "出库类型": "调拨", "调入仓库": "NJ",
            "业务场景": "仓间调拨", "车型标准值": "53尺大车",
            "装车类型标准值": loading, "主产品类型": "FBA", "FBA仓点代码集合": "ONT8",
        } for i, (cost, loading) in enumerate([(9500, "地板"), (10000, "卡板")])])
        loaded = tool_common.apply_floor_loading_fee(rows)
        self.assertEqual(loaded["派送成本"].tolist(), [9700, 10000])
        snapshot = loaded.copy(deep=True)
        transfer = delivery_runtime._build_transfer_report(loaded).iloc[0]
        linehaul = delivery_audit_backfill._build_linehaul_sheet(loaded)
        linehaul = linehaul.loc[linehaul["专线线路"] == "LA-NJ"].iloc[0]
        for report in [transfer, linehaul]:
            self.assertEqual(report["供应商平均整车成本"], "Carrier $9750.00")
            self.assertEqual(report["供应商使用比例"], "Carrier 100.00%")
            self.assertEqual(report["总派送成本"], 19700)
            self.assertEqual(report["平均整车价"], 9850)
            self.assertEqual(report["每方平均价"], 98.5)
        pd.testing.assert_frame_equal(loaded, snapshot)

        regular = loaded.iloc[:1].copy()
        regular["出库类型"] = "正常"
        regular["业务场景"] = "FBA派送"
        regular["调入仓库"] = ""
        report = delivery_match_adapter.build_station_cost_report(regular)
        report = report.loc[report["指标名称"] == "FBA及FBX平台仓成本"].iloc[0]
        self.assertEqual(report["平均整车价"], 9700)
        self.assertEqual(report["P80整车价"], 9700)
        self.assertEqual(report["每方平均价"], 97)
        self.assertEqual(report["总派送成本"], 9700)


if __name__ == "__main__":
    unittest.main()
