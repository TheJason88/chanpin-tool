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


NUMERIC_COLS = ["出库体积", "出库卡板数", "派送成本"]


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
    out.columns = [_clean_header(c) for c in out.columns]
    target_exists = target_col in out.columns
    target_sum = _usable_sum(out[target_col]) if target_exists else 0
    best_col, best_sum = _best_candidate(out, candidates)
    if best_col is None:
        if not target_exists:
            out[target_col] = 0
        return out
    # 如果标准列不存在，或标准列全空/全0而别名列有值，则用有效别名列覆盖标准列。
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


def _split_batch_ids(value):
    if processors.is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ["nan", "none", "null"]]


def _ensure_numeric(df, cols):
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    return out


def _force_cleaned_totals_from_detail(cleaned_batches, detail_df):
    """
    用派送一明细里的批次真实方数/板数/成本，强制回填到FTL车次合并结果。
    目的：避免任何中间字段映射异常导致“清洗后数据”的出库体积/出库卡板数被错误写成0。
    """
    if cleaned_batches is None or cleaned_batches.empty or detail_df is None or detail_df.empty:
        return cleaned_batches

    detail = repair_delivery_stage1_numeric_columns(detail_df)
    detail = _ensure_numeric(detail, NUMERIC_COLS)
    if "批次号" not in detail.columns:
        return cleaned_batches
    if "FBA/FBX" not in detail.columns:
        detail["FBA/FBX"] = ""

    out = cleaned_batches.copy()
    for col in ["出库体积", "出库卡板数", "派送成本", "FBA出库体积", "FBX出库体积"]:
        if col not in out.columns:
            out[col] = 0

    for idx, row in out.iterrows():
        batch_ids = _split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        if not batch_ids:
            continue
        matched = detail[detail["批次号"].astype(str).isin(batch_ids)].copy()
        if matched.empty:
            continue
        out.at[idx, "出库体积"] = float(matched["出库体积"].sum())
        out.at[idx, "出库卡板数"] = float(matched["出库卡板数"].sum())
        out.at[idx, "派送成本"] = float(matched["派送成本"].sum())
        out.at[idx, "FBA出库体积"] = float(matched.loc[matched["FBA/FBX"] == "FBA", "出库体积"].sum())
        out.at[idx, "FBX出库体积"] = float(matched.loc[matched["FBA/FBX"] == "FBX", "出库体积"].sum())

        # 主产品类型同步按方数重新判定。
        fba_volume = out.at[idx, "FBA出库体积"]
        fbx_volume = out.at[idx, "FBX出库体积"]
        if fba_volume > 0 and fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "混合目的地"
        elif fba_volume > 0:
            out.at[idx, "系统产品类型"] = "FBA"
        elif fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "FBX"
        out.at[idx, "主产品类型"] = "FBA" if fba_volume >= fbx_volume and fba_volume > 0 else ("FBX" if fbx_volume > 0 else "未知")

    return out


def patch_delivery_stage1(delivery_workflow_module):
    if not hasattr(delivery_workflow_module, "_original_process_stage1_raw_files_to_cleaned_batches"):
        delivery_workflow_module._original_process_stage1_raw_files_to_cleaned_batches = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches

    def patched_process_stage1_raw_files_to_cleaned_batches(file_dfs, warehouse, period_type="不适用", start_date=None, end_date=None):
        repaired_file_dfs = []
        for source_name, df in file_dfs:
            repaired_file_dfs.append((source_name, repair_delivery_stage1_numeric_columns(df)))

        result = delivery_workflow_module._original_process_stage1_raw_files_to_cleaned_batches(
            file_dfs=repaired_file_dfs,
            warehouse=warehouse,
            period_type=period_type,
            start_date=start_date,
            end_date=end_date,
        )

        # 当前delivery_workflow返回4项：清洗后数据、无效数据、邮编异常数据、原明细参考。
        if isinstance(result, tuple) and len(result) == 4:
            cleaned_batches, invalid_detail, zip_audit_df, raw_detail = result
            cleaned_batches = _force_cleaned_totals_from_detail(cleaned_batches, raw_detail)
            if cleaned_batches is not None and not cleaned_batches.empty and "目的地邮编待补充" in cleaned_batches.columns:
                zip_audit_df = cleaned_batches[cleaned_batches["目的地邮编待补充"]].copy()
            return cleaned_batches, invalid_detail, zip_audit_df, raw_detail

        return result

    delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = patched_process_stage1_raw_files_to_cleaned_batches
    return delivery_workflow_module
