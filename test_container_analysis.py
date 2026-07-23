import unittest

import pandas as pd

import processors


def container_row(
    container_no,
    warehouse="LA",
    delivery_method="拆送",
    eta="2026-01-01",
    available="2026-01-02",
    pickup="2026-01-03",
    arrival="2026-01-04",
    unload="2026-01-05",
    customer="联宇客户",
    channel="T1",
):
    return {
        "仓库": warehouse,
        "柜号": container_no,
        "派送方式": delivery_method,
        "ETA": eta,
        "Available时间": available,
        "提柜时间": pickup,
        "实际抵仓时间": arrival,
        "拆柜完成时间": unload,
        "客户名称": customer,
        "产品渠道": channel,
    }


class ContainerAnalysisTests(unittest.TestCase):
    def test_selected_time_dimension_controls_filter_dedup_and_week(self):
        source = pd.DataFrame([
            container_row(
                "MSCU1234567",
                pickup="2026-07-07",
                available="2026-07-06",
                arrival="2026-07-08",
                unload="2026-07-10",
            ),
            container_row(
                "MSCU1234567",
                pickup="2026-07-08",
                available="2026-07-07",
                arrival="2026-07-09",
                unload="2026-07-11",
            ),
            container_row(
                "TGHU7654321",
                delivery_method="拆柜",
                pickup="2026-07-09",
                available="2026-07-09",
                arrival="2026-07-09",
                unload="2026-07-10",
                customer="ACME",
                channel="T2",
            ),
            container_row(
                "OOLU1111111",
                delivery_method="直送",
                pickup="2026-07-10",
                available="2026-07-09",
                arrival="2026-07-11",
                unload="2026-07-12",
            ),
        ])

        detail, summary = processors.process_container_analysis(
            source,
            warehouse="LA",
            period_type="按周统计",
            time_dimension="提柜时间",
            start_date="2026-07-06",
            end_date="2026-07-12",
        )

        self.assertEqual(detail["标准柜号"].tolist(), ["MSCU1234567", "TGHU7654321"])
        kept = detail.loc[detail["标准柜号"] == "MSCU1234567"].iloc[0]
        self.assertEqual(kept["提柜时间"], pd.Timestamp("2026-07-08"))
        self.assertEqual(detail["统计时间指标"].unique().tolist(), ["提柜时间"])
        self.assertEqual(detail["统计周期"].unique().tolist(), ["2026-07-06 ~ 2026-07-12"])

        result = summary.iloc[0]
        self.assertEqual(result["总柜量"], 2)
        self.assertAlmostEqual(result["总平均提柜时效"], 1.25)
        self.assertAlmostEqual(result["总P80提柜时效"], 1.7)
        self.assertAlmostEqual(result["总平均拆柜时效"], 1.5)
        self.assertAlmostEqual(result["总P80拆柜时效"], 1.8)

    def test_each_supported_time_dimension_drives_month_attribution(self):
        source = pd.DataFrame([
            container_row(
                "MSCU1234567",
                eta="2026-01-15",
                available="2026-02-15",
                pickup="2026-03-15",
                arrival="2026-04-15",
                unload="2026-05-15",
            )
        ])
        expected_periods = {
            "ETA": "2026-01",
            "Available时间": "2026-02",
            "提柜时间": "2026-03",
            "实际抵仓时间": "2026-04",
            "拆柜完成时间": "2026-05",
        }

        for time_dimension, expected_period in expected_periods.items():
            with self.subTest(time_dimension=time_dimension):
                detail, summary = processors.process_container_analysis(
                    source,
                    warehouse="LA",
                    period_type="按月统计",
                    time_dimension=time_dimension,
                )
                self.assertEqual(detail.iloc[0]["统计时间指标"], time_dimension)
                self.assertEqual(detail.iloc[0]["统计周期"], expected_period)
                self.assertEqual(summary.iloc[0]["统计周期"], expected_period)

    def test_original_file_range_uses_selected_time_dimension(self):
        source = pd.DataFrame([
            container_row("MSCU1234567", eta="2026-01-03"),
            container_row("TGHU7654321", eta="2026-01-20", customer="ACME", channel="T2"),
        ])

        detail, summary = processors.process_container_analysis(
            source,
            warehouse="LA",
            period_type="按原文件时间范围",
            time_dimension="ETA",
        )

        expected = "2026-01-03 ~ 2026-01-20"
        self.assertEqual(detail["统计周期"].unique().tolist(), [expected])
        self.assertEqual(summary.iloc[0]["统计周期"], expected)

    def test_dal_pickup_uses_pickup_time_instead_of_available_time(self):
        source = pd.DataFrame([
            container_row(
                "MSCU1234567",
                warehouse="DAL",
                available="2026-01-01",
                pickup="2026-01-10",
                arrival="2026-01-11",
                unload="2026-01-12",
            )
        ])

        detail, summary = processors.process_container_analysis(
            source,
            warehouse="DAL",
            period_type="按月统计",
            time_dimension="提柜时间",
        )

        self.assertEqual(detail.iloc[0]["提柜时效开始时间"], pd.Timestamp("2026-01-10"))
        self.assertEqual(summary.iloc[0]["总平均提柜时效"], 1)

    def test_pickup_and_unload_validity_are_independent(self):
        source = pd.DataFrame([
            container_row(
                "MSCU1234567",
                available="2026-01-02",
                pickup="2026-01-02",
                arrival="2026-01-03",
                unload="2026-02-15",
            )
        ])

        detail, summary = processors.process_container_analysis(
            source,
            warehouse="LA",
            period_type="按月统计",
            time_dimension="提柜时间",
        )

        row = detail.iloc[0]
        self.assertTrue(bool(row["提柜时效是否有效"]))
        self.assertFalse(bool(row["拆柜时效是否有效"]))
        self.assertEqual(summary.iloc[0]["总柜量"], 1)
        self.assertEqual(summary.iloc[0]["总平均提柜时效"], 1)
        self.assertTrue(pd.isna(summary.iloc[0]["总平均拆柜时效"]))

    def test_no_split_delivery_rows_reports_clear_error(self):
        source = pd.DataFrame([
            container_row("MSCU1234567", delivery_method="直送")
        ])

        with self.assertRaisesRegex(ValueError, "拆送.*拆柜"):
            processors.process_container_analysis(
                source,
                warehouse="LA",
                period_type="按月统计",
                time_dimension="ETA",
            )


if __name__ == "__main__":
    unittest.main()
