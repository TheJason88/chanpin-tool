import io
import json
import unittest

import pandas as pd

import delivery_stage3


def _stage2_row(batch, cost, warehouse="LA", scenario="普通派送", station="ABE8", volume=40.0):
    allocation = [{
        "对象类型": "FBA",
        "平台": "FBA",
        "仓点代码": station,
        "批次号": batch,
        "出库体积": volume,
        "派送成本": cost,
    }]
    return {
        "仓库": warehouse,
        "批次号": batch,
        "批次号集合": batch,
        "业务场景": scenario,
        "出库类型": scenario,
        "主产品类型": "FBA",
        "批次数据是否有效": True,
        "目的仓点分配明细": json.dumps(allocation, ensure_ascii=False),
        "仓点派送方式": "整车大车卡板",
        "派送方式": "53尺大车-卡板",
        "派送卡车": "Direct Carrier",
        "派送成本": cost,
        "出库体积": volume,
        "FBA出库体积": volume,
        "专线线路": "LA-NJ",
    }


class DeliveryStage3Tests(unittest.TestCase):
    def _files(self, stage2, transfer, batches=None):
        stage2_file = io.BytesIO()
        with pd.ExcelWriter(stage2_file, engine="openpyxl") as writer:
            stage2.to_excel(writer, index=False, sheet_name=delivery_stage3.STAGE2_DETAIL_SHEET)
        stage2_file.seek(0)
        transfer_file = io.BytesIO()
        with pd.ExcelWriter(transfer_file, engine="openpyxl") as writer:
            transfer.to_excel(writer, index=False, sheet_name=delivery_stage3.TRANSFER_DETAIL_SHEET)
            if batches is not None:
                batches.to_excel(writer, index=False, sheet_name=delivery_stage3.TRANSFER_BATCH_SHEET)
        transfer_file.seek(0)
        return stage2_file, transfer_file

    def _transfer_rows(self):
        return pd.DataFrame([
            {"调拨线路": "LA→NJ", "出库批次号": "T1", "仓点": "ABE8", "平台": "Amazon", "方数": 30.0},
            {"调拨线路": "LA→NJ", "出库批次号": "T1", "仓点": "TEB9", "平台": "Amazon", "方数": 20.0},
            {"调拨线路": "LA→NJ", "出库批次号": "T1", "仓点": "FBX-1", "平台": "TEMU", "方数": 10.0},
        ])

    def test_combines_direct_and_transfer_and_calculates_real_cost(self):
        stage2 = pd.DataFrame([
            _stage2_row("D1", 400.0),
            _stage2_row("T1", 600.0, scenario="仓间调拨", volume=60.0),
        ])
        stage2_file, transfer_file = self._files(stage2, self._transfer_rows())
        reports = delivery_stage3.build_delivery3_reports(stage2_file, transfer_file, "LA")

        station = reports["派送三_仓点总览"].set_index("FBA仓点")
        self.assertAlmostEqual(station.loc["ABE8", "直发方数"], 40.0)
        self.assertAlmostEqual(station.loc["ABE8", "调拨方数"], 30.0)
        self.assertAlmostEqual(station.loc["ABE8", "总方数"], 70.0)
        self.assertAlmostEqual(station.loc["ABE8", "调拨成本"], 300.0)
        self.assertAlmostEqual(station.loc["ABE8", "真实每方成本"], 10.0)
        methods = reports["派送三_派送方式"]
        transfer_method = methods[(methods["FBA仓点"] == "ABE8") & (methods["派送方式"] == "调拨")]
        self.assertEqual(len(transfer_method), 1)
        self.assertAlmostEqual(transfer_method.iloc[0]["方数"], 30.0)
        self.assertNotIn("T1", set(reports["派送三_批次明细"].query("来源 == '直发'")["批次号"]))

    def test_prefers_cost_carried_by_transfer_batch_summary(self):
        stage2 = pd.DataFrame([_stage2_row("T1", 600.0, scenario="仓间调拨", volume=60.0)])
        batches = pd.DataFrame([{"出库批次号": "T1", "批次总调拨成本": 1200.0}])
        stage2_file, transfer_file = self._files(stage2, self._transfer_rows(), batches)
        reports = delivery_stage3.build_delivery3_reports(stage2_file, transfer_file, "LA")
        detail = reports["派送三_批次明细"].set_index("FBA仓点")
        self.assertAlmostEqual(detail.loc["ABE8", "成本"], 600.0)
        self.assertEqual(detail.loc["ABE8", "成本来源"], "调拨明细批次总成本按方数分摊")

    def test_prefers_complete_station_costs_from_transfer_detail(self):
        stage2 = pd.DataFrame([_stage2_row("T1", 600.0, scenario="仓间调拨", volume=60.0)])
        transfer = self._transfer_rows()
        transfer["仓点分摊调拨成本"] = [330.0, 220.0, 110.0]
        stage2_file, transfer_file = self._files(stage2, transfer)
        reports = delivery_stage3.build_delivery3_reports(stage2_file, transfer_file, "LA")
        detail = reports["派送三_批次明细"].set_index("FBA仓点")
        self.assertAlmostEqual(detail.loc["ABE8", "成本"], 330.0)
        self.assertEqual(detail.loc["ABE8", "成本来源"], "调拨明细仓点分摊成本")

    def test_accepts_transfer_cost_per_cubic_meter(self):
        stage2 = pd.DataFrame([_stage2_row("T1", 600.0, scenario="仓间调拨", volume=60.0)])
        transfer = self._transfer_rows()
        transfer["调拨成本/方"] = [11.0, 11.0, 11.0]
        stage2_file, transfer_file = self._files(stage2, transfer)
        reports = delivery_stage3.build_delivery3_reports(stage2_file, transfer_file, "LA")
        detail = reports["派送三_批次明细"].set_index("FBA仓点")
        self.assertAlmostEqual(detail.loc["ABE8", "成本"], 330.0)
        self.assertEqual(detail.loc["ABE8", "成本来源"], "调拨明细仓点每方成本")

    def test_missing_transfer_cost_keeps_volume_out_of_cost_denominator(self):
        stage2 = pd.DataFrame([_stage2_row("D1", 400.0)])
        transfer = pd.DataFrame([
            {"调拨线路": "LA→NJ", "出库批次号": "MISSING", "仓点": "ABE8", "平台": "Amazon", "方数": 30.0},
            {"调拨线路": "NJ→LA", "出库批次号": "OTHER", "仓点": "ONT8", "平台": "Amazon", "方数": 90.0},
        ])
        stage2_file, transfer_file = self._files(stage2, transfer)
        reports = delivery_stage3.build_delivery3_reports(stage2_file, transfer_file, "LA")
        station = reports["派送三_仓点总览"].set_index("FBA仓点")
        self.assertAlmostEqual(station.loc["ABE8", "总方数"], 70.0)
        self.assertAlmostEqual(station.loc["ABE8", "成本有效方数"], 40.0)
        self.assertAlmostEqual(station.loc["ABE8", "真实每方成本"], 10.0)
        self.assertAlmostEqual(station.loc["ABE8", "真实成本覆盖率"], 40.0 / 70.0)
        self.assertNotIn("ONT8", station.index)


if __name__ == "__main__":
    unittest.main()
