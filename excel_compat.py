import io
import os
import re
import zipfile
from pathlib import Path
from xml.etree.ElementTree import ParseError

import pandas as pd


# XML 1.0 forbids these control characters and the U+FFFE/U+FFFF noncharacters.
# Some WMS exports still write them into sharedStrings.xml, which makes otherwise
# valid .xlsx files unreadable by pandas/openpyxl.
_FORBIDDEN_XML_BYTES = re.compile(
    rb"[\x00-\x08\x0B\x0C\x0E-\x1F]|\xEF\xBF[\xBE\xBF]"
)


def _rewind(source):
    if hasattr(source, "seek"):
        source.seek(0)


def _source_bytes(source):
    if isinstance(source, (str, os.PathLike, Path)):
        return Path(source).read_bytes()
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if not hasattr(source, "read"):
        raise TypeError("Excel 输入必须是文件路径、字节内容或可读取的文件对象。")
    _rewind(source)
    data = source.read()
    _rewind(source)
    return data


def _repair_xlsx_xml(source):
    raw = _source_bytes(source)
    raw_buffer = io.BytesIO(raw)
    if not zipfile.is_zipfile(raw_buffer):
        return None, 0

    raw_buffer.seek(0)
    repaired_buffer = io.BytesIO()
    repaired_count = 0
    with zipfile.ZipFile(raw_buffer, "r") as src_zip:
        with zipfile.ZipFile(repaired_buffer, "w", zipfile.ZIP_DEFLATED) as dst_zip:
            for item in src_zip.infolist():
                data = src_zip.read(item.filename)
                if item.filename.endswith((".xml", ".rels")):
                    data, count = _FORBIDDEN_XML_BYTES.subn(b"", data)
                    repaired_count += count
                dst_zip.writestr(item, data)

    if repaired_count == 0:
        return None, 0
    repaired_buffer.seek(0)
    return repaired_buffer, repaired_count


def open_excel(source):
    """Open an Excel file, repairing forbidden OOXML characters only when needed."""
    _rewind(source)
    try:
        excel_file = pd.ExcelFile(source)
        excel_file.xml_repair_count = 0
        return excel_file
    except ParseError as original_error:
        repaired_source, repaired_count = _repair_xlsx_xml(source)
        if repaired_source is None:
            raise original_error
        excel_file = pd.ExcelFile(repaired_source)
        excel_file.xml_repair_count = repaired_count
        return excel_file


def read_excel(source, *args, **kwargs):
    """Read one sheet with the same guarded OOXML repair used by open_excel."""
    excel_file = open_excel(source)
    frame = pd.read_excel(excel_file, *args, **kwargs)
    frame.attrs["excel_xml_repair_count"] = getattr(excel_file, "xml_repair_count", 0)
    return frame
