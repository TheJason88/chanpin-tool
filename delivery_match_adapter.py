import re

import pandas as pd

import processors
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


def _infer_linehaul_from_text(text):
    upper = str(text).upper()
    if re.search(r"\bSAV\b|转\s*SAV|SAVANNAH", upper):
        return "LA-SAV", "车次/批次备注命中SAV"
    if re.search(r"\bDAL\b|转\s*DAL|DALLAS", upper):
        return "LA-DAL", "车次/批次备注命中DAL"
    if re.search(r"\bNJ\b|转\s*NJ|NEW\s*JERSEY", upper):
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


def _drop_empty_columns(df):
    if df is None or df.empty:
        return df
    out = df.copy()
    keep_cols = []
    for col in out.columns:
        s = out[col]
        non_empty = s.apply(lambda x: not (_is_blank(x) or (pd.isna(x) if not isinstance(x, (list, dict, tuple, set)) else False)))
        if non_empty.any():
            keep_cols.append(col)
    return out[keep_cols]


def _finalize_sheet(df):
    return _drop_empty_columns(_strip_remark_columns(df))


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


def prepare_manual_match_flexible(match_df):
    match = processors.normalize_columns(match_df).copy()
    processors.require_columns(match, ["批次号"], "人工目的地匹配文件")
    zip_col = _find_col(match, ["标准邮编", "目的地邮编", "邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"])
    if not zip_col:
        raise ValueError("人工目的地匹配文件需要包含 标准邮编 / 目的地邮编 / 邮编 字段。")
    state_col = _find_col(match, ["目的州", "省/州", "州", "到达州", "目的地州", "State", "Destination State"])
    platform_col = _find_col(match, ["平台名称", "平台", "渠道", "客户平台"])
    warehouse_code_col = _find_col(match, ["平台仓代码", "仓库代码", "仓库Code", "仓点代码", "目的仓代码", "Warehouse Code"])
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
        entry = grouped.setdefault(batch, {"zips": [], "states": [], "fixes": [], "errors": [], "remarks": [], "platforms": [], "warehouse_codes": []})
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
            "匹配备注集合": _combine_unique(entry["remarks"]),
        })
    return pd.DataFrame(rows)


def apply_manual_match_to_cleaned_batches_flexible(cleaned_batches, match_df):
    df = _fill_fba_zip_memory(cleaned_batches)
    match = prepare_manual_match_flexible(match_df)
    if df.empty or match.empty:
        return df
    match_map = match.set_index("批次号").to_dict("index")
    for col in ["目的州", "邮编来源", "匹配备注集合", "平台仓代码集合", "平台名称"]:
        if col not in df.columns:
            df[col] = ""
    for idx, row in df.iterrows():
        existing_zips = _split_values(row.get("标准邮编集合", ""))
        batch_ids = _split_values(row.get("批次号集合", row.get("批次号", "")))
        zips, states, remarks, platforms, wh_codes = [], [], [], [], []
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
            df.at[idx, "平台仓代码集合"] = ",".join(wh_codes)
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
    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
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


def _expand_volume_by_codes(df, code_col, volume_col, warehouse_col="仓库", period_col="统计周期", platform_col=None, exclude_invalid=True):
    rows = []
    if df.empty or code_col not in df.columns:
        return pd.DataFrame()
    for _, row in df.iterrows():
        volume = pd.to_numeric(row.get(volume_col, 0), errors="coerce")
        if pd.isna(volume) or volume <= 0:
            continue
        codes = _split_values(row.get(code_col, ""))
        if exclude_invalid:
            codes = [c for c in codes if _valid_platform_label(c)]
        if not codes:
            continue
        share_volume = float(volume) / len(codes)
        for code in codes:
            rows.append({
                "仓库": row.get(warehouse_col, ""),
                "统计周期": row.get(period_col, ""),
                "平台": row.get(platform_col, "") if platform_col else "",
                "仓点代码": code,
                "出库体积": share_volume,
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
    agg = agg.rename(columns={"仓点代码": "FBA仓点"})[["仓库", "统计周期", "排名", "FBA仓点", "出库体积", "占比"]]
    return agg


def build_fbx_platform_warehouse_sheet(matched):
    if matched.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "排名", "平台", "平台仓代码", "出库体积", "占比"])
    source = matched[(matched.get("FBX出库体积", 0) > 0)].copy()
    source = source[source["平台名称"].apply(_valid_platform_label)] if "平台名称" in source.columns else source.iloc[0:0]
    expanded = _expand_volume_by_codes(source, "平台仓代码集合", "FBX出库体积", platform_col="平台名称", exclude_invalid=True)
    if expanded.empty:
        return pd.DataFrame(columns=["仓库", "统计周期", "排名", "平台", "平台仓代码", "出库体积", "占比"])
    agg = expanded.groupby(["仓库", "统计周期", "平台", "仓点代码"], dropna=False)["出库体积"].sum().reset_index()
    agg = agg[(agg["出库体积"] > 0) & agg["平台"].apply(_valid_platform_label) & agg["仓点代码"].apply(_valid_platform_label)]
    agg = agg.sort_values(["仓库", "统计周期", "出库体积"], ascending=[True, True, False])
    total = agg.groupby(["仓库", "统计周期"])["出库体积"].transform("sum")
    agg["占比"] = agg["出库体积"] / total
    agg["排名"] = agg.groupby(["仓库", "统计周期"])["出库体积"].rank(method="first", ascending=False).astype(int)
    agg = agg.rename(columns={"仓点代码": "平台仓代码"})[["仓库", "统计周期", "排名", "平台", "平台仓代码", "出库体积", "占比"]]
    return agg


