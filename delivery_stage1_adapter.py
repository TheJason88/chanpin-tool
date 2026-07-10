import re

import pandas as pd

import processors


VOLUME_CANDIDATES = [
    "出库体积", "出库方数", "方数", "体积", "CBM", "cbm", "Volume", "volume", "立方数", "出库CBM"
]
PALLET_CANDIDATES = [
    "出库卡板数", "出库板数", "卡板数", "板数", "托盘数", "Pallets", "pallets", "Pallet", "pallet"
]
COST_CANDIDATES = [
    "派送成本", "成本", "派送费用", "DeliveryCost", "Delivery Cost", "Cost", "cost"
]


def _clean_header(value):
    return processors.clean_col_name(value)


def _normalize_numeric_series(series):
    # 兼容 $1,200、1,200 CBM、空值等。
    text = series.astype(str).str.replace(",", "", regex=False).str.replace("$", "", regex=False)
    text = text.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(text, errors="coerce")


def _usable_sum(series):
    values = _normalize_numeric_series(series)
    return float(values.fillna(0).sum())


def _best_candidate(df, candidates):
    existing = []
    clean_to_originals = {}
    for col in df.columns:
        clean_to_originals.setdefault(_clean_header(col), []).append(col)
    for candidate in candidates:
        clean = _clean_header(candidate)
        for col in clean_to_originals.get(clean, []):
            if col not in existing:
                existing.append(col)
    if not existing:
        return None, 0
    scored = [(col, _usable_sum(df[col])) for col in existing]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0]


def _repair_one_numeric_column(df, target_col, candidates):
    out = df.copy()
    # 先统一列名空格/换行，避免“出库体积 ”这类字段无法识别。
    out.columns = [_clean_header(c) for c in out.columns]
    target_exists = target_col in out.columns
    target_sum = _usable_sum(out[target_col]) if target_exists else 0
    best_col, best_sum = _best_candidate(out, candidates)
    if best_col is None:
        if not target_exists:
            out[target_col] = 0
        return out
    # 关键修复：如果标准列存在但全空/全0，而方数/板数等别名列有值，则用别名列覆盖标准列。
    if (not target_exists) or target_sum <= 0 < best_sum:
        out[target_col] = _normalize_numeric_series(out[best_col])
    else:
        out[target_col] = _normalize_numeric_series(out[target_col])
    return out


def repair_delivery_stage1_numeric_columns(df):
    out = df.copy()
    out = _repair_one_numeric_column(out, "出库体积", VOLUME_CANDIDATES)
    out = _repair_one_numeric_column(out, "出库卡板数", PALLET_CANDIDATES)
    out = _repair_one_numeric_column(out, "派送成本", COST_CANDIDATES)
    return out


def patch_delivery_stage1(delivery_workflow_module):
    if not hasattr(delivery_workflow_module, "_original_process_stage1_raw_files_to_cleaned_batches"):
        delivery_workflow_module._original_process_stage1_raw_files_to_cleaned_batches = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches

    def patched_process_stage1_raw_files_to_cleaned_batches(file_dfs, warehouse, period_type="不适用", start_date=None, end_date=None):
        repaired_file_dfs = []
        for source_name, df in file_dfs:
            repaired_file_dfs.append((source_name, repair_delivery_stage1_numeric_columns(df)))
        return delivery_workflow_module._original_process_stage1_raw_files_to_cleaned_batches(
            file_dfs=repaired_file_dfs,
            warehouse=warehouse,
            period_type=period_type,
            start_date=start_date,
            end_date=end_date,
        )

    delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = patched_process_stage1_raw_files_to_cleaned_batches
    return delivery_workflow_module
