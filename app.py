import importlib
from io import BytesIO
from datetime import date, datetime

import pandas as pd
import streamlit as st

import processors
import delivery_reference

# Streamlit rerun sometimes keeps imported modules in memory.
importlib.reload(processors)
importlib.reload(delivery_reference)

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]
PLACEHOLDER = "请填入"
DELIVERY_STAGE1_MODULE = "派送原数据处理"
DELIVERY_STAGE2_MODULE = "派送数据匹配"
NORMAL_MODULES = ["货量分析", "提柜分析", "拆柜分析"]
DELIVERY_MODULES = [DELIVERY_STAGE1_MODULE, DELIVERY_STAGE2_MODULE]

st.set_page_config(
    page_title="美盈产品数据处理工具",
    layout="wide"
)

st.title("美盈产品数据处理工具")
st.write("请选择分析维度、时间范围并上传对应 Excel，系统将自动清洗、筛选、汇总并生成结果文件。")
st.divider()

warehouse = st.selectbox(
    "1. 选择仓点",
    [PLACEHOLDER, "LA", "NJ", "SAV", "DAL", "全部"],
    index=0
)

analysis_module = st.selectbox(
    "2. 选择分析模块",
    [
        PLACEHOLDER,
        "货量分析",
        "提柜分析",
        "拆柜分析",
        DELIVERY_STAGE1_MODULE,
        DELIVERY_STAGE2_MODULE,
    ],
    index=0
)

if analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    product_type = st.selectbox(
        "3. 选择产品类型",
        [PLACEHOLDER, "FBA", "FBX", "全部"],
        index=0
    )
else:
    product_type = "全部"
    st.info("派送模块不按产品渠道或客户名称拆分，产品类型固定按派送规则自动识别。")

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

st.caption(
    "说明：货量=ETA；提柜=实际抵仓时间；拆柜=拆柜完成时间；派送原数据处理=出库时间。"
    "派送已经拆成两步：第一步上传一个或多个鲲运原始导出文件做合并清洗；第二步上传第一步结果和人工补充目的地文件，补齐邮编后再做完整派送指标。"
    "工具已内置FBA仓点邮编表和平台仓邮编表，会自动补充FBA/平台仓目的地邮编；商业/私人地址仍通过第二步批次匹配补充。"
    "邮编列会按文本处理；四位邮编会自动补0。FTL车型缺失默认53尺大车；同车次装车类型同时出现卡板和地板时，聚合后按地板。"
)


def selected_date_range_is_valid(value):
    return isinstance(value, (tuple, list)) and len(value) == 2 and value[0] is not None and value[1] is not None


def validate_uploaded_warehouse_for_df(df, selected_warehouse):
    preview_df = processors.normalize_columns(df)

    if "仓库" not in preview_df.columns:
        if selected_warehouse == "全部":
            raise ValueError("仓点选择为“全部”，但文件中没有识别到入库仓库/发货仓/仓库字段，无法按 LA/DAL/NJ/SAV 分仓分析。")
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
            # 没有可识别仓点时，单仓文件允许用页面选择作为默认发货仓。
            return
        if set(actual_known) != {selected_warehouse}:
            raise ValueError(
                f"仓点选择不匹配：页面选择的是 {selected_warehouse}，"
                f"但文件仓库识别为 {actual_known}。请改选正确仓点，或上传对应仓点的数据源。"
            )


def validate_uploaded_warehouse(uploaded_file, sheet_name, selected_warehouse):
    uploaded_file.seek(0)
    preview_df = pd.read_excel(uploaded_file, sheet_name=sheet_name)
    validate_uploaded_warehouse_for_df(preview_df, selected_warehouse)


def prepare_zip_text_columns(df):
    df = df.copy()
    for col in df.columns:
        if "邮编" in str(col) or "ZIP" in str(col).upper():
            df[col] = df[col].fillna("").astype(str)
            df[col] = df[col].replace({"nan": "", "None": "", "<NA>": ""})
    return df


