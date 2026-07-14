"""
Runtime extension for chanpin-tool.

This file is imported automatically by Python before app.py. It only installs
small, defensive patches and must never raise during startup.
"""

from __future__ import annotations

import builtins
import re
from typing import Any, Dict, Iterable, List


EXTRA_FBA_REFERENCES = [
    {
        "FBA仓点代码": "LAN2",
        "目的地示例": "Amazon-LAN2",
        "地址": "6500 W Mt Hope Hwy, Lansing, MI",
        "邮编": "48917",
        "邮编前三位": "489",
        "州": "MI",
        "站点名称": "Amazon/LAN2",
    },
    {
        "FBA仓点代码": "LGB9",
        "目的地示例": "Amazon-LGB9",
        "地址": "4375 N Perris Blvd, Perris, CA",
        "邮编": "92571",
        "邮编前三位": "925",
        "州": "CA",
        "站点名称": "Amazon/LGB9",
    },
    {
        "FBA仓点代码": "HOU7",
        "目的地示例": "Amazon-HOU7",
        "地址": "16225-A Tomball Pkwy, Houston, TX",
        "邮编": "77064",
        "邮编前三位": "770",
        "州": "TX",
        "站点名称": "Amazon/HOU7",
    },
]

RAW_ADDRESS_SOURCE_COLS = [
    "源文件地址", "源文件地址参考", "地址", "目的地", "修正后目的地", "目的地址", "派送地址", "收货地址", "Destination", "Address", "ADDRESS", "转仓地址",
]
RAW_CITY_SOURCE_COLS = ["源文件城市", "城市", "目的城市", "City", "CITY"]
RAW_STATE_SOURCE_COLS = ["源文件省州", "省/州", "省州", "省份", "州", "目的州", "State", "STATE", "Destination State", "DestinationState"]
RAW_ZIP_SOURCE_COLS = ["源文件邮编", "邮编", "目的地邮编", "标准邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"]
RAW_REFERENCE_COLS = ["源文件地址参考", "源文件地址", "源文件城市", "源文件省州", "源文件邮编"]

MATCH_ADDRESS_SOURCE_COLS = ["地址", "目的地", "修正后目的地", "目的地址", "派送地址", "收货地址", "Destination", "Address", "ADDRESS"]
MATCH_CITY_SOURCE_COLS = ["城市", "目的城市", "City", "CITY"]
MATCH_STATE_SOURCE_COLS = ["省/州", "省州", "省份", "州", "目的州", "State", "STATE", "Destination State", "DestinationState"]
MATCH_ZIP_SOURCE_COLS = ["邮编", "目的地邮编", "标准邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"]
MATCH_REFERENCE_COLS = ["匹配文件地址参考", "匹配文件地址", "匹配文件城市", "匹配文件省州", "匹配文件邮编"]


_ORIGINAL_IMPORT = builtins.__import__
_PATCHING = False


def _is_blank(value: Any) -> bool:
    try:
        import pandas as pd
        if value is None or pd.isna(value):
            return True
    except Exception:
        if value is None:
            return True
    text = str(value).strip()
    return text.lower() in {"", "nan", "none", "null", "<na>", "false", "0"} or text in {"/", "//", ";", ";;", ";/", "/;", "-"}


