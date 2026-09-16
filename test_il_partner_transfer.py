import json
import unittest

import pandas as pd

import delivery_match_adapter
import delivery_runtime
import delivery_workflow
import tool_common
from il_partner_transfer import resolve_partner_endpoints, AUDIT_COLUMN


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

    def test_two_unloads_resolve_batch_by_address_without_using_so_volume(self):
        remark = "Carrier IL安克(外)+IL合作仓(里)"
        raw = pd.DataFrame([partner_row("PARTNER", volume=17, 目的地="商业地址-A", 备注=remark),
                            partner_row("ANKER", volume=43, 目的地="商业地址-B", 备注=remark)])
        clean, invalid, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        self.assertTrue(invalid.empty)
        self.assertFalse(clean["调拨目标仓代码"].eq("IL").any())
        match = pd.DataFrame([
            {"批次号": "PARTNER", "地址": "610 Supreme Dr", "城市": "Bensenville", "邮编": "60106", "方数": 12},
            {"批次号": "PARTNER", "地址": "Another final destination", "邮编": "60487", "方数": 5},
            {"批次号": "ANKER", "地址": "Other address", "邮编": "60440", "备注": "安克 CHI仓"},
        ])
        matched = delivery_workflow.prepare_stage2_for_report(clean, match, "按月统计")
        il = matched.loc[matched["调拨目标仓代码"].eq("IL")]
        self.assertEqual(il["批次号"].tolist(), ["PARTNER"])
        self.assertEqual(il["出库体积"].sum(), 17)
        self.assertEqual(matched.loc[matched["批次号"].eq("ANKER"), "批次目的仓点"].iloc[0], "商业地址-B")
        report = delivery_runtime._build_transfer_report(matched)
        self.assertEqual(report.iloc[0]["总出库体积"], 17)
        self.assertTrue(pd.isna(report.iloc[0].get("供应商平均整车价")))
        rerun = delivery_workflow.prepare_stage2_for_report(matched, pd.concat([match, match]), "按月统计")
        self.assertEqual(rerun.loc[rerun["调拨目标仓代码"].eq("IL"), "出库体积"].sum(), 17)

    def test_two_unloads_can_resolve_opposite_stop_but_never_unrelated_trip(self):
        for label, evidence in [("安克", {"备注": "安克 CHI仓"}), ("MI", {"省/州": "MI"})]:
            remark = f"IL合作仓(外)+{label}(里)"
            raw = pd.DataFrame([partner_row("A", 目的地="普通地址-A", 备注=remark),
                                partner_row("B", 目的地="普通地址-B", 备注=remark)])
            match = pd.DataFrame([{"批次号": "B", **evidence}])
            result = resolve_partner_endpoints(raw, match)
            self.assertEqual(result.loc[result["调拨目标仓代码"].eq("IL"), "批次号"].tolist(), ["A"])
            raw.loc[1, "车次号"] = "DIFFERENT"
            result = resolve_partner_endpoints(raw, match)
            self.assertTrue(result[AUDIT_COLUMN].str.startswith("待核对").all())

    def test_ambiguous_and_conflicting_two_unloads_stay_unassigned(self):
        raw = pd.DataFrame([partner_row("A", 目的地="普通地址-A", 备注="安克+IL合作仓"),
                            partner_row("B", 目的地="普通地址-B", 备注="安克+IL合作仓")])
        for match in [pd.DataFrame(), pd.DataFrame([
            {"批次号": "A", "地址": "610 Supreme Dr IL 60106"},
            {"批次号": "B", "地址": "610 Supreme Dr IL 60106"}])]:
            result = tool_common.apply_batch_transfer_destination_rules(resolve_partner_endpoints(raw, match))
            self.assertFalse(result["调拨目标仓代码"].eq("IL").any())
            self.assertTrue(result[AUDIT_COLUMN].str.startswith("待核对").all())
        raw.loc[1, "调入仓库"] = "NJ"
        result = resolve_partner_endpoints(raw, pd.DataFrame([{"批次号": "B", "备注": "安克"}]))
        self.assertEqual(result.loc[1, "调入仓库"], "NJ")

    def test_partner_full_truck_ignores_final_so_destination_and_keeps_both_batches(self):
        raw = pd.DataFrame([partner_row("A", volume=20, 目的地="TikTok-FC10_ORD2", 备注="IL合作仓整车卡板"),
                            partner_row("B", volume=35, 目的地="TikTok-IND3", 备注="IL合作仓整车卡板")])
        match = pd.DataFrame([{"批次号": "A", "地址": "Other final address", "邮编": "60440"}])
        clean, _, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([("input.xlsx", raw)], "LA")
        matched = delivery_workflow.prepare_stage2_for_report(clean, match, "按月统计")
        self.assertTrue(matched["调拨目标仓代码"].eq("IL").all())
        self.assertEqual(matched["出库体积"].sum(), 55)


if __name__ == "__main__":
    unittest.main()
