import re

import pandas as pd

import processors
import tool_common
import delivery_reference


STATE_ALIASES = {
    "CALIFORNIA": "CA", "加州": "CA", "FLORIDA": "FL", "FL": "FL",
    "SEATTLE, WA": "WA", "WASHINGTON": "WA", "TEXAS": "TX",
    "NEW JERSEY": "NJ", "PENNSYLVANIA": "PA", "OHIO": "OH",
    "GEORGIA": "GA", "NORTH CAROLINA": "NC", "ILLINOIS": "IL",
}

REMARK_COLS = ["MEMO", "跟进记录", "内部备注", "备注", "派送区域", "供应商名称", "派送车次号", "车次"]
DROP_REMARK_COLS = ["备注", "备注信息", "匹配备注集合", "专线识别方式", "派送区域识别方式", "备注列"]
INVALID_LABEL_KEYWORDS = ["盈仓", "未知", "非平台", "其他"]

ZIP_FILL_COL = "补充标准邮编"
STATE_FILL_COL = "补充目的州"
AUDIT_FILL_COLS = [ZIP_FILL_COL, STATE_FILL_COL]
INTEGER_COLUMNS = ["排名", "车次数", "发车数", "派送数", "出库卡板数"]
DECIMAL_COLUMNS = [
    "数值", "占比", "出库体积", "FBA出库体积", "FBX出库体积", "派送成本", "派送时效",
    "总出库体积", "总派送成本", "平均整车价", "每方平均价", "平均每车出库体积",
    "P80每车出库体积", "平均每车出库卡板数", "P80每车出库卡板数", "P80整车价",
    "平均派送时效", "P80派送时效",
]

# 仓间调拨目标仓地址：用于功能二补邮编、识别干线，避免调拨行长期留在邮编异常审核。
TRANSFER_WAREHOUSE_INFO = {
    "LA": {"zip": "91708", "state": "CA", "line": "LA", "keywords": ["LA", "美西", "洛杉矶", "CHINO"]},
    "NJ": {"zip": "08857", "state": "NJ", "line": "LA-NJ", "keywords": ["NJ", "新泽西", "NEW JERSEY", "OLD BRIDGE", "JAKE BROWN"]},
    "SAV": {"zip": "31408", "state": "GA", "line": "LA-SAV", "keywords": ["SAV", "萨凡纳", "SAVANNAH", "GARDEN CITY", "PROSPERITY"]},
    "DAL": {"zip": "75180", "state": "TX", "line": "LA-DAL", "keywords": ["DAL", "达拉斯", "DALLAS", "BALCH SPRINGS", "PEACHTREE"]},
}


def _is_blank(value):
    return processors.is_blank(value)


def _split_values(value):
    if _is_blank(value):
        return []
    parts = re.split(r"[,，;；/\s]+", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in ["nan", "none", "null", "false", "0"]]