def format_text_columns_in_sheet(ws):
    if ws.max_row < 1:
        return
    text_col_indexes = []
    for cell in ws[1]:
        header = str(cell.value) if cell.value is not None else ""
        if "邮编" in header or "ZIP" in header.upper():
            text_col_indexes.append(cell.column)
    for col_idx in text_col_indexes:
        for row in range(1, ws.max_row + 1):
            ws.cell(row=row, column=col_idx).number_format = "@"


def write_sheets_to_excel(sheets):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, df in sheets.items():
            safe_name = sheet_name[:31]
            if df is None:
                df = pd.DataFrame()
            df = prepare_zip_text_columns(df)
            df.to_excel(writer, index=False, sheet_name=safe_name)
            ws = writer.book[safe_name]
            format_text_columns_in_sheet(ws)
    output.seek(0)
    return output


def build_stage1_summary(stage1_df, exclude_df, zip_audit_df):
    rows = [
        {"项目": "派送一总行数", "数量": len(stage1_df)},
        {"项目": "非卡车派送/排除行数", "数量": len(exclude_df)},
        {"项目": "待补目的地邮编行数", "数量": len(zip_audit_df)},
    ]
    if "标准运输类型" in stage1_df.columns:
        counts = stage1_df["标准运输类型"].value_counts(dropna=False)
        for key, value in counts.items():
            rows.append({"项目": f"运输类型-{key}", "数量": int(value)})
    if "系统产品类型" in stage1_df.columns:
        counts = stage1_df["系统产品类型"].value_counts(dropna=False)
        for key, value in counts.items():
            rows.append({"项目": f"系统产品类型-{key}", "数量": int(value)})
    if "规则匹配类型" in stage1_df.columns:
        counts = stage1_df["规则匹配类型"].replace("", "无内置匹配").value_counts(dropna=False)
        for key, value in counts.items():
            rows.append({"项目": f"内置规则匹配-{key}", "数量": int(value)})
    return pd.DataFrame(rows)


selection_complete = warehouse != PLACEHOLDER and analysis_module != PLACEHOLDER and period_type != PLACEHOLDER
if analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    selection_complete = selection_complete and product_type != PLACEHOLDER

date_range_valid = selected_date_range_is_valid(date_range)

