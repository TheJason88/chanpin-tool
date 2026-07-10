import importlib
from io import BytesIO
from datetime import date, datetime

import pandas as pd
import streamlit as st

import processors
import delivery_reference
import delivery_workflow
import delivery_match_adapter

# Streamlit rerun sometimes keeps imported modules in memory.
importlib.reload(processors)
importlib.reload(delivery_reference)
importlib.reload(delivery_workflow)
importlib.reload(delivery_match_adapter)
delivery_match_adapter.patch_delivery_workflow(delivery_workflow)

VALID_WAREHOUSES = ["LA", "NJ", "SAV", "DAL"]
PLACEHOLDER = "请填入"
DELIVERY_STAGE1_MODULE = "派送原数据处理"
DELIVERY_STAGE2_MODULE = "派送数据匹配及分析"
NORMAL_MODULES = ["货量分析", "提柜分析", "拆柜分析"]

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

if analysis_module == DELIVERY_STAGE1_MODULE:
    period_type = "不适用"
    st.info("派送原数据处理不做按周/按月分析，只负责合并、清洗、FTL/LTL识别、FTL车次合并、FBA/FBX与邮编识别。")
else:
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
    "派送拆成两步：第一步合并多个鲲运源文件、剔除无效批次、识别FTL/LTL、FTL按车次号合并，并识别FBA/FBX与邮编；未匹配邮编放到结果底部。"
    "无效批次审核包含非卡车派送、批次状态无效，以及派送方式为卡车配送但备注含废单/取消/作废/无效/删除/关闭等关键词的记录。"
    "第二步“派送数据匹配及分析”上传第一步结果和人工补充目的地文件，补齐商业/私人地址邮编后，再严格按周/月输出Excel分析报告。"
    "第二步匹配文件支持鲲运导出列表格式，字段可为批次号 + 邮编/目的地邮编/标准邮编 + 省/州/州/目的州；同一批次多个邮编会全部保留。"
    "干线识别优先读车次/批次备注中的 NJ / SAV / DAL，再读邮编规则。干线只对LA仓派送分析生效。"
    "工具已内置FBA仓点邮编表、平台仓邮编表和干线识别规则。干线邮编规则：NJ=070-089，Dallas=750-753，Chicago=606xx，Savannah=314xx。"
    "LTL不计入发车数，只参与方数结构；邮编列按文本处理；四位邮编自动补0。FTL车型缺失默认53尺大车；同车次装车类型同时出现卡板和地板时，聚合后按地板。"
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


selection_complete = warehouse != PLACEHOLDER and analysis_module != PLACEHOLDER
if analysis_module in NORMAL_MODULES or analysis_module == PLACEHOLDER:
    selection_complete = selection_complete and product_type != PLACEHOLDER and period_type != PLACEHOLDER
elif analysis_module == DELIVERY_STAGE2_MODULE:
    selection_complete = selection_complete and period_type != PLACEHOLDER

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
                st.warning("请先把仓点、分析模块选择完整。")
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
                cleaned_batches, invalid_detail, zip_audit_df, raw_detail = delivery_workflow.process_stage1_raw_files_to_cleaned_batches(
                    file_dfs=file_dfs,
                    warehouse=warehouse_for_processing,
                    period_type="不适用",
                    start_date=date_range[0],
                    end_date=date_range[1]
                )

                st.subheader("清洗后数据预览")
                st.dataframe(cleaned_batches.head(100), use_container_width=True)
                st.subheader("邮编异常数据预览")
                st.dataframe(zip_audit_df.head(100), use_container_width=True)
                st.subheader("无效数据预览")
                st.dataframe(invalid_detail.head(100), use_container_width=True)

                sheets = {
                    "清洗后数据": cleaned_batches,
                    "邮编异常数据": zip_audit_df,
                    "无效数据": invalid_detail,
                }
                output = write_sheets_to_excel(sheets)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"派送一_原数据处理_{warehouse}_{timestamp}.xlsx"
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
# 派送数据匹配及分析：上传派送一清洗合并结果 + 人工目的地匹配表
# =========================
elif analysis_module == DELIVERY_STAGE2_MODULE:
    stage1_file = st.file_uploader(
        "6A. 上传第一部分生成的派送一结果文件",
        type=["xlsx", "xls"],
        key="stage1_result_file"
    )
    match_file = st.file_uploader(
        "6B. 上传人工匹配完成的批次目的地文件（可直接上传鲲运导出列表，需含批次号 + 邮编/目的地邮编/标准邮编，可含省/州）",
        type=["xlsx", "xls"],
        key="manual_match_file"
    )

    if st.button("开始匹配并生成派送分析报告", type="primary"):
        try:
            if not selection_complete:
                st.warning("请先把仓点、分析模块、统计周期都选择完整。")
            elif not stage1_file or not match_file:
                st.warning("请同时上传派送一结果文件和人工匹配文件。")
            else:
                cleaned_batches = delivery_workflow.read_stage1_cleaned_batches(stage1_file)

                match_file.seek(0)
                match_xls = pd.ExcelFile(match_file)
                match_sheet = match_xls.sheet_names[0]
                match_file.seek(0)
                match_df = pd.read_excel(match_file, sheet_name=match_sheet, dtype=str)

                metrics = delivery_workflow.process_stage2_analysis(cleaned_batches, match_df, period_type=period_type)

                st.success("派送分析报告已生成，详细结果请下载Excel查看。")
                st.write("报告结构：货量、发车量、派送时效、成本四张核心表。")

                output = write_sheets_to_excel(metrics)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                file_name = f"派送分析报告_{warehouse}_{period_type}_{timestamp}.xlsx"
                st.download_button(
                    label="下载派送分析报告 Excel",
                    data=output,
                    file_name=file_name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
        except Exception as e:
            st.error("派送数据匹配及分析失败，请检查派送一结果文件、匹配文件字段或批次号是否一致。")
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