def _find_col(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _combine_unique(values, sep=", "):
    out = []
    for value in values:
        if _is_blank(value):
            continue
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return sep.join(out)


def _normalize_state(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    upper = text.upper()
    if upper in STATE_ALIASES:
        return STATE_ALIASES[upper]
    match = re.search(r"\b([A-Z]{2})\b", upper)
    if match:
        return match.group(1)
    return text


def _normalize_zip(value):
    zip_code, fix, valid, reason = processors.normalize_zip_value(value)
    return zip_code, fix, bool(valid), reason


def _valid_platform_label(value):
    if _is_blank(value):
        return False
    text = str(value).strip()
    return not any(k in text for k in INVALID_LABEL_KEYWORDS)


def _sync_fbx_code_columns(df):
    """兼容历史“平台仓代码集合”，同时保证新列“FBX代码集合”始终可用。"""
    if df is None or df.empty:
        return df
    out = df.copy()
    if "FBX代码集合" not in out.columns:
        out["FBX代码集合"] = out.get("平台仓代码集合", "")
    if "平台仓代码集合" not in out.columns:
        out["平台仓代码集合"] = out.get("FBX代码集合", "")
    new_blank = out["FBX代码集合"].apply(_is_blank)
    old_blank = out["平台仓代码集合"].apply(_is_blank)
    out.loc[new_blank & ~old_blank, "FBX代码集合"] = out.loc[new_blank & ~old_blank, "平台仓代码集合"]
    out.loc[old_blank & ~new_blank, "平台仓代码集合"] = out.loc[old_blank & ~new_blank, "FBX代码集合"]
    return out


def _infer_transfer_target(text):
    upper = str(text).upper()
    for target, info in TRANSFER_WAREHOUSE_INFO.items():
        for keyword in info["keywords"]:
            if str(keyword).upper() in upper:
                return target, info
    return "", None


def _infer_linehaul_from_text(text):
    upper = str(text).upper()
    target, info = _infer_transfer_target(upper)
    if target in ["NJ", "SAV", "DAL"]:
        return info["line"], f"车次/批次/调入仓库命中{target}"
    if re.search(r"\bSAV\b|转\s*SAV|SAVANNAH|萨凡纳", upper):
        return "LA-SAV", "车次/批次备注命中SAV"
    if re.search(r"\bDAL\b|转\s*DAL|DALLAS|达拉斯", upper):
        return "LA-DAL", "车次/批次备注命中DAL"
    if re.search(r"\bNJ\b|转\s*NJ|NEW\s*JERSEY|新泽西", upper):
        return "LA-NJ", "车次/批次备注命中NJ"
    return "", ""


def _strip_remark_columns(df):
    if df is None or df.empty:
        return df
    cols_to_drop = []
    for c in df.columns:
        text = str(c)
        if text in DROP_REMARK_COLS or "备注" in text or "识别方式" in text:
            cols_to_drop.append(c)
    return df.drop(columns=cols_to_drop, errors="ignore")


def _drop_empty_columns(df, preserve=None):
    if df is None or df.empty:
        return df
    preserve = set(preserve or [])
    out = df.copy()
    keep_cols = []
    for col in out.columns:
        if col in preserve:
            keep_cols.append(col)
            continue
        s = out[col]
        non_empty = s.apply(lambda x: not (_is_blank(x) or (pd.isna(x) if not isinstance(x, (list, dict, tuple, set)) else False)))
        if non_empty.any():
            keep_cols.append(col)
    return out[keep_cols]


def _format_numbers(df, sheet_type=""):
    if df is None or df.empty:
        return df
    out = df.copy()

    for col in INTEGER_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(0).astype("Int64")

    # 发车量表里的“数值”本质是车次数，不保留小数。
    if sheet_type == "发车量" and "数值" in out.columns:
        out["数值"] = pd.to_numeric(out["数值"], errors="coerce").round(0).astype("Int64")

    for col in DECIMAL_COLUMNS:
        if col in out.columns:
            if sheet_type == "发车量" and col == "数值":
                continue
            out[col] = pd.to_numeric(out[col], errors="coerce").round(2)

    # 如果车次数因为历史分摊逻辑出现小数，统一改成整数计数口径。
    if "车次数" in out.columns:
        out["车次数"] = pd.to_numeric(out["车次数"], errors="coerce").round(0).astype("Int64")
    return out


def _finalize_sheet(df, sheet_type=""):
    if sheet_type == "明细" and df is not None:
        out = df.copy()
        remarks = out.get("同车次备注集合", pd.Series("", index=out.index)).copy()
        out = _strip_remark_columns(out)
        out["同车次备注集合"] = remarks
        out = _drop_empty_columns(out, preserve=["同车次备注集合"])
        out = out[[col for col in out.columns if col != "同车次备注集合"] + ["同车次备注集合"]]
        return _format_numbers(out, sheet_type=sheet_type)
    return _format_numbers(_drop_empty_columns(_strip_remark_columns(df)), sheet_type=sheet_type)


def _finalize_zip_audit_sheet(df):
    if df is None or df.empty:
        cols = ["分析批次ID", "批次号集合", ZIP_FILL_COL, STATE_FILL_COL, "标准邮编集合", "目的州"]
        return pd.DataFrame(columns=cols)
    out = _strip_remark_columns(df.copy())
    for col in AUDIT_FILL_COLS:
        if col not in out.columns:
            out.insert(min(2, len(out.columns)), col, "")
    preferred = [
        "分析批次ID", "仓库", "标准运输类型", "派送方式", "车次号", "批次号集合",
        ZIP_FILL_COL, STATE_FILL_COL,
        "出库类型", "业务场景", "调入仓库", "出库体积", "出库卡板数", "派送成本",
        "系统产品类型", "主产品类型", "平台名称", "FBX代码集合", "平台仓代码集合", "FBA仓点代码集合",
        "标准邮编集合", "目的州", "邮编来源", "目的地邮编待补充",
    ]
    cols = [c for c in preferred if c in out.columns] + [c for c in out.columns if c not in preferred]
    out = out[cols]
    return _format_numbers(out, sheet_type="邮编异常审核")


def _fill_fba_zip_memory(df):
    out = df.copy()
    for col in ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源"]:
        if col not in out.columns:
            out[col] = ""
    for idx, row in out.iterrows():
        if _split_values(row.get("标准邮编集合", "")):
            continue
        codes = _split_values(row.get("FBA仓点代码集合", ""))
        if not codes:
            continue
        zips, states = [], []
        for code in codes:
            ref = delivery_reference.FBA_REFERENCE_MAP.get(str(code).upper().strip())
            if not ref:
                continue
            raw_zip = str(ref.get("邮编", "")).strip()
            z = raw_zip.zfill(5) if raw_zip else ""
            state = str(ref.get("州", "")).upper().strip()
            if z and z not in zips:
                zips.append(z)
            if state and state not in states:
                states.append(state)
        if zips:
            out.at[idx, "标准邮编集合"] = ",".join(zips)
            out.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            out.at[idx, "邮编来源"] = "内置FBA仓点邮编表"
            out.at[idx, "目的地邮编待补充"] = False
        if states:
            out.at[idx, "目的州"] = ",".join(states)
    return out


def _fill_transfer_zip_memory(df):
    out = df.copy()
    for col in ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "调入仓库"]:
        if col not in out.columns:
            out[col] = ""
    for idx, row in out.iterrows():
        text = " ".join(str(row.get(c, "")) for c in ["出库类型", "业务场景", "调入仓库"] if c in out.columns)
        if not ("调拨" in text or "仓间" in text or "调入" in text):
            continue
        target, info = _infer_transfer_target(text)
        if not info:
            continue
        out.at[idx, "标准邮编集合"] = info["zip"]
        out.at[idx, "邮编前三位集合"] = info["zip"][:3]
        out.at[idx, "目的州"] = info["state"]
        out.at[idx, "邮编来源"] = "仓间调拨目标仓地址"
        out.at[idx, "目的地邮编待补充"] = False
        out.at[idx, "调入仓库"] = row.get("调入仓库", "") or target
    return out


def prepare_manual_match_flexible(match_df):
    if match_df is None or match_df.empty:
        return pd.DataFrame(columns=["批次号", "补充标准邮编", "补充目的州", "补充邮编是否有效"])

    match = processors.normalize_columns(match_df).copy()
    if "批次号" not in match.columns:
        return pd.DataFrame(columns=["批次号", "补充标准邮编", "补充目的州", "补充邮编是否有效"])

    zip_col = _find_col(match, ["标准邮编", "目的地邮编", "邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"])
    if not zip_col:
        return pd.DataFrame(columns=["批次号", "补充标准邮编", "补充目的州", "补充邮编是否有效"])

    state_col = _find_col(match, ["目的州", "省/州", "州", "到达州", "目的地州", "State", "Destination State"])
    platform_col = _find_col(match, ["平台名称", "平台仓", "平台", "渠道", "客户平台"])
    warehouse_code_col = _find_col(match, ["FBX代码", "平台仓代码", "仓库代码", "仓库Code", "仓点代码", "目的仓代码", "Warehouse Code"])
    remark_cols = [c for c in REMARK_COLS if c in match.columns]
    grouped = {}
    for _, row in match.iterrows():
        batch = str(row.get("批次号", "")).strip()
        if not batch or batch.lower() in ["nan", "none", "null"]:
            continue
        zip_code, fix, valid, reason = _normalize_zip(row.get(zip_col))
        state = _normalize_state(row.get(state_col)) if state_col else ""
        platform = str(row.get(platform_col, "")).strip() if platform_col else ""
        wh_code = str(row.get(warehouse_code_col, "")).strip() if warehouse_code_col else ""
        remarks = [row.get(c, "") for c in remark_cols]
        entry = grouped.setdefault(batch, {"zips": [], "states": [], "fixes": [], "errors": [], "remarks": [], "platforms": [], "warehouse_codes": [], "platform_pairs": []})
        if valid and zip_code:
            if zip_code not in entry["zips"]:
                entry["zips"].append(zip_code)
            if state and state not in entry["states"]:
                entry["states"].append(state)
            if fix and fix not in entry["fixes"]:
                entry["fixes"].append(fix)
        else:
            if reason and reason not in entry["errors"]:
                entry["errors"].append(reason)
        if _valid_platform_label(platform) and platform not in entry["platforms"]:
            entry["platforms"].append(platform)
        if _valid_platform_label(wh_code) and wh_code not in entry["warehouse_codes"]:
            entry["warehouse_codes"].append(wh_code)
        if _valid_platform_label(platform) and _valid_platform_label(wh_code):
            pair = f"{platform}||{wh_code}"
            if pair not in entry["platform_pairs"]:
                entry["platform_pairs"].append(pair)
        for text in remarks:
            if not _is_blank(text) and str(text).strip() not in entry["remarks"]:
                entry["remarks"].append(str(text).strip())
    rows = []
    for batch, entry in grouped.items():
        rows.append({
            "批次号": batch,
            "补充标准邮编": ",".join(entry["zips"]),
            "补充目的州": ",".join(entry["states"]),
            "补充邮编修正类型": ",".join(entry["fixes"]) if entry["fixes"] else "",
            "补充邮编是否有效": len(entry["zips"]) > 0,
            "补充邮编异常原因": "" if entry["zips"] else "; ".join(entry["errors"]),
            "补充平台名称": ",".join(entry["platforms"]),
            "补充平台仓代码": ",".join(entry["warehouse_codes"]),
            "补充平台仓配对": ";".join(entry["platform_pairs"]),
            "匹配备注集合": _combine_unique(entry["remarks"]),
        })
    return pd.DataFrame(rows)


def apply_manual_match_to_cleaned_batches_flexible(cleaned_batches, match_df):
    df = _sync_fbx_code_columns(_fill_transfer_zip_memory(_fill_fba_zip_memory(cleaned_batches)))
    match = prepare_manual_match_flexible(match_df)
    if df.empty or match.empty:
        df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
        return _sync_fbx_code_columns(_fill_transfer_zip_memory(df))

    match_map = match.set_index("批次号").to_dict("index")
    for col in ["目的州", "邮编来源", "匹配备注集合", "FBX代码集合", "平台仓代码集合", "平台仓配对集合", "平台名称"]:
        if col not in df.columns:
            df[col] = ""
    for idx, row in df.iterrows():
        existing_zips = _split_values(row.get("标准邮编集合", ""))
        batch_ids = _split_values(row.get("批次号集合", row.get("批次号", "")))
        zips, states, remarks, platforms, wh_codes, pairs = [], [], [], [], [], []
        for batch in batch_ids:
            rec = match_map.get(batch)
            if not rec:
                continue
            remark_text = rec.get("匹配备注集合", "")
            if not _is_blank(remark_text) and remark_text not in remarks:
                remarks.append(remark_text)
            for p in _split_values(rec.get("补充平台名称", "")):
                if _valid_platform_label(p) and p not in platforms:
                    platforms.append(p)
            for code in _split_values(rec.get("补充平台仓代码", "")):
                if _valid_platform_label(code) and code not in wh_codes:
                    wh_codes.append(code)
            for pair in [p for p in str(rec.get("补充平台仓配对", "")).split(";") if p.strip()]:
                if pair not in pairs:
                    pairs.append(pair)
            if rec.get("补充邮编是否有效"):
                for z in _split_values(rec.get("补充标准邮编", "")):
                    if z not in zips:
                        zips.append(z)
                for s in _split_values(rec.get("补充目的州", "")):
                    if s not in states:
                        states.append(s)
        if remarks:
            df.at[idx, "匹配备注集合"] = _combine_unique(remarks)
        if wh_codes:
            df.at[idx, "FBX代码集合"] = ",".join(wh_codes)
            df.at[idx, "平台仓代码集合"] = ",".join(wh_codes)
        if pairs:
            df.at[idx, "平台仓配对集合"] = ";".join(pairs)
        if platforms and (not _valid_platform_label(row.get("平台名称", ""))):
            df.at[idx, "平台名称"] = ",".join(platforms)
        if existing_zips:
            continue
        if zips:
            df.at[idx, "标准邮编集合"] = ",".join(zips)
            df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            df.at[idx, "邮编来源"] = "批次号人工匹配补充"
            df.at[idx, "目的地邮编待补充"] = False
        if states:
            df.at[idx, "目的州"] = ",".join(states)
    df = _fill_transfer_zip_memory(df)
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
    return _sync_fbx_code_columns(df)


def _pick_zip_from_audit_row(row):
    for col in [ZIP_FILL_COL, "待填邮编", "补充邮编", "目的地邮编", "邮编", "标准邮编", "标准邮编集合"]:
        if col not in row.index:
            continue
        values = _split_values(row.get(col, ""))
        valid_zips = []
        for value in values:
            zip_code, _, valid, _ = _normalize_zip(value)
            if valid and zip_code:
                valid_zips.append(zip_code)
        if valid_zips:
            return ",".join(list(dict.fromkeys(valid_zips)))
    return ""


def _apply_zip_audit_updates(main_df, audit_df):
    if main_df is None or main_df.empty or audit_df is None or audit_df.empty:
        return main_df
    df = main_df.copy()
    audit = audit_df.copy()
    for col in ["标准邮编集合", "邮编前三位集合", "目的州", "邮编来源", "目的地邮编待补充"]:
        if col not in df.columns:
            df[col] = ""

    def build_key(row):
        if "分析批次ID" in row.index and not _is_blank(row.get("分析批次ID")):
            return f"ID::{str(row.get('分析批次ID')).strip()}"
        if "批次号集合" in row.index and not _is_blank(row.get("批次号集合")):
            return f"BATCH::{str(row.get('批次号集合')).strip()}"
        return ""

    main_index = {build_key(row): idx for idx, row in df.iterrows() if build_key(row)}
    for _, row in audit.iterrows():
        key = build_key(row)
        if not key or key not in main_index:
            continue
        zip_value = _pick_zip_from_audit_row(row)
        if not zip_value:
            continue
        idx = main_index[key]
        state = ""
        for state_col in [STATE_FILL_COL, "目的州", "州", "省/州"]:
            if state_col in row.index and not _is_blank(row.get(state_col)):
                state = _normalize_state(row.get(state_col))
                break
        zips = _split_values(zip_value)
        df.at[idx, "标准邮编集合"] = ",".join(zips)
        df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
        if state:
            df.at[idx, "目的州"] = state
        df.at[idx, "邮编来源"] = "邮编异常审核人工补充"
        df.at[idx, "目的地邮编待补充"] = False
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
    return df


def read_stage1_or_stage2_with_audit_updates(excel_file):
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
    df = pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)
    if "邮编异常审核" in xls.sheet_names:
        excel_file.seek(0)
        audit_df = pd.read_excel(excel_file, sheet_name="邮编异常审核", dtype=str)
        df = _apply_zip_audit_updates(df, audit_df)
    return df


