"""Compact presentation of existing stage-two results; no cost/time recalculation."""
import pandas as pd


BUSINESS_SHEETS = (
    "派送总览", "FBA仓点总览", "FBA派送方式分析", "FBX仓点总览",
    "分类价格参考", "调拨数据", "干线数据", "黄金标准数据",
)
AUDIT_SHEETS = ("派送二_匹配后批次数据", "派送二_车次汇总核对", "邮编异常审核")


def _frame(reports, name):
    value = reports.get(name)
    return value.copy(deep=True) if isinstance(value, pd.DataFrame) else pd.DataFrame()


def _join(frames, keys):
    """Join only at the declared grain; never multiply station rows or average P80s."""
    result = pd.DataFrame(columns=keys)
    for source in frames:
        if source.empty:
            continue
        part = source.copy()
        for key in keys:
            part[key] = part[key].fillna("").astype(str).str.strip()
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
    timing = _frame(reports, "派送时效")
    if not timing.empty:
        timing = timing[timing["目的地类型"].eq(kind)].rename(columns={"目的仓点": code})
        timing = timing[keys + [c for c in ["平均派送时效", "P80派送时效", "有效时效批次数", "有效时效方数", "无效时效批次数"] if c in timing]]
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
        "批次平均每方成本", "每方参考价（总成本÷总方数）", "平均派送时效", "P80派送时效",
    ]
    columns = [c for c in preferred if c in result] + [c for c in result if c not in preferred]
    return result[columns].sort_values(keys[:2] + ["总出库体积"] + keys[2:],
        ascending=[True, True, False] + [True] * len(keys[2:]), na_position="last", kind="stable").reset_index(drop=True)


def _move_station_dispatch(reports, stations):
    dispatch = _frame(reports, "发车量")
    if dispatch.empty:
        return set()
    dispatch = dispatch[dispatch["指标名称"].eq("目的仓点发车数")].copy()
    keys = ["仓库", "统计周期", "维度值"]
    identities = []
    for name, data in stations.items():
        code = "FBA仓点" if name == "FBA仓点总览" else "FBX仓点"
        if not data.empty:
            identities.append(data[["仓库", "统计周期", code]].rename(columns={code: "维度值"}))
    if not identities:
        return set()
    identity = pd.concat(identities, ignore_index=True)
    unique = identity.loc[~identity.duplicated(keys, keep=False)]
    dispatch = dispatch.merge(unique, on=keys, how="inner", validate="one_to_one")
    covered = set(dispatch[keys].itertuples(index=False, name=None))
    for name, data in list(stations.items()):
        if data.empty:
            continue
        code = "FBA仓点" if name == "FBA仓点总览" else "FBX仓点"
        keep = keys + [c for c in ["数值", "精确车份额"] if c in dispatch]
        extra = dispatch[keep].rename(columns={"维度值": code, "数值": "FTL折算发车数"})
        stations[name] = data.merge(extra, on=["仓库", "统计周期", code], how="left", validate="many_to_one")
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
                               (row["仓库"], row["统计周期"], row["维度值"]) in covered, axis=1)
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
            rows.append({"仓库": warehouse, "统计周期": period, "类别": name,
                         "指标": metric, "数值": total, "分布及占比": "；".join(entry(row) for _, row in group.iterrows())})
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
    })


def _rules(reports):
    rows = [
        {"规则类别": "使用说明", "说明": "业务报告用于查看汇总；本审核文件保留完整批次、车次和邮编补录。填写邮编异常审核后，将本文件上传功能二的5A。"},
        {"规则类别": "成本口径", "说明": "批次平均每方成本是有效批次单价的算术平均；每方参考价是原价格参考有效总成本÷有效总方数，两者分母不同。"},
        {"规则类别": "整车样本", "说明": "FBA派送方式分析允许同目的地多批次合车；分类价格参考沿用单批次整车样本。供应商成本不含仓内装车费，运营成本含装车费。"},
        {"规则类别": "时效口径", "说明": "按有效方数加权平均和P80；LTL无需车次，FTL须有真实车次；其他有效性规则沿用。时效单位为天，货量单位为CBM，价格单位为美元。"},
        {"规则类别": "发车口径", "说明": "总发车数按真实FTL车次去重，分布按批次车份额汇总后取整；各分组显示值不能直接相加代替总发车数。"},
    ]
    for name in ["区域识别规则", "干线识别规则"]:
        for _, row in _frame(reports, name).iterrows():
            rows.append({"规则类别": name, "说明": "；".join(f"{col}：{value}" for col, value in row.items() if pd.notna(value))})
    return pd.DataFrame(rows)


def build_delivery_exports(reports):
    """Return a business workbook (at most eight tabs) and a reusable audit workbook."""
    stations = {"FBA仓点总览": _station(reports, "FBA"), "FBX仓点总览": _station(reports, "FBX平台仓")}
    covered = _move_station_dispatch(reports, stations)
    candidates = {
        "派送总览": _overview(reports, covered),
        "FBA仓点总览": stations["FBA仓点总览"],
        "FBA派送方式分析": _frame(reports, "FBA派送方式分析"),
        "FBX仓点总览": stations["FBX仓点总览"],
        "分类价格参考": _classification_prices(reports),
        **{name: _frame(reports, name) for name in ["调拨数据", "干线数据", "黄金标准数据"]},
    }
    business = {name: data for name, data in candidates.items() if not data.empty}
    if not business:
        business["派送总览"] = pd.DataFrame({"说明": ["当前范围没有可展示的业务数据。"]})
    audit = {name: _frame(reports, name) for name in AUDIT_SHEETS}
    audit["规则说明"] = _rules(reports)
    return business, audit
