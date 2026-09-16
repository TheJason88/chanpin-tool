import unittest
from unittest.mock import patch

import pandas as pd

import delivery_reference
import delivery_runtime
import delivery_workflow


class FbaSiteAliasTests(unittest.TestCase):
    def test_all_aliases_share_location_but_keep_both_identities(self):
        aliases = pd.read_csv(delivery_reference.FBA_ALIASES_PATH, dtype=str)
        self.assertEqual(len(aliases), 11)
        df, mapping = delivery_reference.load_fba_reference()
        self.assertTrue(df['FBA仓点代码'].is_unique)
        for new, old in aliases.itertuples(index=False, name=None):
            with self.subTest(new=new, old=old):
                for field in ['地址', '邮编', '邮编前三位', '州']:
                    self.assertEqual(mapping[new][field], mapping[old][field])
                    self.assertTrue(mapping[new][field])
                self.assertEqual(mapping[new]['FBA仓点代码'], new)
                self.assertEqual(mapping[old]['FBA仓点代码'], old)
                self.assertEqual(delivery_reference.match_fba_reference('Amazon-' + new)['代码'], new)
                self.assertEqual(delivery_reference.match_fba_reference('Amazon-' + old)['代码'], old)
        self.assertNotIn('TBD', mapping)

    def test_iutj_and_screenshot_codes_resolve_to_correct_locations(self):
        for code, state, zipcode in [('IUTJ', 'CA', '92335'), ('ITX3', 'TX', '79108'), ('IMO1', 'MO', '64068')]:
            ref = delivery_reference.match_fba_reference('Amazon-' + code)
            self.assertEqual((ref['州'], ref['邮编']), (state, zipcode))
        self.assertEqual(delivery_reference.match_fba_reference('Amazon-IUTJ')['地址'],
                         '9253 Dreamland Drive, Fontana, CA 92335, USA')

    def test_alias_does_not_overwrite_explicit_new_site_or_invent_missing_old(self):
        original = pd.DataFrame([
            {'FBA仓点代码': 'LAN2', '邮编': '48917', '州': 'MI', '地址': 'old'},
            {'FBA仓点代码': 'IMI1', '邮编': '48918', '州': 'MI', '地址': 'independent'},
        ])
        aliases = pd.DataFrame([{'新仓点代码': 'IMI1', '原仓点代码': 'LAN2'},
                                {'新仓点代码': 'INC1', '原仓点代码': 'RDU4'},
                                {'新仓点代码': 'tbd', '原仓点代码': 'LAN2'}])
        snapshot = original.copy(deep=True)
        with patch.object(delivery_reference, '_read_reference_csv',
                          side_effect=lambda p: (original if p == delivery_reference.FBA_ZIP_PATH else aliases).copy()):
            _, mapping = delivery_reference.load_fba_reference()
        self.assertEqual(mapping['IMI1']['地址'], 'independent')
        self.assertEqual(mapping['LAN2']['地址'], 'old')
        self.assertNotIn('INC1', mapping)
        self.assertNotIn('TBD', mapping)
        pd.testing.assert_frame_equal(original, snapshot)

    def test_stage_one_and_two_keep_old_and_new_sites_separate(self):
        delivery_runtime.bootstrap(delivery_workflow)
        raw = pd.DataFrame([{
            '仓库': 'LA', '批次号': f'B{i}', '车次号': f'T{i}',
            '目的地': 'Amazon-' + code, '出库类型': '派送', '派送方式': '卡车派送',
            '运输类型': 1, '车型': '53尺', '装车类型': '地板',
            '出库体积': 80, '出库卡板数': 20, '派送成本': 1000,
            '派送卡车': 'Carrier', '出库时间': '2026-09-10 12:00:00',
            '创建时间': '2026-09-09 12:00:00', '签收时间': '2026-09-12 12:00:00', '备注': '',
        } for i, code in enumerate(['MCI3', 'IMO1', 'AMA1', 'ITX3', 'IUTJ', 'LAN2', 'IMI1'])])
        clean, invalid, _, _ = delivery_workflow.process_stage1_raw_files_to_cleaned_batches([('input.xlsx', raw)], 'LA')
        self.assertTrue(invalid.empty)
        matched = delivery_workflow.prepare_stage2_for_report(clean, pd.DataFrame(), '按月统计')
        expected = {'MCI3': '64068', 'IMO1': '64068', 'AMA1': '79108', 'ITX3': '79108',
                    'IUTJ': '92335', 'LAN2': '48917', 'IMI1': '48917'}
        self.assertEqual(len(matched), len(expected))
        for _, row in matched.iterrows():
            self.assertEqual(row['标准邮编集合'], expected[row['批次目的仓点']])
            self.assertEqual(row['主产品类型'], 'FBA')


if __name__ == '__main__':
    unittest.main()