def identify_linehaul_with_remark_priority(row, delivery_workflow_module):
    if str(row.get("仓库", "")).strip() not in ["LA", "美西仓", "美西二号仓", "CA"]:
        return "非LA干线", "非LA仓暂不识别LA干线"
    text_parts = [row.get("匹配备注集合", ""), row.get("批次号集合", ""), row.get("车次号", ""), row.get("调入仓库", ""), row.get("业务场景", ""), row.get("出库类型", "")]
    line, reason = _infer_linehaul_from_text(" ".join(str(x) for x in text_parts if not _is_blank(x)))
    if line:
        return line, reason
    return delivery_workflow_module._original_identify_linehaul_second_part(row)


def apply_linehaul_rules_with_remark_priority(df, delivery_workflow_module):
    if df.empty:
        return df
    line_results = df.apply(lambda row: identify_linehaul_with_remark_priority(row, delivery_workflow_module), axis=1, result_type="expand")
    line_results.columns = ["专线线路", "专线识别方式"]
    out = df.drop(columns=["专线线路", "专线识别方式"], errors="ignore")
    return pd.concat([out, line_results], axis=1)


def _expand_volume_by_codes(df, code_col, volume_col, warehouse_col="仓库", period_col="统计周期", platform_col=None, pair_col=None, exclude_invalid=True):
    rows = []
    if df.empty:
        return pd.DataFrame()
    for _, row in df.iterrows():
        volume = pd.to_numeric(row.get(volume_col, 0), errors="coerce")
        if pd.isna(volume) or volume <= 0:
            continue
        if pair_col and pair_col in df.columns:
            pairs = [p for p in str(row.get(pair_col, "")).split(";") if p.strip() and "||" in p]
            if pairs:
                share_volume = float(volume) / len(pairs)
                for pair in pairs:
                    platform, code = pair.split("||", 1)
                    if _valid_platform_label(platform) and _valid_platform_label(code):
                        rows.append({"仓库": row.get(warehouse_col, ""), "统计周期": row.get(period_col, ""), "平台": platform, "仓点代码": code, "出库体积": share_volume})
                continue
        codes = _split_values(row.get(code_col, "")) if code_col in row.index else []
        if exclude_invalid:
            codes = [c for c in codes if _valid_platform_label(c)]
        if not codes:
            continue
        share_volume = float(volume) / len(codes)
        platform_value = row.get(platform_col, "") if platform_col else ""
        for code in codes:
            rows.append({"仓库": row.get(warehouse_col, ""), "统计周期": row.get(period_col, ""), "平台": platform_value, "仓点代码": code, "出库体积": share_volume})
    return pd.DataFrame(rows)


