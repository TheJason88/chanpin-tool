import importlib
from io import BytesIO
from datetime import date, datetime

import pandas as pd
import streamlit as st

import processors

# Streamlit rerun sometimes keeps imported modules in memory.
# Reload processors so GitHub updates to processors.py are picked up together with app.py.
importlib.reload(processors)

# 仓点筛选必须优先识别“入库仓库”。
# 鲲运导出表常用字段是“入库仓库”，页面第一项选择 LA/NJ/SAV/DAL/全部时，
# 需要先把入库仓库标准化为仓库，再执行仓点筛选或分仓统计。
WAREHOUSE_ALIASES = [
    "仓库", "仓点", "仓库名称", "所属仓", "目的仓",
    "入库仓库", "入库仓", "到仓仓库", "抵仓仓库", "实际入库仓库",
    "Warehouse", "Inbound Warehouse"
]
processors.FIELD_ALIASES["仓库"] = WAREHOUSE_ALIASES

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]


def process_delivery_timing_for_truck_delivery(df, warehouse, product_type, period_type, start_date=None, end_date=None):
    """
    派送分析专用口径：
    - 派送数据源没有产品渠道、客户名称，不按 T渠道 / 客户类型拆分。
    - 固定新增字段：派送方式 = 卡车配送。
    - 时间范围和统计周期字段：出库时间。
    - 派送时效 = 签收时间 - 出库时间。
    """
    df = processors.prepare_base_df(df)
    df = processors.filter_warehouse(df, warehouse)

    processors.require_columns(df, ["出库时间", "签收时间"], "派送分析")
    df = processors.filter_date_range(df, "出库时间", start_date, end_date)

    df["派送方式"] = "卡车配送"

    if "目的地" in df.columns:
        df = processors.apply_transfer_destination(df)
        df["系统产品类型"] = df["修正后目的地"].apply(processors.classify_system_product_type)
        df = processors.filter_by_product_type(df, product_type)
    else:
        df["系统产品类型"] = "未知"

    df = processors.add_period_column(df, period_type, "出库时间")
    df["出库时间"] = pd.to_datetime(df["出库时间"], errors="coerce")
    df["签收时间"] = pd.to_datetime(df["签收时间"], errors="coerce")
    df["派送时效"] = (df["签收时间"] - df["出库时间"]).dt.total_seconds() / 86400

    df = processors.mark_duration_abnormal(
        df,
        "派送时效",
        "出库时间",
        "签收时间",
        min_days=0.01,
        max_days=30,
    )

    detail_df = df.copy()
    detail_df.loc[~detail_df["是否有效"], "派送时效"] = pd.NA

    group_cols = ["仓库", "统计周期", "系统产品类型", "派送方式"]
    result_df = processors.build_duration_summary(
        detail_df,
        group_cols,
        "派送时效",
        total_name="总票数",
        valid_name="有效票数",
    )

    result_df = result_df.rename(columns={
        "平均时效": "平均派送时效",
        "P80时效": "P80派送时效",
        "P90时效": "P90派送时效",
        "最小时效": "最小派送时效",
        "最大时效": "最大派送时效",
    })

    return detail_df, result_df


# 覆盖 processors 中的派送函数，确保页面执行时采用最新派送口径。
processors.process_delivery_timing = process_delivery_timing_for_truck_delivery


st.set_page_config(
    page_title="美盈产品数据处理工具",
    layout="wide"
)

st.title("美盈产品数据处理工具")

st.write("请选择分析维度、时间范围并上传对应 Excel，系统将自动清洗、筛选、汇总并生成结果文件。")

st.divider()

PLACEHOLDER = "请填入"

warehouse = st.selectbox(
    "1. 选择仓点",
    [PLACEHOLDER, "LA", "NJ", "SAV", "DAL", "全部"],
    index=0
)

product_type = st.selectbox(
    "2. 选择产品类型",
    [PLACEHOLDER, "FBA", "FBX", "全部"],
    index=0
)

analysis_module = st.selectbox(
    "3. 选择分析模块",
    [
        PLACEHOLDER,
        "货量分析",
        "提柜分析",
        "拆柜分析",
        "派送分析",
    ],
    index=0
)

period_type = st.selectbox(
    "4. 选择统计周期",
    [PLACEHOLDER, "按月统计", "按周统计"],
    index=0
)

today = date.today()
month_start = today.replace(day=1)

date_range = st.date_input(
    "5. 选择时间范围",
    value=(month_start, today),
    format="YYYY-MM-DD"
)

uploaded_file = st.file_uploader(
    "6. 上传 Excel",
    type=["xlsx", "xls"]
)

st.caption(
    "说明：网页工具会按所选时间范围筛选数据，再按月或按周汇总。"
    "各模块使用的筛选日期字段为：货量=ETA；提柜=实际抵仓时间；拆柜=拆柜完成时间；派送=出库时间。"
    "派送分析不按产品渠道或客户名称拆分，固定派送方式为卡车配送。"
    "仓点筛选字段优先识别入库仓库，并统一映射：美西二号仓=LA，达拉斯盈仓=DAL，新泽西二号仓=NJ，萨凡纳盈仓=SAV。"
    "如果仓点选择“全部”，系统不会把四仓合并成一行，而是先按入库仓库识别 LA/DAL/NJ/SAV，再分别输出各仓结果。"
)


