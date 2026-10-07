"""Compact presentation of existing stage-two results; no cost/time recalculation."""
import re

import pandas as pd

import processors


BUSINESS_SHEETS = (
    "派送总览", "FBA仓点总览", "FBA派送方式分析", "FBX仓点总览",
    "分类价格参考", "调拨数据", "干线数据", "黄金标准数据", "满载率与地板率",
)
AUDIT_SHEETS = ("派送二_匹配后批次数据", "派送二_车次汇总核对", "邮编异常审核")
PLATFORM_ALIASES = {"运去哪仓": "运去哪"}


def _frame(reports, name):
    value = reports.get(name)
    return value.copy(deep=True) if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _clean_label(value):
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _canonical_platform(value):
    label = _clean_label(value)
    return PLATFORM_ALIASES.get(label, label)


def _normalize_keys(frame, keys):
    out = frame.copy()
    for key in keys:
        cleaner = _canonical_platform if key == "平台名称" else _clean_label
        out[key] = out[key].apply(cleaner)
    return out



def _is_truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是", "有", "ftl"}


def _is_real_trip(row):
    trip_no = _clean_label(row.get("车次号", ""))
    if not trip_no:
        return False
    if "是否有真实车次号" in row.index and not _is_truthy(row.get("是否有真实车次号")):
        return False
    return True


def _is_ftl_trip(row):
    if _is_truthy(row.get("是否FTL发车")):
        return True
    transport_type = _clean_label(row.get("标准运输类型", "")).upper()
    return "FTL" in transport_type or "整车" in transport_type


def _loading_group(row):
    vehicle = _clean_label(row.get("车型标准值", ""))
    loading = _clean_label(row.get("装车类型标准值", ""))
    if "26" in vehicle or "小车" in vehicle:
        return "非大车"
    is_large_truck = "53" in vehicle or "大车" in vehicle
    if is_large_truck and "地板" in loading:
        return "大车地板"
    if is_large_truck and "卡板" in loading:
        return "大车卡板"
    return "无法判定"


