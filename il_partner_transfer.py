"""Resolve a partner transfer's unloading endpoint, independently of final SO addresses."""
import re

import pandas as pd

import processors
import tool_common as common


AUDIT_COLUMN = "IL合作仓判断"


def text(value):
    return common._clean_transfer_text(value)


def batch_key(row):
    return text(row.get("批次号", "")) or text(row.get("批次号集合", ""))


def address_is_partner(row):
    # Address cells only: order remarks can mention earlier stops and instructions.
    locality = " ".join(text(row.get(col, "")) for col in ["城市", "省/州", "目的州", "邮编", "标准邮编"])
    return any(common.is_il_partner_text(text(row.get(col, "")) + " " + locality)
               for col in ["地址", "标准地址", "原地址信息"] if text(row.get(col, "")))


def other_stop_matches(records, remarks):
    """Only resolve the named opposite stop in an explicitly two-stop trip."""
    if not records:
        return False
    if "安克" in remarks or re.search(r"\bANKER\b", remarks, re.I):
        return all(any("安克" in text(row.get(col, "")) or re.search(r"\bANKER\b", text(row.get(col, "")), re.I)
                       for col in ["备注", "内部备注", "原地址信息", "仓库代码"])
                   for row in records)
    if re.search(r"(?<![A-Z])MI(?![A-Z])", remarks.upper()):
        return all(text(row.get("省/州", row.get("目的州", ""))).upper() in {"MI", "MICHIGAN", "密歇根州"}
                   for row in records)
    return False


def resolve_partner_endpoints(cleaned, match_df):
    if cleaned is None or cleaned.empty:
        return cleaned
    out = cleaned.copy()
    out[AUDIT_COLUMN] = out.get(AUDIT_COLUMN, pd.Series("", index=out.index)).fillna("").astype(object)
    records = {}
    if match_df is not None and not match_df.empty:
        normalized = processors.normalize_columns(match_df)
        for _, row in normalized.iterrows():
            batch = batch_key(row)
            if batch:
                records.setdefault(batch, []).append(row)
    ambiguous = set()
    for idx, row in out.iterrows():
        if processors.standardize_warehouse(text(row.get("仓库", ""))) != "LA":
            continue
        remark = text(row.get("备注", ""))
        if common.il_partner_remark_has_multiple_stops(remark):
            ambiguous.add(idx)
            out.at[idx, AUDIT_COLUMN] = "待核对：多卸备注未绑定具体批次"
        elif common.is_il_partner_row(row):
            out.at[idx, AUDIT_COLUMN] = "确认：本批次目的地或单卸合作仓备注"

    def assign(indexes, message):
        for col in ["调入仓库", "调拨目标仓代码"]:
            if col not in out:
                out[col] = ""
            out[col] = out[col].astype(object)
        out.loc[indexes, "调入仓库"] = "IL合作仓"
        out.loc[indexes, "调拨目标仓代码"] = "IL"
        out.loc[indexes, AUDIT_COLUMN] = "确认：" + message

    trip = out.get("车次号", pd.Series("", index=out.index)).map(text)
    warehouse = out.get("仓库", pd.Series("", index=out.index)).map(text)
    for _, indexes in out.groupby([warehouse, trip], sort=False).groups.items():
        group = out.loc[indexes]
        candidates = [idx for idx in indexes if idx in ambiguous]
        if not candidates:
            continue
        # No inference with missing trip, unknown/missing batch, or three-plus batches.
        batch_ids = group.apply(batch_key, axis=1)
        if not trip.loc[indexes].iloc[0] or len(group) != 2 or len(candidates) != 2 or batch_ids.nunique() != 2 or batch_ids.eq("").any():
            continue
        if any(text(row.get("调入仓库", "")) and not common.is_il_partner_text(row.get("调入仓库", ""))
               for _, row in group.iterrows()):
            continue
        remarks = " ".join(text(v) for v in group.get("备注", []))
        # Require an explicit two-stop list, not an arbitrary multi-unload reference.
        individual = [text(out.at[idx, "备注"]) for idx in candidates]
        if any(len(re.split(r"[+＋]", s)) != 2 for s in individual):
            continue
        if "安克" in remarks and re.search(r"(?<![A-Z])MI(?![A-Z])", remarks.upper()):
            continue
        hits, opposite = [], []
        for idx, row in group.iterrows():
            matched = records.get(batch_key(row), [])
            explicit_partner = any(common.is_il_partner_text(row.get(col, "")) for col in
                                   ["调入仓库", "实际目的地", "修正后目的地", "目的地", "标准地址", "批次目的仓点"])
            if explicit_partner or any(address_is_partner(rec) for rec in matched):
                hits.append(idx)
            elif other_stop_matches(matched, remarks):
                opposite.append(idx)
        if len(hits) == 1:
            selected = hits[0]
            assign([selected], "两卸中本批次命中合作仓地址/目的地")
            out.loc[[idx for idx in indexes if idx != selected], AUDIT_COLUMN] = "排除：两卸另一批已确认卸合作仓"
        elif not hits and len(opposite) == 1:
            selected = [idx for idx in indexes if idx not in opposite][0]
            assign([selected], "两卸另一批明确匹配安克/MI，剩余批次卸合作仓")
            out.loc[opposite, AUDIT_COLUMN] = "排除：运单证据明确匹配另一卸货点"
        elif len(hits) > 1:
            out.loc[candidates, AUDIT_COLUMN] = "待核对：两卸两个批次均命中合作仓地址"
    return out
