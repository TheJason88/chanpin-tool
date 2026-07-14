"""
Runtime extension for chanpin-tool.

This file is imported automatically by Python before app.py.  It only installs
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
]

ADDRESS_SOURCE_COLS = [
    "源文件地址", "源文件地址参考", "地址", "目的地", "修正后目的地", "目的地址", "派送地址", "收货地址", "Destination", "转仓地址",
]
CITY_SOURCE_COLS = ["源文件城市", "城市", "目的城市", "City", "CITY"]
STATE_SOURCE_COLS = ["源文件省州", "省/州", "省州", "省份", "州", "目的州", "State", "STATE", "DestinationState"]
ZIP_SOURCE_COLS = ["源文件邮编", "邮编", "目的地邮编", "标准邮编", "ZIP", "Zip", "zipcode", "ZipCode", "PostalCode", "Postal Code"]
REFERENCE_COLS = ["源文件地址参考", "源文件地址", "源文件城市", "源文件省州", "源文件邮编"]


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
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


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


def _combine_from_columns(df, columns: List[str]) -> str:
    if df is None or getattr(df, "empty", True):
        return ""
    values: List[Any] = []
    for col in columns:
        if col in df.columns:
            values.extend(df[col].tolist())
    return _combine(values)


def _address_ref_from_group(group) -> Dict[str, str]:
    address = _combine_from_columns(group, ADDRESS_SOURCE_COLS)
    city = _combine_from_columns(group, CITY_SOURCE_COLS)
    state = _combine_from_columns(group, STATE_SOURCE_COLS)
    zip_code = _combine_from_columns(group, ZIP_SOURCE_COLS)
    parts = [address, city, state, zip_code]
    return {
        "源文件地址": address,
        "源文件城市": city,
        "源文件省州": state,
        "源文件邮编": zip_code,
        "源文件地址参考": _combine(parts),
    }


def _ensure_address_reference_columns(df):
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    for col in REFERENCE_COLS:
        if col not in out.columns:
            out[col] = ""
    for idx, _ in out.iterrows():
        row_df = out.loc[[idx]]
        ref = _address_ref_from_group(row_df)
        for col in REFERENCE_COLS:
            if _is_blank(out.at[idx, col]) and ref.get(col):
                out.at[idx, col] = ref[col]
    return out


def _source_address_by_batch(raw_detail):
    if raw_detail is None or getattr(raw_detail, "empty", True) or "批次号" not in raw_detail.columns:
        return {}
    raw = raw_detail.copy()
    raw["__批次匹配Key"] = raw["批次号"].apply(_normalize_batch_key)
    result = {}
    for key, group in raw.groupby("__批次匹配Key", dropna=False):
        if not key:
            continue
        result[str(key)] = _address_ref_from_group(group)
    return result


def _merge_ref_dicts(refs: Iterable[Dict[str, str]]) -> Dict[str, str]:
    merged = {}
    for col in REFERENCE_COLS:
        merged[col] = _combine(ref.get(col, "") for ref in refs)
    return merged


def _enrich_cleaned_with_source_address(cleaned_batches, raw_detail):
    if cleaned_batches is None or getattr(cleaned_batches, "empty", True):
        return cleaned_batches
    out = _ensure_address_reference_columns(cleaned_batches)
    batch_ref = _source_address_by_batch(raw_detail)
    if not batch_ref:
        return out
    for idx, row in out.iterrows():
        batch_ids = _split_batch_ids(row.get("批次号集合", row.get("批次号", "")))
        refs = [batch_ref[_normalize_batch_key(batch)] for batch in batch_ids if _normalize_batch_key(batch) in batch_ref]
        if not refs:
            continue
        merged = _merge_ref_dicts(refs)
        for col in REFERENCE_COLS:
            if merged.get(col):
                out.at[idx, col] = merged[col]
    return out


def _merge_address_from_matched(audit, matched):
    if audit is None or getattr(audit, "empty", True):
        return audit
    out = _ensure_address_reference_columns(audit)
    if matched is None or getattr(matched, "empty", True):
        return out
    matched = _ensure_address_reference_columns(matched)

    key_cols = [c for c in ["分析批次ID", "批次号集合"] if c in out.columns and c in matched.columns]
    if not key_cols:
        return out

    for key_col in key_cols:
        lookup = matched.drop_duplicates(subset=[key_col], keep="first").set_index(key_col, drop=False)
        for idx, row in out.iterrows():
            key = row.get(key_col, "")
            if _is_blank(key) or key not in lookup.index:
                continue
            source = lookup.loc[key]
            for col in REFERENCE_COLS:
                if _is_blank(out.at[idx, col]) and col in source.index and not _is_blank(source.get(col, "")):
                    out.at[idx, col] = source.get(col, "")
    return out


def _reorder_zip_audit_columns(df):
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    desired_front = [
        "分析批次ID", "仓库", "标准运输类型", "派送方式", "车次号", "批次号集合",
        "源文件地址参考", "源文件地址", "源文件城市", "源文件省州", "源文件邮编",
        "补充标准邮编", "补充目的州",
    ]
    cols = [c for c in desired_front if c in out.columns] + [c for c in out.columns if c not in desired_front]
    return out[cols]


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


def _patch_after_delivery_bootstrap(delivery_workflow_module):
    try:
        import pandas as pd  # noqa: F401
        import tool_common
        import delivery_match_adapter

        base_stage1 = delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches
        if not getattr(base_stage1, "_source_address_reference_patch", False):
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

            process_stage1_with_source_address._source_address_reference_patch = True
            delivery_workflow_module.process_stage1_raw_files_to_cleaned_batches = process_stage1_with_source_address

        base_build = delivery_match_adapter.build_split_stage2_report
        if not getattr(base_build, "_source_address_reference_patch", False):
            def build_split_stage2_report_with_source_address(*args, **kwargs):
                report = base_build(*args, **kwargs)
                if isinstance(report, dict) and "邮编异常审核" in report:
                    matched = report.get("派送二_匹配后合并数据")
                    audit = report.get("邮编异常审核")
                    audit = _merge_address_from_matched(audit, matched)
                    report["邮编异常审核"] = _reorder_zip_audit_columns(audit)
                return report

            build_split_stage2_report_with_source_address._source_address_reference_patch = True
            delivery_match_adapter.build_split_stage2_report = build_split_stage2_report_with_source_address
    except Exception:
        return


def _patch_delivery_runtime(module):
    try:
        bootstrap = getattr(module, "bootstrap", None)
        if bootstrap is None or getattr(bootstrap, "_source_address_reference_patch", False):
            return

        def bootstrap_with_source_address(delivery_workflow_module):
            result = bootstrap(delivery_workflow_module)
            _patch_after_delivery_bootstrap(delivery_workflow_module)
            return result

        bootstrap_with_source_address._source_address_reference_patch = True
        module.bootstrap = bootstrap_with_source_address
    except Exception:
        return


_ORIGINAL_IMPORT = builtins.__import__


def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        root_name = name.split(".", 1)[0]
        target = module
        if root_name == "delivery_reference":
            _patch_delivery_reference(target)
        elif root_name == "delivery_runtime":
            _patch_delivery_runtime(target)
    except Exception:
        pass
    return module


builtins.__import__ = _patched_import