def _clean_text(value: Any) -> str:
    if _is_blank(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def _combine(values: Iterable[Any], sep: str = " / ") -> str:
    out: List[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in out:
            out.append(text)
    return sep.join(out)


def _split_batch_ids(value: Any) -> List[str]:
    text = _clean_text(value)
    if not text:
        return []
    parts = re.split(r"[,，;；\s]+", text)
    return [p.strip() for p in parts if p.strip()]


def _normalize_batch_key(value: Any) -> str:
    text = _clean_text(value)
    if re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def _first_existing_col(df, candidates: Iterable[str]) -> str:
    if df is None:
        return ""
    columns = list(getattr(df, "columns", []))
    for col in candidates:
        if col in columns:
            return col
    return ""


def _extract_ref_from_row(row, address_col="", city_col="", state_col="", zip_col="", prefix="源文件") -> Dict[str, str]:
    address = _clean_text(row.get(address_col, "")) if address_col else ""
    city = _clean_text(row.get(city_col, "")) if city_col else ""
    state = _clean_text(row.get(state_col, "")) if state_col else ""
    zip_code = _clean_text(row.get(zip_col, "")) if zip_col else ""
    ref = _combine([address, city, state, zip_code], sep=", ")
    return {
        f"{prefix}地址参考": ref,
        f"{prefix}地址": address,
        f"{prefix}城市": city,
        f"{prefix}省州": state,
        f"{prefix}邮编": zip_code,
    }


def _ensure_columns(df, cols: Iterable[str]):
    try:
        for col in cols:
            if col not in df.columns:
                df[col] = ""
    except Exception:
        pass
    return df


def _merge_ref_dicts(refs: Iterable[Dict[str, str]], cols: Iterable[str]) -> Dict[str, str]:
    return {col: _combine(ref.get(col, "") for ref in refs) for col in cols}


def _address_ref_from_group(group):
    address_col = _first_existing_col(group, RAW_ADDRESS_SOURCE_COLS)
    city_col = _first_existing_col(group, RAW_CITY_SOURCE_COLS)
    state_col = _first_existing_col(group, RAW_STATE_SOURCE_COLS)
    zip_col = _first_existing_col(group, RAW_ZIP_SOURCE_COLS)
    refs = [_extract_ref_from_row(row, address_col, city_col, state_col, zip_col, prefix="源文件") for _, row in group.iterrows()]
    return _merge_ref_dicts(refs, RAW_REFERENCE_COLS)


def _source_address_by_batch(raw_detail):
    if raw_detail is None or getattr(raw_detail, "empty", True) or "批次号" not in raw_detail.columns:
        return {}
    raw = raw_detail.copy()
    raw["__批次匹配Key"] = raw["批次号"].apply(_normalize_batch_key)
    result = {}
    for key, group in raw.groupby("__批次匹配Key", dropna=False):
        if key:
            result[str(key)] = _address_ref_from_group(group)
    return result


def _enrich_cleaned_with_source_address(cleaned_batches, raw_detail):
    if cleaned_batches is None or getattr(cleaned_batches, "empty", True):
        return cleaned_batches
    out = _ensure_columns(cleaned_batches.copy(), RAW_REFERENCE_COLS)
    batch_ref = _source_address_by_batch(raw_detail)
    if not batch_ref:
        return out
    for idx, row in out.iterrows():
        batch_ids = _split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        refs = [batch_ref[_normalize_batch_key(batch)] for batch in batch_ids if _normalize_batch_key(batch) in batch_ref]
        if not refs:
            continue
        merged = _merge_ref_dicts(refs, RAW_REFERENCE_COLS)
        for col in RAW_REFERENCE_COLS:
            if merged.get(col):
                out.at[idx, col] = merged[col]
    return out


def _normalize_match_columns(match_df):
    try:
        import processors
        return processors.normalize_columns(match_df).copy()
    except Exception:
        try:
            return match_df.copy()
        except Exception:
            return match_df


def _match_address_reference_by_batch(match_df):
    try:
        import pandas as pd
        if match_df is None or getattr(match_df, "empty", True):
            return pd.DataFrame(columns=["批次号"] + MATCH_REFERENCE_COLS)
        match = _normalize_match_columns(match_df)
        if "批次号" not in match.columns:
            return pd.DataFrame(columns=["批次号"] + MATCH_REFERENCE_COLS)
        address_col = _first_existing_col(match, MATCH_ADDRESS_SOURCE_COLS)
        city_col = _first_existing_col(match, MATCH_CITY_SOURCE_COLS)
        state_col = _first_existing_col(match, MATCH_STATE_SOURCE_COLS)
        zip_col = _first_existing_col(match, MATCH_ZIP_SOURCE_COLS)
        grouped = {}
        for _, row in match.iterrows():
            batch = _normalize_batch_key(row.get("批次号", ""))
            if not batch:
                continue
            entry = grouped.setdefault(batch, {col: [] for col in MATCH_REFERENCE_COLS})
            ref = _extract_ref_from_row(row, address_col, city_col, state_col, zip_col, prefix="匹配文件")
            for col in MATCH_REFERENCE_COLS:
                value = ref.get(col, "")
                if value and value not in entry[col]:
                    entry[col].append(value)
        rows = []
        for batch, values in grouped.items():
            row = {"批次号": batch}
            for col in MATCH_REFERENCE_COLS:
                row[col] = _combine(values[col])
            rows.append(row)
        return pd.DataFrame(rows, columns=["批次号"] + MATCH_REFERENCE_COLS)
    except Exception:
        try:
            import pandas as pd
            return pd.DataFrame(columns=["批次号"] + MATCH_REFERENCE_COLS)
        except Exception:
            return None


def _copy_match_refs_to_rows(df, match_df):
    if df is None or getattr(df, "empty", True):
        return df
    out = _ensure_columns(df.copy(), MATCH_REFERENCE_COLS)
    match_ref = _match_address_reference_by_batch(match_df)
    if match_ref is None or getattr(match_ref, "empty", True):
        return out
    lookup = match_ref.set_index("批次号", drop=False).to_dict("index")
    for idx, row in out.iterrows():
        batch_ids = _split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        refs = [lookup[_normalize_batch_key(batch)] for batch in batch_ids if _normalize_batch_key(batch) in lookup]
        if not refs:
            continue
        merged = _merge_ref_dicts(refs, MATCH_REFERENCE_COLS)
        for col in MATCH_REFERENCE_COLS:
            if merged.get(col):
                out.at[idx, col] = merged[col]
    return out


def _merge_address_from_matched(audit, matched):
    if audit is None or getattr(audit, "empty", True):
        return audit
    out = _ensure_columns(audit.copy(), RAW_REFERENCE_COLS + MATCH_REFERENCE_COLS)
    if matched is None or getattr(matched, "empty", True):
        return out
    matched = _ensure_columns(matched.copy(), RAW_REFERENCE_COLS + MATCH_REFERENCE_COLS)
    key_cols = [c for c in ["分析批次ID", "批次号集合"] if c in out.columns and c in matched.columns]
    for key_col in key_cols:
        try:
            lookup = matched.drop_duplicates(subset=[key_col], keep="first").set_index(key_col, drop=False)
        except Exception:
            continue
        for idx, row in out.iterrows():
            key = row.get(key_col, "")
            if _is_blank(key) or key not in lookup.index:
                continue
            source = lookup.loc[key]
            for col in RAW_REFERENCE_COLS + MATCH_REFERENCE_COLS:
                if _is_blank(out.at[idx, col]) and col in source.index and not _is_blank(source.get(col, "")):
                    out.at[idx, col] = source.get(col, "")
    return out


def _reorder_zip_audit_columns(df):
    if df is None or getattr(df, "empty", True):
        return df
    out = _ensure_columns(df.copy(), RAW_REFERENCE_COLS + MATCH_REFERENCE_COLS)
    desired_front = [
        "分析批次ID", "仓库", "标准运输类型", "派送方式", "车次号", "批次号集合",
        "匹配文件地址参考", "匹配文件地址", "匹配文件城市", "匹配文件省州", "匹配文件邮编",
        "源文件地址参考", "源文件地址", "源文件城市", "源文件省州", "源文件邮编",
        "补充标准邮编", "补充目的州",
    ]
    cols = [c for c in desired_front if c in out.columns] + [c for c in out.columns if c not in desired_front]
    return out[cols]


def _stage2_match_df_from_call(args, kwargs):
    if "match_df" in kwargs:
        return kwargs.get("match_df")
    if len(args) >= 3:
        return args[2]
    return None


def _patch_delivery_reference(module):
    try:
        import pandas as pd
        existing_map = getattr(module, "FBA_REFERENCE_MAP", None)
        if isinstance(existing_map, dict):
            for rec in EXTRA_FBA_REFERENCES:
                existing_map[rec["FBA仓点代码"]] = rec.copy()
        existing_df = getattr(module, "FBA_REFERENCE_DF", None)
        if existing_df is not None:
            df = existing_df.copy()
            codes = set(df.get("FBA仓点代码", pd.Series(dtype=str)).astype(str).str.upper().tolist()) if not df.empty else set()
            add_rows = [rec for rec in EXTRA_FBA_REFERENCES if rec["FBA仓点代码"] not in codes]
            if add_rows:
                module.FBA_REFERENCE_DF = pd.concat([df, pd.DataFrame(add_rows)], ignore_index=True)
    except Exception:
        return


def _patch_delivery_match_adapter(module):
    try:
        if getattr(module, "_address_reference_patch_v3", False):
            return

        original_prepare = module.prepare_manual_match_flexible
        def prepare_manual_match_with_address_refs(match_df):
            base = original_prepare(match_df)
            extra = _match_address_reference_by_batch(match_df)
            if base is None:
                return base
            base = base.copy()
            if "批次号" in base.columns:
                base["批次号"] = base["批次号"].apply(_normalize_batch_key)
            if extra is not None and not getattr(extra, "empty", True) and "批次号" in base.columns:
                base = base.merge(extra, on="批次号", how="left")
            return _ensure_columns(base, MATCH_REFERENCE_COLS)

        original_apply = module.apply_manual_match_to_cleaned_batches_flexible
        def apply_manual_match_with_address_refs(cleaned_batches, match_df):
            out = original_apply(cleaned_batches, match_df)
            return _copy_match_refs_to_rows(out, match_df)

        original_finalize = module._finalize_zip_audit_sheet
        def finalize_zip_audit_with_address_refs(df):
            out = original_finalize(df)
            return _reorder_zip_audit_columns(out)

        module.prepare_manual_match_flexible = prepare_manual_match_with_address_refs
        module.apply_manual_match_to_cleaned_batches_flexible = apply_manual_match_with_address_refs
        module._finalize_zip_audit_sheet = finalize_zip_audit_with_address_refs
        module._address_reference_patch_v3 = True
    except Exception:
        return


def _patch_after_delivery_bootstrap(delivery_workflow_module):
    try:
        import tool_common
        import delivery_match_adapter
        _patch_delivery_match_adapter(delivery_match_adapter)

        try:
            delivery_workflow_module.prepare_manual_match = delivery_match_adapter.prepare_manual_match_flexible
            delivery_workflow_module.apply_manual_match_to_cleaned_batches = delivery_match_adapter.apply_manual_match_to_cleaned_batches_flexible
        except Exception:
            pass

        base_stage1 = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches
        if not getattr(base_stage1, "_source_address_reference_patch_v3", False):
            def process_stage1_with_source_address(*args, **kwargs):
                result = base_stage1(*args, **kwargs)
                if isinstance(result, tuple) and len(result) == 4:
                    cleaned_batches, invalid_detail, zip_audit_df, raw_detail = result
                    cleaned_batches = _enrich_cleaned_with_source_address(cleaned_batches, raw_detail)
                    if cleaned_batches is not None and not getattr(cleaned_batches, "empty", True) and "目的地邮编待补充" in cleaned_batches.columns:
                        zip_audit_df = cleaned_batches[tool_common.normalize_boolean_series(cleaned_batches["目的地邮编待补充"])].copy()
                        zip_audit_df = _reorder_zip_audit_columns(zip_audit_df)
                    return cleaned_batches, invalid_detail, zip_audit_df, raw_detail
                return result

            process_stage1_with_source_address._source_address_reference_patch_v3 = True
            delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = process_stage1_with_source_address

        base_build = delivery_match_adapter.build_split_stage2_report
        if not getattr(base_build, "_source_address_reference_patch_v3", False):
            def build_split_stage2_report_with_source_address(*args, **kwargs):
                report = base_build(*args, **kwargs)
                if isinstance(report, dict):
                    match_df = _stage2_match_df_from_call(args, kwargs)
                    matched = report.get("派送二_匹配后合并数据")
                    matched = _copy_match_refs_to_rows(matched, match_df)
                    if matched is not None:
                        report["派送二_匹配后合并数据"] = matched
                    if "邮编异常审核" in report:
                        audit = report.get("邮编异常审核")
                        audit = _merge_address_from_matched(audit, matched)
                        report["邮编异常审核"] = _reorder_zip_audit_columns(audit)
                return report

            build_split_stage2_report_with_source_address._source_address_reference_patch_v3 = True
            delivery_match_adapter.build_split_stage2_report = build_split_stage2_report_with_source_address
    except Exception:
        return


def _patch_delivery_runtime(module):
    try:
        bootstrap = getattr(module, "bootstrap", None)
        if bootstrap is None or getattr(bootstrap, "_source_address_reference_patch_v3", False):
            return

        def bootstrap_with_source_address(delivery_workflow_module):
            result = bootstrap(delivery_workflow_module)
            _patch_after_delivery_bootstrap(delivery_workflow_module)
            return result

        bootstrap_with_source_address._source_address_reference_patch_v3 = True
        module.bootstrap = bootstrap_with_source_address
    except Exception:
        return


def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    global _PATCHING
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    if _PATCHING:
        return module
    try:
        _PATCHING = True
        root_name = name.split(".", 1)[0]
        target = module
        if root_name == "delivery_reference":
            _patch_delivery_reference(target)
        elif root_name == "delivery_match_adapter":
            _patch_delivery_match_adapter(target)
        elif root_name == "delivery_runtime":
            _patch_delivery_runtime(target)
    except Exception:
        pass
    finally:
        _PATCHING = False
    return module


builtins.__import__ = _patched_import
