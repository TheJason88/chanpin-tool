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
RECALC_COLS = ["出库体积", "出库卡板数", "派送成本", "FBA出库体积", "FBX出库体积"]
ZIP_FILL_COL = "补充标准邮编"
STATE_FILL_COL = "补充目的州"

# 仓间调拨目的地强制覆盖规则。
# 只要出库类型为调拨，或调入仓库不为空，就以“调入仓库”为实际目的地，不能再按原目的地/FBA/平台仓识别。
TRANSFER_WAREHOUSE_INFO = {
    "LA": {
        "display": "LA盈仓",
        "zip": "91708",
        "zip3": "917",
        "state": "CA",
        "line": "LA",
        "keywords": ["LA", "美西", "洛杉矶", "CHINO", "SAN ANTONIO"],
    },
    "NJ": {
        "display": "新泽西盈仓",
        "zip": "08857",
        "zip3": "088",
        "state": "NJ",
        "line": "LA-NJ",
        "keywords": ["NJ", "新泽西", "NEW JERSEY", "OLD BRIDGE", "JAKE BROWN"],
    },
    "SAV": {
        "display": "萨凡纳盈仓",
        "zip": "31408",
        "zip3": "314",
        "state": "GA",
        "line": "LA-SAV",
        "keywords": ["SAV", "萨凡纳", "SAVANNAH", "GARDEN CITY", "PROSPERITY"],
    },
    "DAL": {
        "display": "达拉斯盈仓",
        "zip": "75180",
        "zip3": "751",
        "state": "TX",
        "line": "LA-DAL",
        "keywords": ["DAL", "达拉斯", "DALLAS", "BALCH SPRINGS", "PEACHTREE"],
    },
}

TRANSFER_TEXT_COLS = ["出库类型", "业务场景", "调入仓库", "目的地", "修正后目的地", "备注", "车次号", "批次号集合"]
DESTINATION_OVERRIDE_COLS = [
    "实际目的地", "修正后目的地", "目的地", "调入仓库", "标准邮编集合", "邮编前三位集合", "目的州",
    "邮编来源", "系统产品类型", "主产品类型", "平台名称", "平台仓代码集合", "FBA仓点代码集合",
    "FBA出库体积", "FBX出库体积", "目的地邮编待补充", "专线线路", "专线识别方式",
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
    out.columns = [_clean_header(c) for c in out.columns]
    target_exists = target_col in out.columns
    target_sum = _usable_sum(out[target_col]) if target_exists else 0
    best_col, best_sum = _best_candidate(out, candidates)
    if best_col is None:
        if not target_exists:
            out[target_col] = 0.0
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


def _split_values(value):
    if processors.is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ["nan", "none", "null", "false", "0"]]


def _ensure_numeric(df, cols):
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(float)
    return out


def _prepare_recalc_columns(df):
    """确保回填列都是float，避免向int64列写入 2.3544 这类小数时报错。"""
    out = df.copy()
    for col in RECALC_COLS:
        if col not in out.columns:
            out[col] = 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(float)
    return out