def _cost_vehicle_group(row):
    vehicle = str(row.get("车型标准值", ""))
    loading = str(row.get("装车类型标准值", ""))
    if "26" in vehicle or "小车" in vehicle:
        return "小车"
    if "53" in vehicle or "大车" in vehicle:
        if "卡板" in loading:
            return "大车卡板"
        if "地板" in loading:
            return "大车地板"
        return "大车未知装车"
    return "未知车型"


def _expand_cost_by_station(df, object_type, vehicle_group_override=None):
    rows = []
    if df.empty:
        return pd.DataFrame()
    for _, row in df.iterrows():
        vehicle_group = vehicle_group_override or _cost_vehicle_group(row)
        if vehicle_group not in ["小车", "大车卡板", "大车地板", "大车未知装车", "LTL"]:
            continue
        volume = pd.to_numeric(row.get("出库体积", 0), errors="coerce")
        cost = pd.to_numeric(row.get("派送成本", 0), errors="coerce")
        pallets = pd.to_numeric(row.get("出库卡板数", 0), errors="coerce")
        volume = 0 if pd.isna(volume) else float(volume)
        cost = 0 if pd.isna(cost) else float(cost)
        pallets = 0 if pd.isna(pallets) else float(pallets)
        if volume <= 0 and cost <= 0:
            continue
        objects = []
        if object_type == "FBA":
            for code in _split_values(row.get("FBA仓点代码集合", "")):
                if code:
                    objects.append(("FBA", code))
        else:
            pairs = [p for p in str(row.get("平台仓配对集合", "")).split(";") if p.strip() and "||" in p]
            if pairs:
                for pair in pairs:
                    platform, code = pair.split("||", 1)
                    if _valid_platform_label(platform) and _valid_platform_label(code):
                        objects.append((platform, code))
            else:
                code_values = row.get("FBX代码集合", row.get("平台仓代码集合", ""))
                codes = [c for c in _split_values(code_values) if _valid_platform_label(c)]
                platforms = [p for p in _split_values(row.get("平台名称", "")) if _valid_platform_label(p)]
                if len(platforms) == len(codes) and codes:
                    objects.extend(list(zip(platforms, codes)))
                elif codes:
                    platform = platforms[0] if len(platforms) == 1 else ""
                    for code in codes:
                        objects.append((platform, code))
        if not objects:
            continue
        share_volume = volume / len(objects)
        share_cost = cost / len(objects)
        share_pallets = pallets / len(objects)
        for platform, code in objects:
            rows.append({
                "仓库": row.get("仓库", ""), "统计周期": row.get("统计周期", ""),
                "对象类型": object_type if object_type == "FBA" else "FBX平台仓",
                "平台": platform if object_type != "FBA" else "FBA", "仓点代码": code,
                "车型装车分组": vehicle_group, "车次数": 1,
                "出库体积": share_volume, "出库卡板数": share_pallets, "派送成本": share_cost,
            })
    return pd.DataFrame(rows)


