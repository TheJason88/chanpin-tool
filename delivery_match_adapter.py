import re

import pandas as pd

import processors


STATE_ALIASES = {
    "CALIFORNIA": "CA", "加州": "CA", "FLORIDA": "FL", "FL": "FL",
    "SEATTLE, WA": "WA", "WASHINGTON": "WA", "TEXAS": "TX",
    "NEW JERSEY": "NJ", "PENNSYLVANIA": "PA", "OHIO": "OH",
    "GEORGIA": "GA", "NORTH CAROLINA": "NC", "ILLINOIS": "IL",
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


def _normalize_state(value):
    if _is_blank(value):
        return ""
    text = str(value).strip()
    upper = text.upper()
    if upper in STATE_ALIASES:
        return STATE_ALIASES[upper]
    # 兼容 “Seattle, WA”、完整地址、异常混入地址的情况，提取两位州缩写。
    match = re.search(r"\b([A-Z]{2})\b", upper)
    if match:
        return match.group(1)
    return text


def _normalize_zip(value):
    zip_code, fix, valid, reason = processors.normalize_zip_value(value)
    return zip_code, fix, bool(valid), reason


def prepare_manual_match_flexible(match_df):
    """
    兼容第二部分人工匹配文件：
    1. 支持字段：批次号 + 邮编/目的地邮编/标准邮编 + 省/州/州/目的州。
    2. 支持一个批次号对应多个目的地邮编，不再只保留最后一行。
    3. 输出仍保持 delivery_workflow 原接口字段，方便后续按批次号补充。
    """
    match = processors.normalize_columns(match_df).copy()
    processors.require_columns(match, ["批次号"], "人工目的地匹配文件")

    zip_col = _find_col(match, ["标准邮编", "目的地邮编", "邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"])
    if not zip_col:
        raise ValueError("人工目的地匹配文件需要包含 标准邮编 / 目的地邮编 / 邮编 字段。")

    state_col = _find_col(match, ["目的州", "省/州", "州", "到达州", "目的地州", "State", "Destination State"])

    grouped = {}
    for _, row in match.iterrows():
        batch = str(row.get("批次号", "")).strip()
        if not batch or batch.lower() in ["nan", "none", "null"]:
            continue
        zip_code, fix, valid, reason = _normalize_zip(row.get(zip_col))
        state = _normalize_state(row.get(state_col)) if state_col else ""
        entry = grouped.setdefault(batch, {"zips": [], "states": [], "fixes": [], "errors": []})
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

    rows = []
    for batch, entry in grouped.items():
        rows.append({
            "批次号": batch,
            "补充标准邮编": ",".join(entry["zips"]),
            "补充目的州": ",".join(entry["states"]),
            "补充邮编修正类型": ",".join(entry["fixes"]) if entry["fixes"] else "",
            "补充邮编是否有效": len(entry["zips"]) > 0,
            "补充邮编异常原因": "" if entry["zips"] else "; ".join(entry["errors"]),
        })
    return pd.DataFrame(rows)


def apply_manual_match_to_cleaned_batches_flexible(cleaned_batches, match_df):
    df = cleaned_batches.copy()
    match = prepare_manual_match_flexible(match_df)
    if df.empty or match.empty:
        return df

    match_map = match.set_index("批次号").to_dict("index")
    if "目的州" not in df.columns:
        df["目的州"] = ""
    if "邮编来源" not in df.columns:
        df["邮编来源"] = ""

    for idx, row in df.iterrows():
        existing_zips = _split_values(row.get("标准邮编集合", ""))
        if existing_zips:
            continue
        batch_ids = _split_values(row.get("批次号集合", row.get("批次号", "")))
        zips = []
        states = []
        for batch in batch_ids:
            rec = match_map.get(batch)
            if not rec or not rec.get("补充邮编是否有效"):
                continue
            for z in _split_values(rec.get("补充标准邮编", "")):
                if z not in zips:
                    zips.append(z)
            for s in _split_values(rec.get("补充目的州", "")):
                if s not in states:
                    states.append(s)
        if zips:
            df.at[idx, "标准邮编集合"] = ",".join(zips)
            df.at[idx, "邮编前三位集合"] = ",".join([z[:3] for z in zips if len(z) == 5])
            df.at[idx, "邮编来源"] = "批次号人工匹配补充"
            df.at[idx, "目的地邮编待补充"] = False
        if states:
            df.at[idx, "目的州"] = ",".join(states)

    df["目的地邮编待补充"] = df["标准邮编集合"].apply(lambda x: len(_split_values(x)) == 0)
    return df


def patch_delivery_workflow(delivery_workflow_module):
    delivery_workflow_module.prepare_manual_match = prepare_manual_match_flexible
    delivery_workflow_module.apply_manual_match_to_cleaned_batches = apply_manual_match_to_cleaned_batches_flexible
    return delivery_workflow_module