def _as_mutable_object_df(df):
    """把Excel读入的严格string/int列改成object，避免二次补邮编时写入False/小数报dtype错误。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    for col in out.columns:
        out[col] = out[col].astype(object)
    return out


def _normalize_state(value):
    if processors.is_blank(value):
        return ""
    text = str(value).strip()
    upper = text.upper()
    aliases = {
        "CALIFORNIA": "CA", "加州": "CA", "NEW JERSEY": "NJ", "GEORGIA": "GA",
        "TEXAS": "TX", "FLORIDA": "FL", "NORTH CAROLINA": "NC", "ILLINOIS": "IL",
    }
    if upper in aliases:
        return aliases[upper]
    match = re.search(r"\b([A-Z]{2})\b", upper)
    return match.group(1) if match else text


def _text_value(value):
    if processors.is_blank(value):
        return ""
    return str(value).strip()


def _infer_transfer_target_from_text(text):
    upper = str(text).upper()
    for target, info in TRANSFER_WAREHOUSE_INFO.items():
        for keyword in info["keywords"]:
            if str(keyword).upper() in upper:
                return target, info
    return "", None


def _row_is_transfer(row):
    outbound_type = _text_value(row.get("出库类型", ""))
    business_scene = _text_value(row.get("业务场景", ""))
    transfer_to = _text_value(row.get("调入仓库", ""))
    combined = " ".join([outbound_type, business_scene, transfer_to])
    return bool(transfer_to) or ("调拨" in combined) or ("仓间" in combined) or ("调入" in combined)


def _infer_transfer_target_from_row(row):
    # 调入仓库优先级最高；其次再看出库类型、业务场景、目的地等文本。
    for col in ["调入仓库", "出库类型", "业务场景", "实际目的地", "修正后目的地", "目的地", "备注", "车次号", "批次号集合"]:
        if col in row.index:
            target, info = _infer_transfer_target_from_text(row.get(col, ""))
            if info:
                return target, info
    combined = " ".join(_text_value(row.get(c, "")) for c in TRANSFER_TEXT_COLS if c in row.index)
    return _infer_transfer_target_from_text(combined)


def _apply_transfer_destination_override(cleaned_batches):
    """
    调拨/调入仓库不为空的行，实际目的地必须覆盖为调入仓库。
    这一步放在功能一最后，确保邮编异常审核不会再把“调拨到盈仓”的行当成普通目的地异常。
    """
    if cleaned_batches is None or cleaned_batches.empty:
        return cleaned_batches
    out = _as_mutable_object_df(cleaned_batches)
    for col in DESTINATION_OVERRIDE_COLS:
        if col not in out.columns:
            out[col] = "" if col not in ["FBA出库体积", "FBX出库体积"] else 0.0

    for idx, row in out.iterrows():
        if not _row_is_transfer(row):
            continue
        target, info = _infer_transfer_target_from_row(row)
        if not info:
            continue

        display = info["display"]
        out.at[idx, "实际目的地"] = display
        out.at[idx, "修正后目的地"] = display
        out.at[idx, "目的地"] = display
        out.at[idx, "调入仓库"] = display
        out.at[idx, "标准邮编集合"] = info["zip"]
        out.at[idx, "邮编前三位集合"] = info["zip3"]
        out.at[idx, "目的州"] = info["state"]
        out.at[idx, "邮编来源"] = "调拨目标仓地址规则"
        out.at[idx, "系统产品类型"] = "仓间调拨"
        out.at[idx, "主产品类型"] = "仓间调拨"
        out.at[idx, "平台名称"] = "盈仓"
        out.at[idx, "平台仓代码集合"] = display
        out.at[idx, "FBA仓点代码集合"] = ""
        out.at[idx, "FBA出库体积"] = 0.0
        out.at[idx, "FBX出库体积"] = 0.0
        out.at[idx, "目的地邮编待补充"] = False
        out.at[idx, "专线线路"] = info["line"]
        out.at[idx, "专线识别方式"] = "调入仓库优先覆盖"
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

    out = _prepare_recalc_columns(cleaned_batches)

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
        fba_volume = float(out.at[idx, "FBA出库体积"] or 0)
        fbx_volume = float(out.at[idx, "FBX出库体积"] or 0)
        if fba_volume > 0 and fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "混合目的地"
        elif fba_volume > 0:
            out.at[idx, "系统产品类型"] = "FBA"
        elif fbx_volume > 0:
            out.at[idx, "系统产品类型"] = "FBX"
        out.at[idx, "主产品类型"] = "FBA" if fba_volume >= fbx_volume and fba_volume > 0 else ("FBX" if fbx_volume > 0 else "未知")

    return out


def _build_stage_key(row):
    if "分析批次ID" in row.index and not processors.is_blank(row.get("分析批次ID")):
        return f"ID::{str(row.get('分析批次ID')).strip()}"
    if "批次号集合" in row.index and not processors.is_blank(row.get("批次号集合")):
        return f"BATCH::{str(row.get('批次号集合')).strip()}"
    return ""


def _pick_manual_zip_from_audit_row(row):
    # 只读人工补充列；不再回退读取“标准邮编集合”，避免未补行被误判为已补。
    for col in [ZIP_FILL_COL, "待填邮编", "补充邮编", "目的地邮编", "邮编", "标准邮编"]:
        if col not in row.index:
            continue
        valid_zips = []
        for value in _split_values(row.get(col, "")):
            zip_code, _, valid, _ = processors.normalize_zip_value(value)
            if valid and zip_code:
                valid_zips.append(zip_code)
        if valid_zips:
            return ",".join(list(dict.fromkeys(valid_zips)))
    return ""


def _apply_zip_audit_updates_safely(main_df, audit_df):
    if main_df is None or main_df.empty or audit_df is None or audit_df.empty:
        return _as_mutable_object_df(main_df)
    df = _as_mutable_object_df(main_df)
    audit = _as_mutable_object_df(audit_df)
    for col in ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "目的地邮编待补充"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(object)

    main_index = {_build_stage_key(row): idx for idx, row in df.iterrows() if _build_stage_key(row)}
    for _, row in audit.iterrows():
        key = _build_stage_key(row)
        if not key or key not in main_index:
            continue
        zip_value = _pick_manual_zip_from_audit_row(row)
        if not zip_value:
            continue
        idx = main_index[key]
        zips = _split_values(zip_value)
        df.at[idx, "标准邮编集合"] = ",".join(zips)
        df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
        state = ""
        for state_col in [STATE_FILL_COL, "目的州", "州", "省/州"]:
            if state_col in row.index and not processors.is_blank(row.get(state_col)):
                state = _normalize_state(row.get(state_col))
                break
        if state:
            df.at[idx, "目的州"] = state
        df.at[idx, "邮编来源"] = "邮编异常审核人工补充"
        df.at[idx, "目的地邮编待补充"] = False
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
    return df


def _read_stage1_or_stage2_with_audit_updates_safely(excel_file):
    excel_file.seek(0)
    xls = pd.ExcelFile(excel_file)
    if "派送二_匹配后合并数据" in xls.sheet_names:
        sheet_name = "派送二_匹配后合并数据"
    elif "清洗后数据" in xls.sheet_names:
        sheet_name = "清洗后数据"
    elif "派送一_清洗合并数据" in xls.sheet_names:
        sheet_name = "派送一_清洗合并数据"
    elif "派送二_批次车次聚合" in xls.sheet_names:
        sheet_name = "派送二_批次车次聚合"
    else:
        sheet_name = xls.sheet_names[0]
    excel_file.seek(0)
    df = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=object)
    df = _as_mutable_object_df(df)
    if "邮编异常审核" in xls.sheet_names:
        excel_file.seek(0)
        audit_df = pd.read_excel(excel_file, sheet_name="邮编异常审核", dtype=object)
        df = _apply_zip_audit_updates_safely(df, audit_df)
    return df


def _wrap_stage2_match_apply(delivery_workflow_module):
    original_apply = delivery_workflow_module.apply_manual_match_to_cleaned_batches

    def patched_apply_manual_match_to_cleaned_batches(cleaned_batches, match_df):
        cleaned_batches = _as_mutable_object_df(cleaned_batches)
        match_df = _as_mutable_object_df(match_df)
        return original_apply(cleaned_batches, match_df)

    delivery_workflow_module.apply_manual_match_to_cleaned_batches = patched_apply_manual_match_to_cleaned_batches


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
            cleaned_batches = _apply_transfer_destination_override(cleaned_batches)
            if cleaned_batches is not None and not cleaned_batches.empty and "目的地邮编待补充" in cleaned_batches.columns:
                zip_audit_df = cleaned_batches[cleaned_batches["目的地邮编待补充"]].copy()
            return cleaned_batches, invalid_detail, zip_audit_df, raw_detail

        return result

    delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = patched_process_stage1_raw_files_to_cleaned_batches
    # 功能二支持“派送二报告 -> 邮编异常审核补邮编 -> 再上传重新分析”。
    # 这里覆盖读取和匹配阶段，避免Excel读入的string/bool dtype冲突。
    delivery_workflow_module.read_stage1_cleaned_batches = _read_stage1_or_stage2_with_audit_updates_safely
    _wrap_stage2_match_apply(delivery_workflow_module)
    return delivery_workflow_module