def build_fba_rank_sheet(matched):
    source = matched[matched.get("FBA出库体积", 0) > 0].copy() if not matched.empty else pd.DataFrame()
    expanded = _expand_volume_by_codes(source, "FBA仓点代码集合", "FBA出库体积", platform_col=None, exclude_invalid=False)
    if expanded.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "排名", "FBA仓点", "出库体积", "占比"])
    agg = expanded.groupby(["仓库", "统计周期", "仓点代码"], dropna=False)["出库体积"].sum().reset_index()
    agg = agg[agg["出库体积"] > 0].sort_values(["仓库", "统计周期", "出库体积"], ascending=[True, True, False])
    total = agg.groupby(["仓库", "统计周期"])["出库体积"].transform("sum")
    agg["占比"] = agg["出库体积"] / total
    agg["排名"] = agg.groupby(["仓库", "统计周期"])["出库体积"].rank(method="first", ascending=False).astype(int)
    return agg.rename(columns={"仓点代码": "FBA仓点"})[["仓库", "统计周期", "排名", "FBA仓点", "出库体积", "占比"]]


def build_fbx_platform_warehouse_sheet(matched):
    if matched.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "排名", "平台仓", "FBX代码", "出库体积", "占比"])
    source = _sync_fbx_code_columns(matched[(matched.get("FBX出库体积", 0) > 0)].copy())
    source = source[source["平台名称"].apply(_valid_platform_label)] if "平台名称" in source.columns else source.iloc[0:0]
    code_col = "FBX代码集合" if "FBX代码集合" in source.columns else "平台仓代码集合"
    expanded = _expand_volume_by_codes(source, code_col, "FBX出库体积", platform_col="平台名称", pair_col="平台仓配对集合", exclude_invalid=True)
    if expanded.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "排名", "平台仓", "FBX代码", "出库体积", "占比"])
    agg = expanded.groupby(["仓库", "统计周期", "平台", "仓点代码"], dropna=False)["出库体积"].sum().reset_index()
    agg = agg[(agg["出库体积"] > 0) & agg["平台"].apply(_valid_platform_label) & agg["仓点代码"].apply(_valid_platform_label)]
    agg = agg.sort_values(["仓库", "统计周期", "出库体积"], ascending=[True, True, False])
    total = agg.groupby(["仓库", "统计周期"])["出库体积"].transform("sum")
    agg["占比"] = agg["出库体积"] / total
    agg["排名"] = agg.groupby(["仓库", "统计周期"])["出库体积"].rank(method="first", ascending=False).astype(int)
    return agg.rename(columns={"平台": "平台仓", "仓点代码": "FBX代码"})[["仓库", "统计周期", "排名", "平台仓", "FBX代码", "出库体积", "占比"]]


