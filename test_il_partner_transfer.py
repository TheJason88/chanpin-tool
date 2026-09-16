import json
import unittest

import pandas as pd

import delivery_match_adapter
import delivery_runtime
import delivery_workflow
import tool_common


def partner_row(batch, trip="T1", volume=80, cost=4000, **extra):
    return {
        "仓库": "LA", "批次号": batch, "车次号": trip, "出库体积": volume,
        "出库卡板数": 20, "派送成本": cost, "派送卡车": "Carrier",
        "出库类型": "派送", "调入仓库": "", "目的地": "610 Supreme Dr. Bensenville, IL 60106",
        "派送方式": "卡车派送", "运输类型": "FTL", "车型": "53尺", "装车类型": "地板",
        "创建时间": "2026-08-30 12:00:00", "出库时间": "2026-09-01 12:00:00",
        "签收时间": "2026-09-04 12:00:00", "备注": "", **extra,
    }


class IlPartnerTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        delivery_runtime.bootstrap(delivery_workflow)

    def test_specific_address_or_batch_marker_only(self):
        for text in ["610 Supreme Dr. Bensenville, IL 60106", "610 SUPREME DRIVE, BENSENVILLE IL 60106-1234",
                     "610 Supreme Dr, Bensenville", "610 Supreme Drive IL 60106", "LA至IL合作仓", "IL 合作仓"]:
            with self.subTest(text=text):
                self.assertTrue(tool_common.is_il_partner_text(text))
                self.assertEqual(tool_common.transfer_route_from_row(pd.Series(partner_row("B", 目的地=text))), "LA-IL")
        for text in ["IL", "60106", "Bensenville IL 60106", "1610 Supreme Dr IL 60106",
                     "610 Supreme Dr, Bensenville IL 60107", "610 Supreme Road IL 60106", "610 Supreme Dr"]:
            with self.subTest(text=text):
                self.assertFalse(tool_common.is_il_partner_text(text))
        for extra in [{"目的地": "普通地址 IL 60106"},
                      {"目的地": "Amazon-ORD2", "同车次备注集合": "IL合作仓"},
                      {"目的地": "Amazon-ORD2", "车次号": "IL合作仓"},
                      {"仓库": "NJ"}]:
            self.assertNotEqual(tool_common.transfer_route_from_row(pd.Series(partner_row("B", **extra))), "LA-IL")

    def test_pipeline_keeps_fbx_and_transfer_costs_separate(self):
        raw = pd.DataFrame([
            partner_row("FLOOR"),
            partner_row("PALLET", trip="T2", cost=4200, 装车类型="卡板", 目的地="商业地址-示例", 备注="IL合作仓"),
        ])
        clean, invalid, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        self.assertTrue(invalid.empty)
        self.assertTrue(clean["主产品类型"].eq("FBX").all())
        self.assertTrue(clean["批次目的地类型"].eq("FBX平台仓").all())
        self.assertEqual(clean["FBX出库体积"].sum(), 160)
        reports = delivery_match_adapter.build_split_stage2_report(delivery_workflow, clean, pd.DataFrame(), "按月统计")
        transfer = reports["调拨数据"]
        self.assertEqual(len(transfer), 1)
        row = transfer.iloc[0]
        self.assertEqual((row["专线线路"], row["调拨目标仓"], row["统计周期"]), ("LA-IL", "IL合作仓", "2026-09"))
        self.assertEqual(row["车次数"], 2)
        self.assertEqual(row["总出库体积"], 160)
        self.assertEqual(row["总派送成本"], 8400)
        self.assertEqual(row["平均整车价"], 4200)
        self.assertEqual(row["供应商平均整车价"], 4100)
        self.assertEqual(reports["FBX平台仓货量"]["出库体积"].sum(), 160)
        self.assertTrue(reports["FBA仓点分析"].empty)
        self.assertTrue(reports["邮编异常审核"].empty)
        matched = reports["派送二_匹配后批次数据"]
        self.assertTrue(matched["目的州"].eq("IL").all())
        self.assertTrue(matched["标准邮编集合"].eq("60106").all())
        self.assertTrue(matched["专线线路"].eq("LA-IL").all())
        self.assertEqual(sum(json.loads(v)[0]["出库体积"] for v in matched["目的仓点分配明细"]), 160)
        rerun = delivery_workflow.prepare_stage2_for_report(matched, pd.DataFrame(), "按月统计")
        self.assertEqual(rerun["派送成本"].sum(), 8400)
        self.assertEqual(rerun["原始派送成本"].sum(), 8200)
        self.assertEqual(rerun["FBX出库体积"].sum(), 160)
        exported = pd.read_excel(tool_common.write_sheets_to_excel(reports), sheet_name="调拨数据")
        self.assertEqual(exported.iloc[0]["供应商平均整车价"], 4100)

    def test_ordinary_same_zip_and_manual_matches_cannot_reassign_partner(self):
        raw = pd.DataFrame([
            partner_row("IL"),
            partner_row("OTHER", trip="T2", 目的地="999 Other Road, Bensenville, IL 60106"),
            partner_row("FBA", trip="T3", 目的地="Amazon-ORD2"),
        ])
        clean, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        manual = pd.DataFrame([{"批次号": "IL", "邮编": "08857", "目的州": "NJ", "平台名称": "TEMU", "FBX代码": "08857"}])
        matched = delivery_workflow.prepare_stage2_for_report(clean, manual, "按周统计")
        partner = matched.loc[matched["批次号"].eq("IL")].iloc[0]
        self.assertEqual(partner["标准邮编集合"], "60106")
        self.assertEqual(partner["平台名称"], "IL合作仓")
        self.assertEqual(partner["批次目的仓点"], "IL合作仓")
        transfer = delivery_runtime._build_transfer_report(matched)
        self.assertEqual(len(transfer), 1)
        self.assertEqual(transfer.iloc[0]["总出库体积"], 80)

    def test_same_trip_does_not_propagate_and_conflicting_batch_is_audited(self):
        raw = pd.DataFrame([partner_row("IL", volume=50), partner_row("FBA", volume=30, 目的地="Amazon-ORD2")])
        clean, invalid, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        self.assertTrue(invalid.empty)
        fba = clean.loc[clean["批次号"].eq("FBA")].iloc[0]
        self.assertEqual(fba["批次目的仓点"], "ORD2")
        self.assertEqual(fba["主产品类型"], "FBA")
        matched = delivery_workflow.prepare_stage2_for_report(clean, pd.DataFrame(), "按月统计")
        transfer = delivery_runtime._build_transfer_report(matched).iloc[0]
        self.assertEqual(transfer["总出库体积"], 50)
        self.assertTrue(pd.isna(transfer["供应商平均整车价"]))
        conflict = pd.DataFrame([partner_row("CONFLICT"), partner_row("CONFLICT", 调入仓库="NJ")])
        result = tool_common.apply_batch_transfer_destination_rules(conflict)
        self.assertEqual(len(tool_common.transfer_override_error_rows(result)), 2)

    def test_missing_trip_and_ltl_follow_existing_transfer_rules(self):
        raw = pd.DataFrame([partner_row("NO-TRIP", trip=None, cost=0),
                            partner_row("LTL", trip="LT", volume=10, cost=100, 运输类型="LTL")])
        clean, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        reports = delivery_match_adapter.build_split_stage2_report(delivery_workflow, clean, pd.DataFrame(), "按月统计")
        row = reports["调拨数据"].iloc[0]
        self.assertEqual(row["总出库体积"], 80)
        self.assertEqual(row["车次数"], 0)
        self.assertTrue(pd.isna(row.get("供应商平均整车价")))
        self.assertEqual(reports["FBX平台仓货量"]["出库体积"].sum(), 90)


if __name__ == "__main__":
    unittest.main()
