import re

import pandas as pd

import processors


ZIP_UPDATE_COLUMNS = [
    "补充标准邮编", "待填邮编", "补充邮编", "目的地邮编", "邮编", "标准邮编", "标准邮编集合",
]
STATE_UPDATE_COLUMNS = ["补充目的州", "目的州", "州", "省/州", "省州", "State", "STATE"]
BATCH_KEY_COLUMNS = ["批次号集合", "批次号"]
MAIN_SHEET_CANDIDATES = [
    "派送二_匹配后合并数据",
    "清洗后数据",
    "派送一_清洗合并数据",
    "派送二_批次车次聚合",
]
AUDIT_SHEET_NAME = "邮编异常审核"


def _is_blank(value):
    try:
        if value is None or pd.isna(value):
            return True
    except Exception:
        if value is None:
            return True
    text = str(value).strip()
    return text.lower() in ["", "nan", "none", "null", "<na>", "false"] or text in ["/", "//", ";", ";;", "-", "0"]


def _normalize_batch_key(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def _split_batch_keys(value):
    if _is_blank(value):
        return []
    text = str(value).replace("\n", ",")
    parts = re.split(r"[,，;；/\s]+", text)
    result = []
    for part in parts:
        key = _normalize_batch_key(part)
        if key and key not in result:
            result.append(key)
    return result


def _batch_tokens_from_row(row):
    tokens = []
    for col in BATCH_KEY_COLUMNS:
        if col in row.index:
            tokens.extend(_split_batch_keys(row.get(col)))
    return set(tokens)


def _normalize_zip(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    # Prefer the shared normalizer when available, then fall back to local parsing.
    try:
        zip_code, _, valid, _ = processors.normalize_zip_value(text)
        if valid and zip_code:
            return str(zip_code).zfill(5)
    except Exception:
        pass
    match5 = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if match5:
        return match5.group(1)
    match4 = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    if match4:
        return "0" + match4.group(1)
    return ""


def _zip_values_from_cell(value):
    if _is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    result = []
    for part in parts:
        zip_code = _normalize_zip(part)
        if zip_code and zip_code not in result:
            result.append(zip_code)
    return result


def _zip_values_from_row(row):
    result = []
    for col in ZIP_UPDATE_COLUMNS:
        if col not in row.index:
            continue
        for zip_code in _zip_values_from_cell(row.get(col)):
            if zip_code not in result:
                result.append(zip_code)
    return result


def _state_from_row(row):
    for col in STATE_UPDATE_COLUMNS:
        if col in row.index and not _is_blank(row.get(col)):
            text = str(row.get(col)).strip().upper()
            match = re.search(r"\b([A-Z]{2})\b", text)
            return match.group(1) if match else text
    return ""


def _build_batch_token_index(df):
    token_index = {}
    for idx, row in df.iterrows():
        for token in _batch_tokens_from_row(row):
            token_index.setdefault(token, set()).add(idx)
    return token_index


def _build_analysis_id_index(df):
    id_index = {}
    if "分析批次ID" not in df.columns:
        return id_index
    for idx, value in df["分析批次ID"].items():
        if not _is_blank(value):
            id_index.setdefault(str(value).strip(), set()).add(idx)
    return id_index


def apply_zip_audit_updates(main_df, audit_df):
    """
    Consume the filled 邮编异常审核 sheet and write the补充邮编 back to the main stage-2 data.
    Matching priority is 批次号集合/批次号, then 分析批次ID as fallback.
    """
    if main_df is None or getattr(main_df, "empty", True) or audit_df is None or getattr(audit_df, "empty", True):
        return main_df

    df = main_df.copy()
    audit = audit_df.copy()
    for col in ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "目的地邮编待补充"]:
        if col not in df.columns:
            df[col] = ""

    token_index = _build_batch_token_index(df)
    id_index = _build_analysis_id_index(df)

    for _, audit_row in audit.iterrows():
        zips = _zip_values_from_row(audit_row)
        if not zips:
            continue

        target_indexes = set()
        audit_tokens = _batch_tokens_from_row(audit_row)
        if audit_tokens:
            for token in audit_tokens:
                target_indexes.update(token_index.get(token, set()))

        if not target_indexes and "分析批次ID" in audit_row.index and not _is_blank(audit_row.get("分析批次ID")):
            target_indexes.update(id_index.get(str(audit_row.get("分析批次ID")).strip(), set()))

        state = _state_from_row(audit_row)
        for idx in target_indexes:
            df.at[idx, "标准邮编集合"] = ",".join(zips)
            df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            if state:
                df.at[idx, "目的州"] = state
            df.at[idx, "邮编来源"] = "邮编异常审核人工补充"
            df.at[idx, "目的地邮编待补充"] = False

    # Recalculate from the actual zip column; do not trust previous True/False string values.
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda value: len(_zip_values_from_cell(value)) == 0)
    return df


def read_stage1_or_stage2_with_audit_updates(excel_file):
    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    sheet_name = xls.sheet_names[0]
    for candidate in MAIN_SHEET_CANDIDATES:
        if candidate in xls.sheet_names:
            sheet_name = candidate
            break

    excel_file.seek(0)
    df = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)

    if AUDIT_SHEET_NAME in xls.sheet_names:
        excel_file.seek(0)
        audit_df = pd.read_excel(excel_file, sheet_name=AUDIT_SHEET_NAME, dtype=str)
        df = apply_zip_audit_updates(df, audit_df)
    return df


def apply_to_workflow(delivery_workflow_module):
    # app.py calls delivery_workflow.read_stage1_cleaned_batches for 5A files.
    delivery_workflow_module.read_stage1_cleaned_batches = read_stage1_or_stage2_with_audit_updates
    return delivery_workflow_module
