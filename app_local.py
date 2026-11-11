"""Local-only CE-SDS Streamlit app. No model API is imported or called."""

import io
import re
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import pdfplumber
import streamlit as st

from labchip_parser import legacy_peak_row_count, parse_labchip_page, reported_peak_count, zero_peak_table_is_empty

st.set_page_config(page_title="CE-SDS 纯本地提取工具", page_icon="🧪", layout="centered")


def explicit_mode(text):
    text = str(text).upper()
    if re.search(r"\bNR\b|-NR\b", text):
        return "NR"
    if re.search(r"\bR\b|-R\b", text):
        return "R"
    return None


def format_result_value(value):
    if isinstance(value, str):
        return value
    if pd.isna(value):
        return value
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return value

def round_half_up_1(value):
    return float(Decimal(str(value)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))

# UI 标题与说明
st.title("🧪 LabChip CE-SDS 数据提取工具（纯本地版）")
st.markdown("上传仪器生成的 LabChip PDF 报告，按峰表列名识别数据并导出 Excel。整个过程不调用大模型或外部 API。扫描版 PDF 暂不支持。")

uploaded_file = st.file_uploader("📂 请拖拽或点击上传 PDF 报告文件", type="pdf")

if uploaded_file is not None:
    if st.button("🚀 一键开始提取", type="primary"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        status_text.info("正在读取 PDF 文件，请稍候...")

        try:
            with pdfplumber.open(uploaded_file) as pdf:
                pages = pdf.pages
                total_pages = len(pages)
                if not total_pages:
                    st.error("PDF 没有页面。")
                    st.stop()
                status_text.info(f"成功读取 PDF，共 {total_pages} 页。正在识别样品和峰表...")

                parsed_data = []
                errors = []
                empty_pages = 0
                empty_samples = []
                for i, page in enumerate(pages, 1):
                    page_text = page.extract_text() or ""
                    if not page_text:
                        errors.append(f"第 {i} 页：没有可读取的文字；扫描版 PDF 需要先进行 OCR。")
                    else:
                        try:
                            data = parse_labchip_page(page, page_text)
                        except Exception as exc:
                            data = None
                            errors.append(f"第 {i} 页：峰表解析出错（{type(exc).__name__}：{exc}）。")
                        if data:
                            parsed_data.append((i, data))
                        elif (reported_peak_count(page_text) == 0 or legacy_peak_row_count(page_text) == 0) and zero_peak_table_is_empty(page):
                            empty_pages += 1
                            sample_match = re.search(r"Sample\s*<([^>]+)>\s*Page\b", page_text, re.I)
                            sample_name = sample_match.group(1).strip() if sample_match else ""
                            if sample_name and not re.search(r"\b(buffer|blank)\b", sample_name, re.I):
                                empty_samples.append(f"第 {i} 页：{sample_name}")
                        elif not any(error.startswith(f"第 {i} 页：") for error in errors):
                            errors.append(f"第 {i} 页：未识别到峰表，或识别的峰数与报告不一致。")
                    progress_bar.progress(int(i / total_pages * 100))
                    status_text.info(f"正在解析第 {i}/{total_pages} 页…")

            if empty_pages:
                st.info(f"已跳过 {empty_pages} 页无峰样品。")
            if empty_samples:
                st.warning("以下非 Buffer/Blank 样品没有峰数据，未写入 Excel，请核对：" + "；".join(empty_samples))
            missing_area_pages = [page_num for page_num, data in parsed_data if data.get("Area Missing")]
            if missing_area_pages:
                st.warning(
                    f"第 {', '.join(map(str, missing_area_pages))} 页的峰表没有面积列；"
                    "无法验证面积阈值，对应结果会标为‘结果见图谱，供参考’。"
                )
            sample_modes = {}
            for page_num, data in parsed_data:
                mode = explicit_mode(data.get("Sample Name", ""))
                if mode:
                    sample_modes[page_num] = mode
            file_mode = explicit_mode(uploaded_file.name)
            known_modes = set(sample_modes.values())
            unmarked = [(page_num, data) for page_num, data in parsed_data if page_num not in sample_modes]
            if unmarked:
                if file_mode and known_modes <= {file_mode}:
                    for page_num, _ in unmarked:
                        sample_modes[page_num] = file_mode
                    st.warning(f"有 {len(unmarked)} 个样品名称没有 R/NR 标记，已根据文件名暂按 {file_mode} 计算；请核对。")
                else:
                    for page_num, data in unmarked:
                        errors.append(f"第 {page_num} 页：样品「{data.get('Sample Name', '')}」没有 R/NR 标记，无法确定计算规则。")
            if errors:
                with st.expander(f"有 {len(errors)} 处需要检查", expanded=True):
                    for error in errors[:30]:
                        st.write(error)
                status_text.error("提取未完成。请检查上面的提示；本次未生成 Excel。")
                st.stop()
            if not parsed_data:
                status_text.error("没有找到可用的样品峰表。")
                st.stop()
            status_text.info(f"已在本地提取 {len(parsed_data)} 个样品，正在计算结果…")

            parsed_data.sort(key=lambda x: x[0])
            all_results = []
            
            for page_num, data in parsed_data:
                current_sample = data.get('Sample Name', 'Unknown')
                current_vial = data.get('Vial', 'Unknown')
                peaks = data.get('Peaks', [])
                
                if not peaks:
                    continue
                    
                peaks_df = pd.DataFrame(peaks)
                peaks_df['Purity'] = pd.to_numeric(peaks_df['Purity'], errors='coerce')
                peaks_df['Migration Time'] = pd.to_numeric(peaks_df['Migration Time'], errors='coerce')
                if 'Area' in peaks_df.columns:
                    peaks_df['Area'] = pd.to_numeric(peaks_df['Area'], errors='coerce')
                else:
                    peaks_df['Area'] = 0.0
                
                peaks_df = peaks_df.dropna(subset=['Purity', 'Migration Time'])
                
                # 拒绝峰表中的无效纯度（真实纯度不可能大于 100%）
                peaks_df = peaks_df[peaks_df['Purity'] <= 100.0]
                
                if peaks_df.empty:
                    continue
                    
                is_reduced = sample_modes[page_num] == "R"
                sample_upper = str(current_sample).upper()

                result_row = {
                    'Sample Name': current_sample,
                    'Vial': current_vial
                }
                
                if not is_reduced:
                    # 非还原(NR)逻辑
                    max_purity = peaks_df['Purity'].max()
                    
                    # 核心重构：精准定位“需要汇报的主峰(IgG)”
                    # 按照领导指示：主峰通常是最后的那个峰（通常在 28s 之后）
                    late_peaks = peaks_df[peaks_df['Migration Time'] >= 28.0]
                    mp_area = 0.0
                    dominant_peaks = peaks_df[
                        (peaks_df['Purity'] >= 20.0) &
                        (peaks_df['Area'] > 415.0)
                    ]
                    if not dominant_peaks.empty:
                        # 若存在明确高纯度、高面积主峰，优先相信实际最大主峰时间；兼容 22s 左右的正常 NR 主峰。
                        mp_idx = dominant_peaks['Purity'].idxmax()
                        mp_row = peaks_df.loc[mp_idx]
                        mp_time = mp_row['Migration Time']
                        mp_purity = mp_row['Purity']
                        mp_area = float(mp_row['Area'])
                        fragments = peaks_df[peaks_df['Migration Time'] < mp_time]['Purity'].sum()
                    elif not late_peaks.empty and late_peaks['Purity'].max() >= 0.2:
                        # 形态1：存在真实的晚出峰IgG（即使它降解得很严重，只要纯度>0.2%，它就是我们要找的目标）
                        mp_idx = late_peaks['Purity'].idxmax()
                        mp_row = peaks_df.loc[mp_idx]
                        mp_time = mp_row['Migration Time']
                        mp_purity = mp_row['Purity']
                        mp_area = float(mp_row['Area'])
                        # Fragments 为该最终主峰之前的所有峰（主峰后面的碎片峰坚决不计入）
                        fragments = peaks_df[peaks_df['Migration Time'] < mp_time]['Purity'].sum()
                    else:
                        # 形态2：完全没有晚出峰，属于早出峰多主峰形态 (如 20s 左右)
                        candidates = peaks_df[peaks_df['Purity'] > max_purity * 0.2].sort_values('Migration Time')
                        if not candidates.empty:
                            mp_purity = candidates['Purity'].sum()
                            mp_area = float(candidates['Area'].sum())
                            first_mp_time = candidates.iloc[0]['Migration Time']
                            # Fragments 为第一个主峰之前的所有碎峰
                            fragments = peaks_df[peaks_df['Migration Time'] < first_mp_time]['Purity'].sum()
                        else:
                            # 兜底逻辑
                            mp_idx = peaks_df['Purity'].idxmax()
                            mp_time = peaks_df.loc[mp_idx, 'Migration Time']
                            mp_purity = peaks_df.loc[mp_idx, 'Purity']
                            mp_area = float(peaks_df.loc[mp_idx, 'Area'])
                            fragments = peaks_df[peaks_df['Migration Time'] < mp_time]['Purity'].sum()

                    # 浓度过滤：主峰面积不足时提示复核图谱。
                    if pd.isna(mp_area) or mp_area <= 415.0:
                        result_row['Fragments'] = '结果见图谱，供参考'
                        result_row['NR-MP/IgG'] = '结果见图谱，供参考'
                        all_results.append(result_row)
                        continue

                    result_row['Fragments'] = round_half_up_1(fragments)
                    result_row['NR-MP/IgG'] = round_half_up_1(mp_purity)
                else:
                    # 还原(R)逻辑：处理 LC/HC 类型 和 特殊 R-MP 类型

                    # 除首行系统峰外，面积均不足时提示复核图谱。
                    if len(peaks_df) <= 1 or pd.isna(peaks_df.iloc[1:]['Area'].max()) or peaks_df.iloc[1:]['Area'].max() <= 415.0:
                        result_row['LC+HC'] = '结果见图谱，供参考'
                        result_row['NGHC'] = '结果见图谱，供参考'
                        result_row['R-MP'] = '结果见图谱，供参考'
                        all_results.append(result_row)
                        continue

                    # 恢复拓扑骨架的准入门槛到 >5.0%，保证最大间隙算法绝对稳定 (不受微小噪音干扰破坏形态)
                    candidates = peaks_df[peaks_df['Purity'] > 5.0].sort_values('Migration Time')
                    
                    if not candidates.empty:
                        if len(candidates) >= 2:
                            # 核心算法：最大时间间隙聚类 (寻找拓扑断层)
                            max_gap = 0
                            split_idx = 1
                            for i in range(1, len(candidates)):
                                gap = candidates.iloc[i]['Migration Time'] - candidates.iloc[i-1]['Migration Time']
                                if gap > max_gap:
                                    max_gap = gap
                                    split_idx = i
                                    
                            first_hc_time = candidates.iloc[split_idx]['Migration Time']
                            borderline_nghc = peaks_df[
                                (peaks_df['Migration Time'] >= first_hc_time - 1.2) &
                                (peaks_df['Migration Time'] <= first_hc_time - 0.2) &
                                (peaks_df['Purity'] >= 0.05) &
                                (peaks_df['Purity'] <= 5.0)
                            ]

                            # 保留原有 >3.0s 的 LC/HC 规则。仅对 2.0~3.0s 的边界形态，
                            # 在 HC 前方同时存在 NGHC 特征峰时，补充判定为 LC/HC。
                            is_borderline_lc_hc = (
                                2.0 < max_gap <= 3.0 and
                                not borderline_nghc.empty
                            )
                            terminal_peak = candidates.iloc[-1]
                            earlier_major_purity = candidates.iloc[:-1]['Purity'].sum()
                            # 典型双峰 LC/HC：末端 HC 占主导，前方 LC 保持合理比例。
                            # 该补充形态不改变普通双峰 R-MP 或单峰样品的原有路径。
                            is_terminal_hc_pattern = (
                                len(candidates) == 2 and
                                terminal_peak['Migration Time'] >= 25.0 and
                                terminal_peak['Purity'] >= 70.0 and
                                5.0 <= earlier_major_purity <= 30.0
                            )
                            if max_gap > 3.0 or is_borderline_lc_hc or is_terminal_hc_pattern:
                                # 以最大间隙为界，前面全是 LC，后面全是 HC
                                lc_group = candidates.iloc[:split_idx]
                                hc_group = candidates.iloc[split_idx:]
                                
                                # 目标1：完美加和。目标2：不破坏形态。
                                # 方案：形态由 >5.0% 的 candidates 决定。加和基数采用 >= 2.0% 的有效峰。
                                # 这完美包容了 3.88% 的 LC，同时排除了 0.14% 的噪音。
                                valid_sum_peaks = peaks_df[peaks_df['Purity'] >= 2.0]
                                lc_hc_total = valid_sum_peaks['Purity'].sum()
                                
                                # 启用多 NGHC 智能识别：严谨防假阳性策略
                                # 1. 动态相对时间窗：紧紧绑定在 HC 聚类的 **第一个** 峰前 0.2 ~ 1.2 秒
                                first_hc_time = hc_group.iloc[0]['Migration Time']
                                nghc_candidates = peaks_df[
                                    (peaks_df['Migration Time'] >= first_hc_time - 1.2) & 
                                    (peaks_df['Migration Time'] <= first_hc_time - 0.2)
                                ]
                                
                                # 2. 动态纯度阈值：对这批真实样本，NGHC 可低至 0.1% 左右
                                # 强制规则：对于名称中包含 'REP' 的样品，跳过纯度下限阈值，只要在时间窗内就算 NGHC
                                if 'REP' in sample_upper:
                                    valid_nghc = nghc_candidates[nghc_candidates['Purity'] <= 5.0]
                                elif is_borderline_lc_hc:
                                    valid_nghc = nghc_candidates[
                                        (nghc_candidates['Purity'] >= 0.05) &
                                        (nghc_candidates['Purity'] <= 5.0)
                                    ]
                                else:
                                    valid_nghc = nghc_candidates[
                                        (nghc_candidates['Purity'] >= 0.1) & 
                                        (nghc_candidates['Purity'] <= 5.0)
                                    ]
                                
                                nghc_purity = 0.0
                                if not valid_nghc.empty:
                                    nghc_purity = valid_nghc['Purity'].sum()
                                    
                                result_row['NGHC'] = round_half_up_1(nghc_purity)
                                
                                # LC+HC 等于有效峰总和扣除掉那些被判定为 NGHC 且被算入总和的峰
                                for _, row in valid_nghc.iterrows():
                                    if row['Purity'] >= 2.0: 
                                        lc_hc_total -= row['Purity']
                                        
                                result_row['LC+HC'] = round_half_up_1(lc_hc_total)
                            else:
                                # 没有明显的 LC/HC 断层，属于连续的 R-MP 多峰形态
                                r_mp_purity = candidates['Purity'].sum()
                                result_row['R-MP'] = round_half_up_1(r_mp_purity)
                        else:
                            # 只有一个主峰的情况的兜底逻辑
                            first_major_time = candidates.iloc[0]['Migration Time']
                            if first_major_time >= 22.0:
                                result_row['R-MP'] = round_half_up_1(candidates.iloc[0]['Purity'])
                            else:
                                major_peak = candidates.iloc[0]
                                other_peaks = peaks_df[peaks_df.index != major_peak.name]
                                is_single_dominant_lc_hc = (
                                    major_peak['Purity'] >= 90.0 and
                                    (other_peaks.empty or other_peaks['Purity'].max() <= 5.0)
                                )

                                if is_single_dominant_lc_hc:
                                    # 单个高占比 LC/HC 骨架峰：后置小峰属于杂质，不参与 LC+HC 加和。
                                    result_row['LC+HC'] = round_half_up_1(major_peak['Purity'])
                                else:
                                    # 保留既有单峰兜底行为，避免改变其他已验证样品。
                                    valid_sum_peaks = peaks_df[peaks_df['Purity'] >= 2.0]
                                    result_row['LC+HC'] = round_half_up_1(valid_sum_peaks['Purity'].sum())
                                result_row['NGHC'] = 0.0
                    else:
                        # A parsed R sample must still appear in the workbook.
                        # With no peak above the classification threshold, its
                        # LC/HC or R-MP type cannot be assigned reliably.
                        result_row['LC+HC'] = '结果见图谱，供参考'
                        result_row['NGHC'] = '结果见图谱，供参考'
                        result_row['R-MP'] = '结果见图谱，供参考'
                        
                all_results.append(result_row)
                
            if not all_results:
                st.error("未能计算出任何有效结果，请检查 PDF 格式。")
            else:
                df_all = pd.DataFrame(all_results)
                formatted_df_all = df_all.copy()
                for col in formatted_df_all.columns:
                    if col not in ['Sample Name', 'Vial']:
                        formatted_df_all[col] = formatted_df_all[col].apply(format_result_value)
                
                # 将数据写入内存中的 Excel
                output_buffer = io.BytesIO()
                with pd.ExcelWriter(output_buffer, engine='openpyxl') as writer:
                    has_data = False
                    if 'Fragments' in df_all.columns:
                        df_nr = df_all[['Sample Name', 'Vial', 'Fragments', 'NR-MP/IgG']].dropna(subset=['Fragments', 'NR-MP/IgG'])
                        df_nr = df_nr.rename(columns={
                            'Fragments': 'Fragments%',
                            'NR-MP/IgG': 'NR-MP/IgG%'
                        })
                        if not df_nr.empty:
                            df_nr.to_excel(writer, sheet_name='Non-Reduced (NR)', index=False)
                            has_data = True
                    
                    if 'LC+HC' in df_all.columns or 'R-MP' in df_all.columns:
                        cols_r = ['Sample Name', 'Vial']
                        if 'LC+HC' in df_all.columns: cols_r.append('LC+HC')
                        if 'NGHC' in df_all.columns: cols_r.append('NGHC')
                        if 'R-MP' in df_all.columns: cols_r.append('R-MP')
                        
                        df_r = df_all[cols_r].dropna(subset=[c for c in cols_r if c not in ['Sample Name', 'Vial']], how='all')
                        df_r = df_r.rename(columns={
                            'LC+HC': 'LC+HC%',
                            'NGHC': 'NGHC%',
                            'R-MP': 'R-MP%'
                        })
                        if not df_r.empty:
                            df_r.to_excel(writer, sheet_name='Reduced (R)', index=False)
                            has_data = True
                    for sheet in writer.sheets.values():
                        for row in sheet.iter_rows(min_row=2, min_col=3):
                            for cell in row:
                                if cell.data_type == 'n':
                                    cell.number_format = '0.0'

                if has_data:
                    st.success("🎉 数据处理完成！请点击下方按钮下载 Excel 报表。")
                    st.balloons()
                    
                    # 提供下载按钮
                    st.download_button(
                        label="📥 下载 Excel 汇总表",
                        data=output_buffer.getvalue(),
                        file_name=f"Result_{uploaded_file.name.replace('.pdf', '')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        type="primary"
                    )
                    
                    # 页面预览
                    st.markdown("### 📊 数据预览 (前 5 行)")
                    st.dataframe(formatted_df_all.head())
                else:
                    st.warning("没有提取到有效的 R 或 NR 数据。")
                    
        except Exception as e:
            st.error(f"处理过程中发生错误: {str(e)}")