def build_station_cost_report(matched):
    if matched.empty:
        return pd.DataFrame()
    ftl = matched[matched.get("是否FTL发车", False)].copy()
    if ftl.empty:
        return pd.DataFrame()
    rows = []
    full_load = ftl[(ftl["车型标准值"] == "53尺大车") & (ftl["装车类型标准值"] == "地板")].copy()
    for (warehouse, period), group in full_load.groupby(["仓库", "统计周期"], dropna=False):
        rows.append({
            "指标名称": "满载情况", "仓库": warehouse, "统计周期": period,
            "对象类型": "FTL大车地板", "平台": "全部", "仓点代码": "全部", "车型装车分组": "大车地板",
            "车次数": int(len(group)), "总出库体积": group["出库体积"].sum(), "总派送成本": group["派送成本"].sum(),
            "平均整车价": None, "每方平均价": None,
            "平均每车出库体积": processors.regular_delivery_average_sample_rows(group)["出库体积"].mean(),
            "P80每车出库体积": processors.safe_p80(processors.regular_delivery_average_sample_rows(group)["出库体积"]),
        })
    cost_source = ftl[ftl["主产品类型"].isin(["FBA", "FBX"])].copy()
    expanded = pd.concat([
        _expand_cost_by_station(cost_source[cost_source["主产品类型"] == "FBA"], "FBA"),
        _expand_cost_by_station(cost_source[cost_source["主产品类型"] == "FBX"], "FBX平台仓"),
    ], ignore_index=True)
    if not expanded.empty:
        # 在明细聚合前统一平台/仓点大小写，避免导出层为了合并大小写重复项
        # 再次按“总额/车次数”重算并覆盖已筛选样本的平均值与P80。
        expanded = tool_common.normalize_case_insensitive_labels(expanded)
        group_cols = ["仓库", "统计周期", "对象类型", "平台", "仓点代码", "车型装车分组"]
        for keys, group in expanded.groupby(group_cols, dropna=False):
            total_volume = group["出库体积"].sum()
            total_pallets = group["出库卡板数"].sum()
            total_cost = group["派送成本"].sum()
            if total_volume <= 0 and total_cost <= 0:
                continue
            average_group = processors.regular_delivery_average_sample_rows(group)
            row = dict(zip(group_cols, keys))
            row.update({
                "指标名称": "FBA及FBX平台仓成本",
                "车次数": int(group["车次数"].sum()),
                "总出库体积": total_volume,
                "总出库卡板数": total_pallets,
                "总派送成本": total_cost,
                "平均整车价": average_group["派送成本"].mean() if not average_group.empty else pd.NA,
                "P80整车价": processors.safe_p80(average_group["派送成本"]),
                "每方平均价": processors.mean_detail_ratio(average_group, "派送成本", "出库体积"),
                "平均每车出库体积": average_group["出库体积"].mean() if not average_group.empty else pd.NA,
                "P80每车出库体积": processors.safe_p80(average_group["出库体积"]),
                "平均每车出库卡板数": average_group["出库卡板数"].mean() if not average_group.empty else pd.NA,
                "P80每车出库卡板数": processors.safe_p80(average_group["出库卡板数"]),
            })
            rows.append(row)
    return pd.DataFrame(rows)


