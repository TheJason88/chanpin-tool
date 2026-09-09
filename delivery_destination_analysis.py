"""FBA destination / method / carrier analysis, separate from legacy lane reports."""
import json
import re

import numpy as np
import pandas as pd

import processors
import tool_common


TRIP_CONTEXT = "车次批次目的地上下文"
METHOD = "仓点派送方式"
METHODS = ["整车大车卡板", "整车大车地板", "整车小车", "多卸", "LTL", "待确认"]
KEYS = ["仓库", "统计周期", "FBA仓点"]
SUMMARY_COLUMNS = KEYS + [
    "总出库体积", "派送方式货量", "派送方式占比", "平均每方成本",
    "有效成本批次数", "有效成本方数", "成本覆盖率",
]
METHOD_COLUMNS = KEYS + [
    "派送方式", "总出库体积", "占仓点货量比例", "平均整车成本", "平均每方成本",
    "有效整车样本数", "有效成本批次数", "有效成本方数", "成本覆盖率",
    "供应商货量", "供应商使用比例", "供应商平均整车成本", "供应商平均每方成本",
    "供应商有效成本方数", "供应商成本覆盖率", "原始成本缺失回退批次数",
]


def _text(value):
    return "" if processors.is_blank(value) else re.sub(r"\s+", " ", str(value)).strip()


def _series(df, name, default=""):
    return df.get(name, pd.Series(default, index=df.index))


def _number(df, name):
    return pd.to_numeric(_series(df, name, np.nan), errors="coerce").replace([np.inf, -np.inf], np.nan)


def destination(row):
    """Use matched station codes, never interpret a ZIP as an FBA warehouse."""
    kind = _text(row.get("批次目的地类型")) or _text(row.get("主产品类型"))
    if kind == "FBA":
        code = _text(row.get("FBA仓点代码集合")) or _text(row.get("批次目的仓点"))
    elif kind in {"FBX", "FBX平台仓"}:
        kind = "FBX平台仓"
        code = _text(row.get("FBX代码集合")) or _text(row.get("批次目的仓点"))
    else:
        code = _text(row.get("批次目的仓点")) or _text(row.get("调入仓库"))
    if not code or any(token in code for token in [",", "，", ";", "；", "|"]):
        return "", ""
    if any(token in code for token in ["未知", "待补", "未匹配"]):
        return "", ""
    return kind, code.upper()