# =========================
# 派送原数据处理：允许上传多个鲲运导出文件
# =========================
if analysis_module == DELIVERY_STAGE1_MODULE:
    raw_files = st.file_uploader(
        "6. 上传派送原始数据文件（可多选，格式需一致）",
        type=["xlsx", "xls"],
        accept_multiple_files=True
    )

    if raw_files:
        st.success(f"已上传 {len(raw_files)} 个文件。")

    if st.button("开始处理派送原数据", type="primary"):
        try:
            if not selection_complete:
                st.warning("请先把仓点、分析模块、统计周期都选择完整。")
            elif not date_range_valid:
                st.warning("请选择完整的开始日期和结束日期。")
            elif date_range[0] > date_range[1]:
                st.warning("开始日期不能晚于结束日期。")
            elif not raw_files:
                st.warning("请至少上传一个派送原始数据文件。")
            else:
                file_dfs = []
                for file in raw_files:
                    file.seek(0)
                    xls = pd.ExcelFile(file)
                    sheet_name = xls.sheet_names[0]
                    file.seek(0)
                    df = pd.read_excel(file, sheet_name=sheet_name)
                    validate_uploaded_warehouse_for_df(df, warehouse)
                    file_dfs.append((file.name, df))

                warehouse_for_processing = "四仓合并" if warehouse == "全部" else warehouse
                stage1_df, exclude_df, zip_audit_df = processors.process_delivery_stage1_from_files(
                    file_dfs=file_dfs,
                    warehouse=warehouse_for_processing,
                    period_type=period_type,
                    start_date=date_range[0],
                    end_date=date_range[1]
                )

                stage1_df = delivery_reference.apply_delivery_reference_memory(stage1_df)
                exclude_df = stage1_df[~stage1_df["是否进入卡车派送分析"].astype(str).isin(["True", "true", "1", "是", "卡车派送"]) & (stage1_df["是否进入卡车派送分析"] != True)].copy()
                zip_audit_df = stage1_df[stage1_df["目的地邮编待补充"]].copy()
                summary_df = build_stage1_summary(stage1_df, exclude_df, zip_audit_df)

                st.subheader("派送一处理结果预览")
                st.dataframe(stage1_df.head(100), use_container_width=True)
                st.subheader("待补邮编数据预览")
                st.dataframe(zip_audit_df.head(100), use_container_width=True)

                sheets = {
                    "派送一_处理摘要": summary_df,
                    "派送一_清洗明细": stage1_df,
                    "派送一_排除数据审核": exclude_df,
                    "派送一_邮编异常审核": zip_audit_df,
                    "内置FBA邮编表": delivery_reference.FBA_REFERENCE_DF,
                    "内置平台仓邮编表": delivery_reference.PLATFORM_REFERENCE_DF,
                }
                output = write_sheets_to_excel(sheets)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"派送一_原数据处理_{warehouse}_{period_type}_{timestamp}.xlsx"
                st.download_button(
                    label="下载派送一结果 Excel",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error("派送原数据处理失败，请检查文件字段、仓点选择或时间范围。")
            st.exception(e)

# =========================
# 派送数据匹配：上传派送一结果 + 人工目的地匹配表
# =========================
elif analysis_module == DELIVERY_STAGE2_MODULE:
    stage1_file = st.file_uploader(
        "6A. 上传第一部分生成的派送一结果文件",
        type=["xlsx", "xls"],
        key="stage1_result_file"
    )
    match_file = st.file_uploader(
        "6B. 上传人工匹配完成的批次目的地文件（需含批次号 + 目的地邮编/标准邮编，可含州）",
        type=["xlsx", "xls"],
        key="manual_match_file"
    )

    if st.button("开始匹配并生成派送指标", type="primary"):
        try:
            if not selection_complete:
                st.warning("请先把仓点、分析模块、统计周期都选择完整。")
            elif not stage1_file or not match_file:
                st.warning("请同时上传派送一结果文件和人工匹配文件。")
            else:
                stage1_df = processors.read_stage1_sheet(stage1_file)
                stage1_df = delivery_reference.apply_delivery_reference_memory(stage1_df)

                match_file.seek(0)
                match_xls = pd.ExcelFile(match_file)
                match_sheet = match_xls.sheet_names[0]
                match_file.seek(0)
                match_df = pd.read_excel(match_file, sheet_name=match_sheet, dtype=str)

                metrics = processors.process_delivery_stage2_with_match(stage1_df, match_df, period_type=period_type)
                metrics["派送一_匹配后明细"] = delivery_reference.apply_delivery_reference_memory(metrics["派送一_匹配后明细"])
                metrics["内置FBA邮编表"] = delivery_reference.FBA_REFERENCE_DF
                metrics["内置平台仓邮编表"] = delivery_reference.PLATFORM_REFERENCE_DF

                st.subheader("派送二批次/车次聚合预览")
                st.dataframe(metrics["派送二_批次车次聚合"].head(100), use_container_width=True)
                st.subheader("FBA/FBX货量比")
                st.dataframe(metrics["FBA_FBX货量比"], use_container_width=True)
                st.subheader("发车汇总")
                st.dataframe(metrics["发车汇总"], use_container_width=True)

                output = write_sheets_to_excel(metrics)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"派送二_匹配分析结果_{warehouse}_{period_type}_{timestamp}.xlsx"
                st.download_button(
                    label="下载派送二分析结果 Excel",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error("派送数据匹配失败，请检查派送一结果文件、匹配文件字段或批次号是否一致。")
            st.exception(e)

# =========================
# 货量 / 提柜 / 拆柜：单文件处理
# =========================
else:
    uploaded_file = st.file_uploader(
        "6. 上传 Excel",
        type=["xlsx", "xls"]
    )

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

                    output = write_sheets_to_excel({"清洗后的数据集": detail_df, "数据处理结果": result_df})
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
