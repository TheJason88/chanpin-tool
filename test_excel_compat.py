import io
import unittest
import zipfile
from xml.etree.ElementTree import ParseError

import pandas as pd

import excel_compat


def _xlsx_bytes(values):
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        pd.DataFrame({"备注": values}).to_excel(writer, index=False, sheet_name="Sheet1")
    return buffer.getvalue()


def _inject_forbidden_character(raw_xlsx):
    source = io.BytesIO(raw_xlsx)
    output = io.BytesIO()
    with zipfile.ZipFile(source, "r") as src_zip:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as dst_zip:
            for item in src_zip.infolist():
                data = src_zip.read(item.filename)
                if item.filename == "xl/sharedStrings.xml":
                    data = data.replace(b"usable text", b"usable\xef\xbf\xbetext", 1)
                dst_zip.writestr(item, data)
    output.seek(0)
    return output


class ExcelCompatibilityTests(unittest.TestCase):
    def test_normal_workbook_is_unchanged(self):
        source = io.BytesIO(_xlsx_bytes(["usable text"]))

        frame = excel_compat.read_excel(source, sheet_name="Sheet1")

        self.assertEqual(frame.loc[0, "备注"], "usable text")
        self.assertEqual(frame.attrs["excel_xml_repair_count"], 0)

    def test_forbidden_xml_character_is_removed_and_workbook_is_readable(self):
        source = _inject_forbidden_character(_xlsx_bytes(["usable text"]))
        with self.assertRaises(ParseError):
            pd.ExcelFile(source)

        frame = excel_compat.read_excel(source, sheet_name="Sheet1")

        self.assertEqual(frame.loc[0, "备注"], "usabletext")
        self.assertEqual(frame.attrs["excel_xml_repair_count"], 1)


if __name__ == "__main__":
    unittest.main()