def annotate_trip_context(df):
    """Persist destination evidence before FBA filtering, without changing legacy fields.

    The per-batch map survives a stage-one FBA-only export. Re-matching a batch
    replaces that batch's old destination; unseen stops remain as evidence.
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    identities = out.apply(destination, axis=1)
    out["_目的地"] = identities.map(lambda value: "||".join(value) if value[1] else "")
    out["_批次键"] = out.apply(lambda row: _text(row.get("批次号")) or _text(row.get("批次号集合")) or _text(row.get("分析批次ID")), axis=1)
    out["_车次键"] = _series(out, "仓库").map(_text).str.upper() + "||" + _series(out, "车次号").map(_text)
    out[TRIP_CONTEXT] = _series(out, TRIP_CONTEXT)
    out["车次目的地数"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out["车次目的地可确认"] = False
    has_trip = _series(out, "车次号").map(_text).ne("")
    if "是否有真实车次号" in out:
        has_trip &= out["是否有真实车次号"].apply(processors._whole_truck_truthy)
    ftl = _series(out, "标准运输类型").astype(str).str.upper().eq("FTL")
    for _, group in out[ftl & has_trip].groupby("_车次键", sort=False):
        context = {}
        for value in group[TRIP_CONTEXT]:
            try:
                stored = json.loads(_text(value) or "{}")
                if isinstance(stored, dict):
                    context.update({str(k): str(v) for k, v in stored.items() if isinstance(v, str)})
            except (ValueError, TypeError):
                pass
        for _, row in group.iterrows():
            if row["_批次键"]:
                context[row["_批次键"]] = row["_目的地"]
        declared = _number(group, "整车批次数").max()
        count = max(len(context), int(declared) if pd.notna(declared) else len(group))
        destinations = set(v for v in context.values() if v)
        known = bool(context) and all(context.values()) and len(context) >= count
        out.loc[group.index, TRIP_CONTEXT] = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        out.loc[group.index, "车次目的地数"] = len(destinations)
        out.loc[group.index, "车次目的地可确认"] = known
    out[METHOD] = "待确认"
    out.loc[_series(out, "标准运输类型").astype(str).str.upper().eq("LTL"), METHOD] = "LTL"
    out.loc[ftl & has_trip & out["车次目的地数"].ge(2).fillna(False), METHOD] = "多卸"
    single = ftl & has_trip & out["车次目的地可确认"] & out["车次目的地数"].eq(1).fillna(False)
    vehicle = _series(out, "车型标准值").astype(str)
    loading = _series(out, "装车类型标准值").astype(str)
    out.loc[single & vehicle.str.contains("小车|26", regex=True), METHOD] = "整车小车"
    large = vehicle.str.contains("大车|53", regex=True)
    out.loc[single & large & loading.eq("卡板"), METHOD] = "整车大车卡板"
    out.loc[single & large & loading.eq("地板"), METHOD] = "整车大车地板"
    # The shared minimum-volume rule applies to the complete trip, not each stop.
    sample_source = out.copy()
    sample_source["出库体积"] = _number(out, "整车出库体积").fillna(_number(out, "出库体积"))
    out["是否达到普通派送均值门槛"] = ~ftl | out.index.isin(processors.regular_delivery_average_sample_rows(sample_source).index)
    out = out.drop(columns=["_目的地", "_批次键", "_车次键"])
    last = "同车次备注集合" if "同车次备注集合" in out else "备注"
    return out[[col for col in out if col != last] + ([last] if last in out else [])]


def _prepare(df):
    out = annotate_trip_context(df)
    if out.empty:
        return out
    identities = out.apply(destination, axis=1)
    out["FBA仓点"] = identities.map(lambda value: value[1] if value[0] == "FBA" else "")
    out["仓库"] = _series(out, "仓库").map(_text).str.upper()
    out["统计周期"] = _series(out, "统计周期", "未知周期").fillna("未知周期")
    out["_方数"] = _number(out, "出库体积")
    out["_运营成本"] = _number(out, "派送成本")
    base = _number(out, tool_common.BASE_DELIVERY_COST_COLUMN)
    out["_承运成本"] = base.fillna(out["_运营成本"])
    out["_成本回退"] = base.isna()
    # Preserve the original positive-base-cost gate for operational prices.
    gate = base if tool_common.BASE_DELIVERY_COST_COLUMN in out else out["_运营成本"]
    out["_有效运营成本"] = gate.gt(0) & out["_运营成本"].gt(0) & out["是否达到普通派送均值门槛"]
    out["_有效承运成本"] = out["_承运成本"].gt(0) & out["是否达到普通派送均值门槛"]
    out["_供应商"] = _series(out, "派送卡车").map(_text)
    out["_供应商键"] = out["_供应商"].str.casefold()
    return out[out["FBA仓点"].ne("") & out["_方数"].gt(0)].copy()


def _cost_metrics(group, carrier=False):
    sample = group[group["_有效承运成本" if carrier else "_有效运营成本"]]
    cost = "_承运成本" if carrier else "_运营成本"
    volume = float(sample["_方数"].sum())
    return {
        "平均每方成本": sample[cost].div(sample["_方数"]).mean(),
        "有效成本批次数": len(sample), "有效成本方数": volume,
        "成本覆盖率": f"{volume / group['_方数'].sum():.2%}",
    }


def _truck_samples(group):
    rows = []
    if group.empty or group[METHOD].iloc[0] not in METHODS[:3]:
        return pd.DataFrame(columns=["运营成本", "承运成本", "供应商键"])
    for _, trip in group.groupby("车次号", sort=False):
        share = _number(trip, "批次车份额")
        context = json.loads(trip[TRIP_CONTEXT].iloc[0] or "{}")
        # A partial trip (including a trip split across reporting periods) is
        # still volume, but cannot supply an observed whole-truck price.
        if len(context) != len(trip) or share.isna().any() or abs(share.sum() - 1) > 1e-6:
            continue
        supplier_keys = trip["_供应商键"].unique()
        supplier = supplier_keys[0] if len(supplier_keys) == 1 else ""
        rows.append({
            "运营成本": trip["_运营成本"].sum() if trip["_有效运营成本"].all() else np.nan,
            "承运成本": trip["_承运成本"].sum() if trip["_有效承运成本"].all() else np.nan,
            "供应商键": supplier,
        })
    return pd.DataFrame(rows, columns=["运营成本", "承运成本", "供应商键"])


def _supplier_cells(group, trucks):
    entries = []
    for key, part in group.groupby("_供应商键", sort=False):
        display = part["_供应商"].iloc[0] if key else "供应商未知/冲突"
        stats = _cost_metrics(part, carrier=True)
        average = trucks.loc[trucks["供应商键"].eq(key), "承运成本"].mean() if key else np.nan
        entries.append((key, display, float(part["_方数"].sum()), stats, average))
    entries.sort(key=lambda item: (-item[2], item[0]))
    def price(value):
        return "无有效样本" if pd.isna(value) else f"${value:.2f}"
    total = group["_方数"].sum()
    return {
        "供应商货量": "；".join(f"{name} {volume:g}方" for _, name, volume, _, _ in entries),
        "供应商使用比例": "；".join(f"{name} {volume / total:.2%}" for _, name, volume, _, _ in entries),
        "供应商平均整车成本": "；".join(f"{name} {price(truck)}" for _, name, _, _, truck in entries) if group[METHOD].iloc[0] in METHODS[:3] else "",
        "供应商平均每方成本": "；".join(f"{name} {price(stats['平均每方成本'])}/方" if pd.notna(stats['平均每方成本']) else f"{name} 无有效样本" for _, name, _, stats, _ in entries),
        "供应商有效成本方数": "；".join(f"{name} {stats['有效成本方数']:g}方" for _, name, _, stats, _ in entries),
        "供应商成本覆盖率": "；".join(f"{name} {stats['成本覆盖率']}" for _, name, _, stats, _ in entries),
        "原始成本缺失回退批次数": int((group["_成本回退"] & group["_有效承运成本"]).sum()),
    }


def build_fba_destination_reports(matched):
    source = _prepare(matched)
    if source.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS), pd.DataFrame(columns=METHOD_COLUMNS)
    summaries, details = [], []
    for keys, station in source.groupby(KEYS, sort=True):
        identity = dict(zip(KEYS, keys))
        total = float(station["_方数"].sum())
        volumes = station.groupby(METHOD)["_方数"].sum().reindex(METHODS).dropna()
        summaries.append({
            **identity, "总出库体积": total, **_cost_metrics(station),
            "派送方式货量": "；".join(f"{method} {volume:g}方" for method, volume in volumes.items()),
            "派送方式占比": "；".join(f"{method} {volume / total:.2%}" for method, volume in volumes.items()),
        })
        for method in volumes.index:
            part = station[station[METHOD].eq(method)]
            trucks = _truck_samples(part)
            details.append({
                **identity, "派送方式": method, "总出库体积": float(part["_方数"].sum()),
                "占仓点货量比例": f"{part['_方数'].sum() / total:.2%}",
                "平均整车成本": trucks["运营成本"].mean(),
                "有效整车样本数": int(trucks["运营成本"].notna().sum()),
                **_cost_metrics(part), **_supplier_cells(part, trucks),
            })
    summary = pd.DataFrame(summaries, columns=SUMMARY_COLUMNS).sort_values(
        ["仓库", "统计周期", "总出库体积", "FBA仓点"], ascending=[True, True, False, True], kind="stable",
    ).reset_index(drop=True)
    order = summary[KEYS].assign(_仓点顺序=range(len(summary)))
    detail = pd.DataFrame(details, columns=METHOD_COLUMNS).merge(order, on=KEYS, validate="many_to_one")
    detail["_方式顺序"] = detail["派送方式"].map(dict(zip(METHODS, range(len(METHODS)))))
    detail = detail.sort_values(["_仓点顺序", "_方式顺序"], kind="stable").reset_index(drop=True)
    return summary, detail[METHOD_COLUMNS]


def build_station_timing_report(matched):
    """Only recognized FBA/FBX destinations; keep existing valid timing rules."""
    import delivery_workflow

    columns = ["仓库", "统计周期", "目的地类型", "平台名称", "目的仓点", "平均派送时效", "P80派送时效", "有效时效批次数", "有效时效方数", "无效时效批次数"]
    if matched is None or matched.empty:
        return pd.DataFrame(columns=columns)
    source = matched.copy()
    identities = source.apply(destination, axis=1)
    source["目的地类型"] = identities.map(lambda value: value[0])
    source["目的仓点"] = identities.map(lambda value: value[1])
    source["平台名称"] = _series(source, "平台名称").fillna("")
    source = source[source["目的地类型"].isin(["FBA", "FBX平台仓"]) & source["目的仓点"].ne("")]
    rows = []
    group_cols = columns[:5]
    for keys, group in source.groupby(group_cols, dropna=False, sort=True):
        sample = delivery_workflow.timing_sample_rows(group)
        rows.append({
            **dict(zip(group_cols, keys)),
            "平均派送时效": delivery_workflow.volume_weighted_average(sample),
            "P80派送时效": delivery_workflow.volume_weighted_p80(sample),
            "有效时效批次数": len(sample), "有效时效方数": _number(sample, "出库体积").sum(),
            "无效时效批次数": len(group) - len(sample),
        })
    return pd.DataFrame(rows, columns=columns)