def build_ltl_station_cost_report(matched):
    """按目的仓点汇总LTL成本，只保留三个总量指标。

    LTL不按车次计算均值或P80；只统计派送成本大于0、且能明确匹配到
    FBA仓点或FBX平台仓点的明细。
    """
    columns = [
        "指标名称", "仓库", "统计周期", "对象类型", "平台", "仓点代码", "车型装车分组",
        "总出库体积", "总出库卡板数", "总派送成本",
    ]
    if matched is None or matched.empty:
        return pd.DataFrame(columns=columns)

    source = matched.copy()
    if "标准运输类型" in source.columns:
        source = source[source["标准运输类型"].astype(str).str.upper().eq("LTL")].copy()
    elif "是否FTL发车" in source.columns:
        ftl_mask = tool_common.normalize_boolean_series(source["是否FTL发车"])
        source = source[~ftl_mask].copy()
    else:
        return pd.DataFrame(columns=columns)

    for col in ["出库体积", "出库卡板数", "派送成本"]:
        if col not in source.columns:
            source[col] = 0
        source[col] = pd.to_numeric(source[col], errors="coerce").fillna(0)
    if "主产品类型" not in source.columns:
        return pd.DataFrame(columns=columns)

    source = source[
        source["主产品类型"].isin(["FBA", "FBX"])
        & source["派送成本"].gt(0)
    ].copy()
    expanded = pd.concat([
        _expand_cost_by_station(
            source[source["主产品类型"] == "FBA"],
            "FBA",
            vehicle_group_override="LTL",
        ),
        _expand_cost_by_station(
            source[source["主产品类型"] == "FBX"],
            "FBX平台仓",
            vehicle_group_override="LTL",
        ),
    ], ignore_index=True)
    if expanded.empty:
        return pd.DataFrame(columns=columns)

    expanded = tool_common.normalize_case_insensitive_labels(expanded)
    group_cols = ["仓库", "统计周期", "对象类型", "平台", "仓点代码", "车型装车分组"]
    result = expanded.groupby(group_cols, dropna=False, as_index=False).agg(
        总出库体积=("出库体积", "sum"),
        总出库卡板数=("出库卡板数", "sum"),
        总派送成本=("派送成本", "sum"),
    )
    result.insert(0, "指标名称", "FBA及FBX平台仓LTL成本")
    return result[columns]