def build_split_stage2_report(delivery_workflow_module, cleaned_batches, match_df, period_type="按周统计"):
    matched = delivery_workflow_module.prepare_stage2_for_report(cleaned_batches, match_df, period_type)
    combined = delivery_workflow_module.build_sheet1_volume_dispatch_time_report(matched)
    cost = delivery_workflow_module.build_sheet2_cost_report(matched)
    if combined.empty:
        volume = dispatch = timing = combined.copy()
    else:
        volume = combined[combined["报告部分"].astype(str).str.startswith("1.")].copy()
        volume = volume[~volume["指标名称"].astype(str).isin(["FBA仓点货量排行", "FBX平台仓货量排行"])]
        dispatch = combined[combined["报告部分"].astype(str).str.startswith("2.")].copy()
        timing = combined[combined["报告部分"].astype(str).str.startswith("3.")].copy()
    return {
        "货量": processors.round_output_numbers(_finalize_sheet(volume), processors.RESULT_DECIMALS),
        "FBA货量排行": processors.round_output_numbers(_finalize_sheet(build_fba_rank_sheet(matched)), processors.RESULT_DECIMALS),
        "FBX平台仓货量": processors.round_output_numbers(_finalize_sheet(build_fbx_platform_warehouse_sheet(matched)), processors.RESULT_DECIMALS),
        "发车量": processors.round_output_numbers(_finalize_sheet(dispatch), processors.RESULT_DECIMALS),
        "派送时效": processors.round_output_numbers(_finalize_sheet(timing), processors.RESULT_DECIMALS),
        "成本": processors.round_output_numbers(_finalize_sheet(cost), processors.RESULT_DECIMALS),
        "派送二_匹配后合并数据": processors.round_output_numbers(_finalize_sheet(matched), processors.RESULT_DECIMALS),
        "邮编异常审核": _finalize_sheet(matched[matched["目的地邮编待补充"]].copy()) if "目的地邮编待补充" in matched.columns else pd.DataFrame(),
        "区域识别规则": delivery_workflow_module.REGION_RULES_DF,
        "干线识别规则": delivery_workflow_module.LINEHAUL_RULES,
    }


def patch_delivery_workflow(delivery_workflow_module):
    delivery_workflow_module.prepare_manual_match = prepare_manual_match_flexible
    delivery_workflow_module.apply_manual_match_to_cleaned_batches = apply_manual_match_to_cleaned_batches_flexible
    if not hasattr(delivery_workflow_module, "_original_identify_linehaul_second_part"):
        delivery_workflow_module._original_identify_linehaul_second_part = delivery_workflow_module.identify_linehaul_second_part
    delivery_workflow_module.identify_linehaul_second_part = lambda row: identify_linehaul_with_remark_priority(row, delivery_workflow_module)
    delivery_workflow_module.apply_linehaul_rules_second_part = lambda df: apply_linehaul_rules_with_remark_priority(df, delivery_workflow_module)
    delivery_workflow_module.process_stage2_analysis = lambda cleaned_batches, match_df, period_type="按周统计": build_split_stage2_report(delivery_workflow_module, cleaned_batches, match_df, period_type)
    return delivery_workflow_module