def _build_loading_metrics_report(reports):
    """
    Build load-factor and floor-rate metrics at warehouse/period grain.

    A truck is counted once by real trip number. For FBA/FBX filtered exports,
    the internal full matched batch set is used because a truck's capacity
    belongs to the physical truck, not to a destination-type slice.
    """
    data = _frame(reports, "__满载率全量批次")
    if data.empty:
        data = _frame(reports, "派送二_匹配后批次数据")
    if data.empty:
        return pd.DataFrame()

    data = data.copy()
    if "仓库" not in data.columns:
        data["仓库"] = "未识别仓库"
    if "统计周期" not in data.columns:
        data["统计周期"] = "未识别周期"
    if "车次号" not in data.columns:
        return pd.DataFrame()

    data["_出库方数"] = pd.to_numeric(data.get("出库体积", 0), errors="coerce")
    data["_真实FTL车次"] = data.apply(lambda row: _is_real_trip(row) and _is_ftl_trip(row), axis=1)
    data["_装车类型标准值"] = data.apply(_loading_group, axis=1)
    data = data[data["_真实FTL车次"]].copy()
    data = data[data["_装车类型标准值"] != "非大车"].copy()
    if data.empty:
        return pd.DataFrame()

    trip_rows = []
    trip_keys = ["仓库", "统计周期", "车次号"]
    for key_values, group in data.groupby(trip_keys, dropna=False, sort=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        warehouse, period, trip_no = (_clean_label(value) for value in key_values)
        mode_values = {
            value for value in group["_装车类型标准值"].tolist()
            if value
        } if "_装车类型标准值" in group else set()
        mode = mode_values.pop() if len(mode_values) == 1 else "无法判定"
        trip_volume = group["_出库方数"].sum(min_count=1)
        volume_is_valid = pd.notna(trip_volume) and float(trip_volume) > 0
        capacity = {"大车卡板": 60.0, "大车地板": 90.0}.get(mode)
        single_trip_rate = (
            min(float(trip_volume) / capacity, 1.0)
            if volume_is_valid and capacity
            else pd.NA
        )
        trip_rows.append({
            "仓库": warehouse or "未识别仓库",
            "统计周期": period or "未识别周期",
            "车次号": trip_no,
            "_装车类型": mode,
            "_出库方数": float(trip_volume) if pd.notna(trip_volume) else 0.0,
            "_有有效方数": volume_is_valid,
            "_单车满载率": single_trip_rate,
        })

    trips = pd.DataFrame(trip_rows)
    if trips.empty:
        return pd.DataFrame()

    rows = []
    for (warehouse, period), group in trips.groupby(["仓库", "统计周期"], dropna=False, sort=False):
        known = group[group["_装车类型"].isin(["大车卡板", "大车地板"])].copy()
        valid_load = known[known["_有有效方数"] & known["_单车满载率"].notna()].copy()
        pallet = valid_load[valid_load["_装车类型"].eq("大车卡板")]
        floor = valid_load[valid_load["_装车类型"].eq("大车地板")]
        pallet_count = int((known["_装车类型"] == "大车卡板").sum())
        floor_count = int((known["_装车类型"] == "大车地板").sum())
        denominator = pallet_count + floor_count
        effective_volume = float(valid_load["_出库方数"].sum())
        pallet_load_rate = float(pallet["_单车满载率"].mean()) if not pallet.empty else pd.NA
        floor_load_rate = float(floor["_单车满载率"].mean()) if not floor.empty else pd.NA
        overall_load_rate = float(valid_load["_单车满载率"].mean()) if not valid_load.empty else pd.NA
        floor_flags = known["_装车类型"].map({"大车地板": 1.0, "大车卡板": 0.0}).dropna()
        floor_rate_average = float(floor_flags.mean()) if not floor_flags.empty else pd.NA
        rows.append({
            "仓库": warehouse,
            "统计周期": period,
            "统计口径": "真实FTL大车车次；FBA/FBX筛选时仍按全量物理车次计算",
            "大车整车车次数": int(len(group)),
            "大车卡板车次数": pallet_count,
            "大车地板车次数": floor_count,
            "无法判定装车类型车次数": int(len(group) - len(known)),
            "无有效出库方数车次数": int((known["_有有效方数"] == False).sum()),
            "大车卡板标准容量": 60.0,
            "大车卡板有效出库方数": float(pallet["_出库方数"].sum()),
            "大车卡板满载率加权车次": int(len(pallet)),
            "大车卡板满载率": pallet_load_rate,
            "大车卡板满载率平均值": pallet_load_rate,
            "大车卡板满载率P80": processors.safe_p80(pallet["_单车满载率"]),
            "大车卡板满载率P90": processors.safe_p90(pallet["_单车满载率"]),
            "大车地板标准容量": 90.0,
            "大车地板有效出库方数": float(floor["_出库方数"].sum()),
            "大车地板满载率加权车次": int(len(floor)),
            "大车地板满载率": floor_load_rate,
            "大车地板满载率平均值": floor_load_rate,
            "大车地板满载率P80": processors.safe_p80(floor["_单车满载率"]),
            "大车地板满载率P90": processors.safe_p90(floor["_单车满载率"]),
            "满载率有效实际出库方数": effective_volume,
            "满载率加权车次": int(len(valid_load)),
            "满载率": overall_load_rate,
            "满载率平均值": overall_load_rate,
            "满载率P80": processors.safe_p80(valid_load["_单车满载率"]),
            "满载率P90": processors.safe_p90(valid_load["_单车满载率"]),
            "地板率": floor_rate_average,
            "地板率平均值": floor_rate_average,
            "地板率P80": processors.safe_p80(floor_flags),
            "地板率P90": processors.safe_p90(floor_flags),
            "指标说明": (
                "先按车次计算单车满载率=min(实际出库方数÷车型容量,100%)，再按车次等权加权平均；"
                "大车卡板容量60 CBM，大车地板容量90 CBM；"
                "满载率统计同时给平均/P80/P90，单车样本按车次等权；"
                "地板率统计以已判定的大车车次为样本，地板=1、卡板=0，同时给平均/P80/P90。"
            ),
        })
    return pd.DataFrame(rows)


def _weighted_supplier_shares(group):
    weighted = {}
    denominator = 0.0
    fallbacks = []
    for _, row in group.iterrows():
        volume = pd.to_numeric(row.get("_排行货量"), errors="coerce")
        text = _clean_label(row.get("供应商货量占比"))
        if text:
            fallbacks.append(text)
        if pd.isna(volume) or float(volume) <= 0:
            continue
        denominator += float(volume)
        for part in re.split(r"[；;]", text):
            match = re.fullmatch(r"(.+?)\s+([0-9]+(?:\.[0-9]+)?)%", part.strip())
            if match:
                supplier = match.group(1).strip()
                weighted[supplier] = weighted.get(supplier, 0.0) + float(volume) * float(match.group(2)) / 100
    if denominator > 0 and weighted:
        ordered = sorted(weighted.items(), key=lambda item: (-item[1], item[0]))
        return "；".join(f"{supplier} {amount / denominator:.2%}" for supplier, amount in ordered)
    return "；".join(dict.fromkeys(fallbacks))


def _collapse_rank(rank, keys):
    """Consolidate identical/canonical station rows before assigning rank."""
    if rank.empty:
        return rank
    rank = _normalize_keys(rank, keys)
    rank["_排行货量"] = pd.to_numeric(rank["_排行货量"], errors="coerce")
    rows = []
    for key_values, group in rank.groupby(keys, dropna=False, sort=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        row = dict(zip(keys, key_values))
        row["_排行货量"] = group["_排行货量"].sum(min_count=1)
        if "供应商货量占比" in rank:
            row["供应商货量占比"] = _weighted_supplier_shares(group)
        rows.append(row)
    result = pd.DataFrame(rows)
    totals = result.groupby(keys[:2], dropna=False)["_排行货量"].transform("sum")
    result["货量占比"] = result["_排行货量"].div(totals.where(totals.gt(0)))
    result["货量排名"] = result.groupby(keys[:2], dropna=False)["_排行货量"].rank(
        method="first", ascending=False,
    ).astype("Int64")
    return result


def _collapse_price(price, keys):
    if price.empty:
        return price
    price = _normalize_keys(price, keys)
    value_cols = ["参考价有效方数", "参考价有效板数", "参考价总成本"]
    for col in value_cols:
        price[col] = pd.to_numeric(price[col], errors="coerce")
    result = price.groupby(keys, dropna=False, sort=False)[value_cols].sum(min_count=1).reset_index()
    result["每方参考价（总成本÷总方数）"] = result["参考价总成本"].div(
        result["参考价有效方数"].where(result["参考价有效方数"].gt(0))
    )
    return result


def _collapse_timing(timing, keys):
    if timing.empty:
        return timing
    timing = _normalize_keys(timing, keys)
    rows = []
    for key_values, group in timing.groupby(keys, dropna=False, sort=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        row = dict(zip(keys, key_values))
        weights = pd.to_numeric(group["有效时效方数"], errors="coerce")
        averages = pd.to_numeric(group["平均派送时效"], errors="coerce")
        valid = weights.gt(0) & averages.notna()
        row["平均派送时效"] = (
            (weights[valid] * averages[valid]).sum() / weights[valid].sum() if valid.any() else pd.NA
        )
        p80 = pd.to_numeric(group["P80派送时效"], errors="coerce").dropna()
        row["P80派送时效"] = p80.iloc[0] if len(p80) == 1 else (p80.max() if len(p80) else pd.NA)
        p90 = pd.to_numeric(group.get("P90派送时效", pd.Series(dtype=float)), errors="coerce").dropna()
        row["P90派送时效"] = p90.iloc[0] if len(p90) == 1 else (p90.max() if len(p90) else pd.NA)
        for col in ["有效时效批次数", "有效时效方数", "无效时效批次数"]:
            row[col] = pd.to_numeric(group[col], errors="coerce").sum(min_count=1)
        rows.append(row)
    return pd.DataFrame(rows)


def _join(frames, keys):
    """Join only at the declared grain; never multiply station rows or average P80s."""
    result = pd.DataFrame(columns=keys)
    for source in frames:
        if source.empty:
            continue
        part = _normalize_keys(source, keys)
        if part.duplicated(keys).any():
            raise ValueError(f"仓点汇总存在重复键，无法安全合并：{keys}")
        result = result.merge(part, on=keys, how="outer", validate="one_to_one")
    return result


def _station(reports, kind):
    fba = kind == "FBA"
    keys = ["仓库", "统计周期"] + (["FBA仓点"] if fba else ["平台名称", "FBX仓点"])
    code = keys[-1]
    rank = _frame(reports, "FBA货量排行" if fba else "FBX平台仓货量").rename(columns={
        "平台仓": "平台名称", "FBX代码": "FBX仓点", "出库体积": "_排行货量",
        "排名": "货量排名", "占比": "货量占比", "派送卡车使用比例": "供应商货量占比",
    })
    if not rank.empty:
        rank = rank[keys + [c for c in ["_排行货量", "货量排名", "货量占比", "供应商货量占比"] if c in rank]]
        rank = _collapse_rank(rank, keys)
    summary = _frame(reports, "FBA仓点分析") if fba else pd.DataFrame()
    summary = summary.rename(columns={"平均每方成本": "批次平均每方成本"})
    price = _frame(reports, "每方价格参考")
    if not price.empty:
        price = price[price["对象类型"].eq(kind)].rename(columns={
            "平台": "平台名称", "仓点代码": code, "总出库体积": "参考价有效方数",
            "总出库卡板数": "参考价有效板数", "总派送成本": "参考价总成本",
            "每方价格参考": "每方参考价（总成本÷总方数）",
        })
        price = price[keys + ["参考价有效方数", "参考价有效板数", "参考价总成本", "每方参考价（总成本÷总方数）"]]
        price = _collapse_price(price, keys)
    timing = _frame(reports, "派送时效")
    if not timing.empty:
        timing = timing[timing["目的地类型"].eq(kind)].rename(columns={"目的仓点": code})
        timing = timing[keys + [c for c in ["平均派送时效", "P80派送时效", "P90派送时效", "有效时效批次数", "有效时效方数", "无效时效批次数"] if c in timing]]
        timing = _collapse_timing(timing, keys)
    result = _join([summary, rank, price, timing], keys)
    if result.empty:
        return result
    rank_volume = result.pop("_排行货量") if "_排行货量" in result else pd.Series(float("nan"), index=result.index)
    if "总出库体积" not in result:
        result["总出库体积"] = rank_volume
    else:
        mismatch = result["总出库体积"].notna() & rank_volume.notna() & (result["总出库体积"] - rank_volume).abs().gt(0.01)
        if mismatch.any():
            result["排行口径货量"] = rank_volume  # Preserve any legacy grain difference explicitly.
        result["总出库体积"] = result["总出库体积"].fillna(rank_volume)
    preferred = keys[:2] + ["货量排名"] + keys[2:] + [
        "总出库体积", "货量占比", "派送方式货量", "派送方式占比", "供应商货量占比",
        "批次平均每方成本", "P80每方成本", "P90每方成本", "每方参考价（总成本÷总方数）",
        "平均派送时效", "P80派送时效", "P90派送时效",
    ]
    columns = [c for c in preferred if c in result] + [c for c in result if c not in preferred]
    result = result[columns]
    if result.duplicated(keys).any():
        raise ValueError(f"仓点总览归一后仍有重复键：{keys}")
    if not fba:
        result.insert(2, "记录类型", "平台仓")
    return result.sort_values(keys[:2] + ["总出库体积"] + keys[2:],
        ascending=[True, True, False] + [True] * len(keys[2:]), na_position="last", kind="stable").reset_index(drop=True)


def _append_fbx_unidentified_summary(reports, station):
    """Append one reconciling row for FBX volume without a platform-station code."""
    volume = _frame(reports, "货量")
    if volume.empty:
        return station
    totals = volume[
        volume["指标名称"].eq("FBA比FBX方数")
        & volume["维度值"].astype(str).str.strip().eq("FBX")
    ].copy()
    if totals.empty:
        return station
    totals["数值"] = pd.to_numeric(totals["数值"], errors="coerce")
    existing = (
        station.groupby(["仓库", "统计周期"], dropna=False)["总出库体积"].sum(min_count=1)
        if not station.empty else pd.Series(dtype=float)
    )
    rows = []
    for _, total_row in totals.iterrows():
        key = (_clean_label(total_row["仓库"]), _clean_label(total_row["统计周期"]))
        total = total_row["数值"]
        covered = existing.get(key, 0.0)
        residual = float(total) - float(covered) if pd.notna(total) else 0.0
        if residual <= 0.01:
            continue
        rows.append({
            "仓库": key[0], "统计周期": key[1], "记录类型": "非平台/未知目的地汇总",
            "平台名称": "非平台/未知", "FBX仓点": "商业、私人地址及未识别平台仓",
            "总出库体积": residual,
            "货量占比": residual / float(total) if float(total) > 0 else pd.NA,
            "数据说明": "与派送总览FBX总量的差额；完整批次在审核明细文件查看",
        })
    if not rows:
        return station
    return pd.concat([station, pd.DataFrame(rows)], ignore_index=True, sort=False)


def _move_station_dispatch(reports, stations):
    dispatch = _frame(reports, "发车量")
    if dispatch.empty:
        return set()
    dispatch = dispatch[dispatch["指标名称"].eq("目的仓点发车数")].copy()
    base_keys = ["仓库", "统计周期", "维度值"]
    if "平台名称" not in dispatch:
        dispatch["平台名称"] = ""
    dispatch = _normalize_keys(dispatch, base_keys + ["平台名称"])
    covered = set()
    for name, data in list(stations.items()):
        if data.empty:
            continue
        code = "FBA仓点" if name == "FBA仓点总览" else "FBX仓点"
        data = _normalize_keys(data, ["仓库", "统计周期", code] + (["平台名称"] if "平台名称" in data else []))
        extra = dispatch.rename(columns={"维度值": code, "数值": "FTL折算发车数"}).copy()
        match_keys = ["仓库", "统计周期", code]
        if name == "FBX仓点总览" and extra["平台名称"].ne("").any():
            match_keys.append("平台名称")
            extra = extra[extra["平台名称"].ne("")]
        else:
            extra = extra[extra["平台名称"].eq("")]
        keep = match_keys + [c for c in ["FTL折算发车数", "精确车份额"] if c in extra]
        extra = extra[keep].drop_duplicates(match_keys)
        merged = data.merge(extra, on=match_keys, how="left", validate="one_to_one")
        if "FTL折算发车数" in merged:
            for _, row in merged[merged["FTL折算发车数"].notna()].iterrows():
                covered.add((row["仓库"], row["统计周期"], row[code], _canonical_platform(row.get("平台名称", ""))))
        stations[name] = merged
    return covered


def _overview(reports, covered):
    rows = []
    for name in ["货量", "发车量"]:
        data = _frame(reports, name)
        if data.empty:
            continue
        if name == "货量":
            totals = data[data["指标名称"].eq("非LTL方数比LTL方数")]
            for (warehouse, period), group in totals.groupby(["仓库", "统计周期"], sort=False):
                rows.append({"仓库": warehouse, "统计周期": period, "类别": "货量", "指标": "总出库体积（CBM）",
                             "数值": group["数值"].sum(), "分布及占比": ""})
        if name == "发车量":
            moved = data.apply(lambda row: row["指标名称"] == "目的仓点发车数" and
                               (_clean_label(row["仓库"]), _clean_label(row["统计周期"]),
                                _clean_label(row["维度值"]), _canonical_platform(row.get("平台名称", ""))) in covered, axis=1)
            data = data.loc[~moved]
        for keys, group in data.groupby(["仓库", "统计周期", "指标名称", "维度类型"], sort=False, dropna=False):
            warehouse, period, metric, dimension = keys
            # All composition and distribution entries stay in a single cell.
            def entry(row):
                value = row.get("数值")
                amount = "无数据" if pd.isna(value) else f"{float(value):g}"
                ratio = row.get("占比")
                suffix = f"（{float(ratio):.2%}）" if pd.notna(ratio) else ""
                return f"{row['维度值']} {amount}{row.get('单位', '')}{suffix}"
            total = group.iloc[0]["数值"] if len(group) == 1 and metric == "总发车数" else pd.NA
            display_metric = {
                "LA干线货量": "LA线路识别总货量（含未成车批次）",
            }.get(metric, metric)
            rows.append({"仓库": warehouse, "统计周期": period, "类别": name,
                         "指标": display_metric, "数值": total, "分布及占比": "；".join(entry(row) for _, row in group.iterrows())})
    return pd.DataFrame(rows)


def _classification_prices(reports):
    data = _frame(reports, "分类型价格参考")
    if data.empty:
        return data
    # These two pairs are aliases assigned by the existing report builder.
    for alias, original in [("细分货量方数", "总出库体积"), ("整车价格", "平均整车价")]:
        if alias in data and original in data:
            left, right = data[alias], data[original]
            equal = left.eq(right) | (left.isna() & right.isna())
            # LTL's alias intentionally blanks the truck price.
            if alias == "整车价格":
                equal |= data["成本计算类型"].eq("LTL") & left.isna()
            if equal.all():
                data = data.drop(columns=alias)
    data = data.drop(columns=["指标名称", "排名", "目的地总出库体积"], errors="ignore")
    return data.rename(columns={
        "总出库体积": "有效成本方数", "总出库卡板数": "有效成本板数",
        "每方成本": "每方参考价（总成本÷总方数）", "每方平均价": "批次平均每方成本",
        "平均整车价": "平均整车价（单批次整车）", "P80整车价": "P80整车价（单批次整车）",
        "P90整车价": "P90整车价（单批次整车）",
    })


def _rules(reports):
    rows = [
        {"规则类别": "使用说明", "说明": "业务看板与审核明细合并在同一个Excel；业务表看汇总，审核表追溯批次、车次、邮编和异常。"},
        {"规则类别": "统计粒度", "说明": "方数按批次；车次按真实车次去重；柜量按柜号去重。车次类指标优先使用车次样本，避免用总方数÷总车次替代。"},
        {"规则类别": "成本口径", "说明": "批次平均每方成本=同一有效批次单价样本的平均/P80/P90；每方参考价=同一有效来源总成本÷总方数，二者并列展示，不混用。"},
        {"规则类别": "调拨与混合卸货", "说明": "混合目的地车只把调拨批次的方数、成本计入调拨；整车价和整车装载只使用完整调拨车；车次按完整车1、混合车按调拨批次精确车份额。"},
        {"规则类别": "时效统计", "说明": "按有效批次方数加权给平均/P80/P90；LTL无需车次，FTL需真实车次；无效时间、无效方数及备注含“里/外”的批次不进时效样本。"},
        {"规则类别": "装载统计", "说明": "大车卡板容量60 CBM、地板容量90 CBM；先按真实车次算min(实际方数÷容量,100%)，再按车次统计平均/P80/P90；地板率样本为地板=1、卡板=0。"},
        {"规则类别": "分位数边界", "说明": "能落到批次/车次/柜号样本的时效、率和计算指标才输出平均/P80/P90；排名占比、供应商使用比例、成本覆盖率等结构性分子分母只保留同口径占比。"},
        {"规则类别": "数据筛选", "说明": "正成本样本的成本分子与方数分母同进同出；零成本、缺车次或缺车型的数据按指标规则保留在审核/总量口径，不强行进入不适用样本。"},
    ]
    for name in ["区域识别规则", "干线识别规则"]:
        for _, row in _frame(reports, name).iterrows():
            rows.append({"规则类别": name, "说明": "；".join(f"{col}：{value}" for col, value in row.items() if pd.notna(value))})
    return pd.DataFrame(rows)


def build_delivery_exports(reports):
    """Return one business workbook and one reusable audit workbook."""
    stations = {"FBA仓点总览": _station(reports, "FBA"), "FBX仓点总览": _station(reports, "FBX平台仓")}
    stations["FBX仓点总览"] = _append_fbx_unidentified_summary(reports, stations["FBX仓点总览"])
    covered = _move_station_dispatch(reports, stations)
    linehaul = _frame(reports, "干线数据")
    if not linehaul.empty:
        linehaul["货量口径"] = "仅纳入有效FTL发车批次；与派送总览线路识别总货量口径不同"
    candidates = {
        "派送总览": _overview(reports, covered),
        "FBA仓点总览": stations["FBA仓点总览"],
        "FBA派送方式分析": _frame(reports, "FBA派送方式分析"),
        "FBX仓点总览": stations["FBX仓点总览"],
        "分类价格参考": _classification_prices(reports),
        "调拨数据": _frame(reports, "调拨数据"),
        "干线数据": linehaul,
        "黄金标准数据": _frame(reports, "黄金标准数据"),
        "满载率与地板率": _build_loading_metrics_report(reports),
    }
    business = {name: data for name, data in candidates.items() if not data.empty}
    if not business:
        business["派送总览"] = pd.DataFrame({"说明": ["当前范围没有可展示的业务数据。"]})
    audit = {name: _frame(reports, name) for name in AUDIT_SHEETS}
    audit["规则说明"] = _rules(reports)
    return business, audit
