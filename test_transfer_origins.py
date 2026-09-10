"""Regression fixtures model the mixed NJ export without retaining customer records."""
import unittest

import pandas as pd

import delivery_audit_backfill
import delivery_match_adapter
import delivery_runtime
import delivery_workflow
import processors
import tool_common


def source_row(batch, volume=70, trip="T1", cost=2800, **extra):
    return {
        "仓库": "NJ", "批次号": batch, "出库体积": volume, "出库卡板数": 10,
        "车次号": trip, "派送成本": cost, "派送卡车": "Carrier",
        "出库类型": "调拨", "调入仓库": "萨凡纳盈仓", "目的地": "萨凡纳盈仓",
        "派送方式": "卡车派送", "运输类型": 1, "车型": "53尺", "装车类型": "地板",
        "是否转仓": False, "出库时间": "2026/08/10 12:00:00",
        "创建时间": "2026/08/09 12:00:00", "签收时间": None, "备注": "",
        **extra,
    }


class TransferOriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        delivery_runtime.bootstrap(delivery_workflow)

    def test_route_requires_transfer_evidence_and_uses_actual_origin(self):
        for warehouse, target, expected in [
            ("NJ", "SAV", "NJ-SAV"), ("新泽西", "SAV", "NJ-SAV"),
            ("LA", "SAV", "LA-SAV"), ("LA", "NJ", "LA-NJ"),
            ("LA", "DAL", "LA-DAL"), ("SAV", "NJ", "SAV-NJ"),
            ("NJ", "LA", "NJ-LA"), ("NJ", "NJ", ""), ("未知", "SAV", ""),
        ]:
            with self.subTest(warehouse=warehouse, target=target):
                row = pd.Series(source_row("B", 仓库=warehouse, 调入仓库=target))
                self.assertEqual(tool_common.transfer_route_from_row(row), expected)
                self.assertEqual(delivery_runtime._transfer_target_from_row(row), expected.split("-")[-1] if expected else "")
        ordinary = pd.Series(source_row("B", 出库类型="派送", 调入仓库="", 专线线路="NJ-SAV"))
        self.assertEqual(tool_common.transfer_route_from_row(ordinary), "")
        self.assertEqual(delivery_runtime._transfer_target_from_row(ordinary), "")
        self.assertEqual(processors.identify_delivery_line(ordinary)[0], "非LA干线")

    def test_mixed_sav_destinations_do_not_become_transfers_together(self):
        raw = pd.DataFrame([
            source_row("TRANSFER", trip="T1"),
            source_row("ORDINARY", trip="T2", 出库类型="派送", 调入仓库="", 目的地="盈仓-LINKW-萨凡纳"),
            source_row("PICKUP", trip="T3", 出库类型="派送", 调入仓库="", 派送方式="客户自提"),
        ])
        normalized = tool_common.apply_batch_transfer_destination_rules(raw)
        self.assertEqual(normalized["调拨目标仓代码"].fillna("").tolist(), ["SAV", "", ""])
        # Positive volume on the ordinary row proves exclusion is business-based,
        # not an accidental result of this user's ordinary rows lacking volume.
        normalized["标准运输类型"] = "FTL"
        normalized["是否FTL发车"] = True
        found = delivery_runtime._transfer_rows(normalized)
        self.assertEqual(found["批次号"].tolist(), ["TRANSFER"])
        self.assertEqual(found["专线线路"].tolist(), ["NJ-SAV"])

    def test_same_trip_transfer_and_fba_keep_their_own_destinations(self):
        raw = pd.DataFrame([
            source_row("TRANSFER", volume=50),
            source_row("FBA", volume=30, cost=700, 出库类型="派送", 调入仓库="", 目的地="Amazon-SAV4"),
        ])
        cleaned, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "NJ")
        matched = delivery_workflow.prepare_stage2_for_report(cleaned, pd.DataFrame(), "按月统计")
        fba = matched.loc[matched["批次号"].eq("FBA")].iloc[0]
        self.assertEqual(fba["批次目的仓点"], "SAV4")
        self.assertEqual(fba["主产品类型"], "FBA")
        transfer = delivery_runtime._build_transfer_report(matched).iloc[0]
        self.assertEqual(transfer["专线线路"], "NJ-SAV")
        self.assertEqual(transfer["总出库体积"], 50)
        self.assertTrue(pd.isna(transfer["平均整车价"]))
        self.assertEqual(transfer["供应商平均整车成本"], "")
        self.assertTrue(delivery_audit_backfill._build_linehaul_sheet(matched).empty)

    def test_monthly_pipeline_keeps_missing_trip_volume_and_excludes_zero_cost(self):
        raw = pd.DataFrame([
            source_row("A", volume=65.9846, trip="T1"),
            source_row("B", volume=72.1759, trip="T2"),
            source_row("C", volume=86.7207, trip="T3"),
            source_row("D", volume=86.374, trip="T4"),
            source_row("E", volume=57.7208, trip="T5"),
            source_row("F", volume=72.36, trip="T6", 创建时间="2026/08/31 20:00:00", 出库时间="2026/09/01 13:00:00"),
            source_row("G", volume=86.012, trip=None, cost=0, 出库时间="2026/09/08 16:00:00"),
            source_row("MISSING", volume=None, trip="T7"),
            source_row("ZERO", volume=0, trip="T8"),
        ])
        cleaned, invalid, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "NJ")
        self.assertEqual(len(cleaned), 7)
        self.assertEqual(set(invalid["批次号"]), {"MISSING", "ZERO"})
        self.assertTrue(cleaned["专线线路"].eq("NJ-SAV").all())
        reports = delivery_match_adapter.build_split_stage2_report(delivery_workflow, cleaned, pd.DataFrame(), "按月统计")
        transfer = reports["调拨数据"].set_index("统计周期")
        self.assertEqual(transfer.loc["2026-08", "车次数"], 5)
        self.assertEqual(transfer.loc["2026-08", "总出库体积"], 368.98)
        self.assertEqual(transfer.loc["2026-08", "总派送成本"], 15000)
        self.assertEqual(transfer.loc["2026-08", "平均整车价"], 3000)
        self.assertEqual(transfer.loc["2026-08", "供应商平均整车成本"], "Carrier $2800.00")
        self.assertEqual(transfer.loc["2026-09", "车次数"], 1)
        self.assertEqual(transfer.loc["2026-09", "总出库体积"], 158.37)
        self.assertEqual(transfer.loc["2026-09", "每方平均价"], round(3000 / 72.36, 2))
        self.assertTrue(reports["派送时效"].empty)
        self.assertTrue(reports["FBA仓点分析"].empty)
        matched = reports["派送二_匹配后批次数据"]
        self.assertTrue(matched["专线线路"].eq("NJ-SAV").all())
        # Exercise the existing Excel exporter as part of the application path.
        workbook = tool_common.write_sheets_to_excel(reports)
        exported = pd.read_excel(workbook, sheet_name="调拨数据")
        self.assertTrue(exported["专线线路"].eq("NJ-SAV").all())
        self.assertEqual(exported["车次数"].sum(), 6)

    def test_ltl_is_not_counted_as_ftl_transfer_and_missing_trip_is_totals_only(self):
        raw = pd.DataFrame([
            source_row("NO-TRIP", trip=None, volume=70, cost=0),
            source_row("LTL", trip="LT", volume=10, cost=100, 运输类型="LTL"),
        ])
        cleaned, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "NJ")
        matched = delivery_workflow.prepare_stage2_for_report(cleaned, pd.DataFrame(), "按月统计")
        row = delivery_runtime._build_transfer_report(matched).iloc[0]
        self.assertEqual(row["车次数"], 0)
        self.assertEqual(row["总出库体积"], 70)
        self.assertTrue(pd.isna(row["平均整车价"]))
        self.assertTrue(pd.isna(row["每方平均价"]))


if __name__ == "__main__":
    unittest.main()