def _safe_round(df, sheet_type):
    # 先按原有逻辑保留两位小数，再把计数列改回整数。
    rounded = processors.round_output_numbers(df, processors.RESULT_DECIMALS) if df is not None else df
    return _format_numbers(rounded, sheet_type=sheet_type)


def build_split_stage2_report(delivery_workflow_module, cleaned_batches, match_df, period_type="按周统计"):
    matched = delivery_workflow_module.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    combined = delivery_workflow_module.build_sheet1_volume_dispatch_time_report(matched)
    if combined.empty:
        volume = dispatch = timing = combined.copy()
    else:
        volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
        volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
        dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
        timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()
    cost_ftl = build_station_cost_report(matched)
    cost_ltl = build_ltl_station_cost_report(matched)
    zip_audit = matched[matched["目的地邮编待补充"]].copy() if "目的地邮编待补充" in matched.columns else pd.DataFrame()
    return {
        "货量": _safe_round(_finalize_sheet(volume, "货量"), "货量"),
        "FBA货量排行": _safe_round(_finalize_sheet(build_fba_rank_sheet(matched), "FBA货量排行"), "FBA货量排行"),
        "FBX平台仓货量": _safe_round(_finalize_sheet(build_fbx_platform_warehouse_sheet(matched), "FBX平台仓货量"), "FBX平台仓货量"),
        "发车量": _safe_round(_finalize_sheet(dispatch, "发车量"), "发车量"),
        "派送时效": _safe_round(_finalize_sheet(timing, "派送时效"), "派送时效"),
        "成本FTL": _safe_round(_finalize_sheet(cost_ftl, "成本"), "成本"),
        "成本LTL": _safe_round(_finalize_sheet(cost_ltl, "成本"), "成本"),
        "派送二_匹配后合并数据": _safe_round(_finalize_sheet(matched, "明细"), "明细"),
        "邮编异常审核": _finalize_zip_audit_sheet(zip_audit),
        "区域识别规则": delivery_workflow_module.REGION_RULES_DF,
        "干线识别规则": delivery_workflow_module.LINEHAUL_RULES,
    }


def patch_delivery_workflow(delivery_workflow_module):
    delivery_workflow_module.prepare_manual_match = prepare_manual_match_flexible
    delivery_workflow_module.apply_manual_match_to_cleaned_batches = apply_manual_match_to_cleaned_batches_flexible
    delivery_workflow_module.read_stage1_cleaned_batches = read_stage1_or_stage2_with_audit_updates
    if not hasattr(delivery_workflow_module, "_original_identify_linehaul_second_part"):
        delivery_workflow_module._original_identify_linehaul_second_part = delivery_workflow_module.identify_linehaul_second_part
    delivery_workflow_module.identify_linehaul_second_part = lambda row: identify_linehaul_with_remark_priority(row, delivery_workflow_module)
    delivery_workflow_module.apply_linehaul_rules_second_part = lambda df: apply_linehaul_rules_with_remark_priority(df, delivery_workflow_module)
    delivery_workflow_module.process_stage2_analysis = lambda cleaned_batches, match_df=None, period_type="按周统计": build_split_stage2_report(delivery_workflow_module, cleaned_batches, match_df, period_type)
    return delivery_workflow_module