def selected_date_range_is_valid(value):
    return isinstance(value, (tuple, list)) and len(value) == 2 and value[0] is not None and value[1] is not None


def validate_uploaded_warehouse(uploaded_file, sheet_name, selected_warehouse):
    """
    页面层仓点校验：
    1. 如果选择 LA/NJ/SAV/DAL，且文件里存在入库仓库/仓库相关字段，则文件仓库必须全部等于页面选择。
    2. 如果选择“全部”，文件必须存在入库仓库/仓库相关字段，并且可映射为 LA/NJ/SAV/DAL。
    3. 这样可以避免先筛空数据，再误报“产品渠道字段全为空”。
    """
    uploaded_file.seek(0)
    preview_df = pd.read_excel(uploaded_file, sheet_name=sheet_name)
    preview_df = processors.normalize_columns(preview_df)

    if "仓库" not in preview_df.columns:
        if selected_warehouse == "全部":
            raise ValueError("仓点选择为“全部”，但文件中没有识别到入库仓库/仓库字段，无法按 LA/DAL/NJ/SAV 分仓分析。")
        return

    raw_values = sorted([str(x) for x in preview_df["仓库"].dropna().unique()])
    standardized = preview_df["仓库"].apply(processors.standardize_warehouse)
    actual_known = sorted([str(x) for x in standardized.dropna().unique() if str(x) in VALID_WAREHOUSES])

    if selected_warehouse == "全部":
        if not actual_known:
            raise ValueError(f"仓点选择为“全部”，但文件仓库值无法映射到 LA/DAL/NJ/SAV。文件仓库值：{raw_values}")
        return

    if selected_warehouse in VALID_WAREHOUSES:
        if not actual_known:
            raise ValueError(f"页面选择仓点为 {selected_warehouse}，但文件仓库值无法映射到 LA/DAL/NJ/SAV。文件仓库值：{raw_values}")
        if set(actual_known) != {selected_warehouse}:
            raise ValueError(
                f"仓点选择不匹配：页面选择的是 {selected_warehouse}，"
                f"但文件入库仓库识别为 {actual_known}。请改选正确仓点，或上传对应仓点的数据源。"
            )


selection_complete = all(
    item != PLACEHOLDER
    for item in [warehouse, product_type, analysis_module, period_type]
)

date_range_valid = selected_date_range_is_valid(date_range)

if uploaded_file is not None:
    try:
        uploaded_file.seek(0)
        excel_file = pd.ExcelFile(uploaded_file)
        sheet_names = excel_file.sheet_names

        if len(sheet_names) > 1:
            sheet_name = st.selectbox("选择工作表", sheet_names)
        else:
            sheet_name = sheet_names[0]

        st.success(f"文件上传成功，当前工作表：{sheet_name}")

        if st.button("开始分析", type="primary"):
            if not selection_complete:
                st.warning("请先把仓点、产品类型、分析模块、统计周期都选择完整。")
            elif not date_range_valid:
                st.warning("请选择完整的开始日期和结束日期。")
            elif date_range[0] > date_range[1]:
                st.warning("开始日期不能晚于结束日期。")
            else:
                validate_uploaded_warehouse(uploaded_file, sheet_name, warehouse)

                uploaded_file.seek(0)
                warehouse_for_processing = "四仓合并" if warehouse == "全部" else warehouse

                detail_df, result_df, final_module = processors.process_uploaded_file(
                    uploaded_file=uploaded_file,
                    sheet_name=sheet_name,
                    warehouse=warehouse_for_processing,
                    product_type=product_type,
                    analysis_module=analysis_module,
                    period_type=period_type,
                    start_date=date_range[0],
                    end_date=date_range[1]
                )

                st.subheader("数据处理结果")
                st.dataframe(result_df, use_container_width=True)

                st.subheader("清洗后的数据集预览")
                st.dataframe(detail_df.head(100), use_container_width=True)

                output = BytesIO()

                with pd.ExcelWriter(output, engine="openpyxl") as writer:
                    detail_df.to_excel(writer, index=False, sheet_name="清洗后的数据集")
                    result_df.to_excel(writer, index=False, sheet_name="数据处理结果")

                output.seek(0)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                start_date_text = date_range[0].strftime("%Y%m%d")
                end_date_text = date_range[1].strftime("%Y%m%d")
                file_name = (
                    f"{final_module}_{warehouse}_{product_type}_{period_type}_"
                    f"{start_date_text}-{end_date_text}_{timestamp}.xlsx"
                )

                st.download_button(
                    label="下载结果 Excel",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

    except Exception as e:
        st.error("处理失败，请检查文件字段、工作表、时间范围或分析模块是否匹配。")
        st.exception(e)
else:
    st.info("请先完成选择，并上传 Excel 文件。")
