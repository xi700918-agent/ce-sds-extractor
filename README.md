# LabChip CE-SDS 数据提取工具

上传 LabChip 仪器导出的 PDF 报告，读取样品信息和峰表，按现有 R/NR 规则计算并下载 Excel 汇总结果。

本项目使用 Streamlit 提供网页界面，通过 `pdfplumber` 读取 PDF 中已有的文字及其位置。提取和计算不调用大模型或外部识别 API，不需要 API Key。

> 页面中的“纯本地版”指使用程序自身的解析算法。部署到 Streamlit Community Cloud 后，上传的 PDF 在云端运行环境中处理；在自己电脑启动时，PDF 在自己电脑中处理。

## 使用方法

1. 打开已经部署好的工具网页。
2. 上传一份 LabChip PDF 报告。每次处理一个文件，一份报告可以包含多个样品页面。
3. 点击“一键开始提取”。
4. 查看页面中的报错、无峰样品提示和结果预览。
5. 点击“下载 Excel 汇总表”。

## 支持的 PDF

- 使用仪器软件直接导出、包含可读取文字的 LabChip 报告。
- 已适配的峰表布局中，按字段标题定位迁移时间、纯度和面积，允许这些列换序，也允许增加孔位、分子量等列。
- 识别的字段包括 `Migration Time` / `Retention Time`、`% Purity`、`Corr. Area` / `Area` 等。
- 报告需要包含可识别的样品名称、孔位，以及完整的峰表信息。
- 有 `Peak Count` 的报告会核对峰数；已适配的旧版两行表头没有此字段时，会核对可见峰表行数。

当前不包含 OCR，扫描版、截图拼成的 PDF 无法直接处理。支持已有布局的列变化，不代表能够读取任意格式的 PDF；多样品同页、峰表跨页或其他未适配布局需要另行验证。

## R / NR 如何区分

- 优先读取样品名称中独立的 `R` 或 `NR` 标记，例如 `Sample-001 R`、`Sample-001 NR`。
- 样品名称缺少标记时，仅在文件名有明确 R/NR 标记、且报告中已标记的样品没有相反模式时，才按文件名推断，并显示提醒。
- 无法确定模式时，列出页码并停止导出。

`R` 表示还原，`NR` 表示非还原。R 样品进一步归入 LC/HC 或 R-MP，使用的是现有计算规则，并非直接从字母 R 得出。

## Excel 结果

按报告中的样品页生成结果行，重复测定保留为独立行。只生成有数据的工作表：

| 工作表 | 可能包含的列 |
| --- | --- |
| `Reduced (R)` | `Sample Name`、`Vial`、`LC+HC%`、`NGHC%`、`R-MP%` |
| `Non-Reduced (NR)` | `Sample Name`、`Vial`、`Fragments%`、`NR-MP/IgG%` |

- `Vial` 为孔位，例如报告中的 `A02` 可规范为 `A2`。
- 可计算结果保存为 Excel 数值，四舍五入保留一位小数；`99.8` 表示 `99.8%`。
- 空白结果单元格表示该样品没有输出这一类结果，不等于数值 0。
- 确认无峰的页面会跳过；非 Buffer/Blank 样品无峰时，会列出样品名称及页码供核对。
- 遇到不能识别的页面、关键数据损坏或峰数不一致时，整份报告停止导出，避免只生成部分样品的结果。

## “结果见图谱，供参考”是什么意思

该文字表示现有规则不输出确定的数值结果，常见原因包括：

- 峰表缺少面积列，无法执行面积筛选。
- NR 样品所选主峰的 `Corr. Area` 小于或等于 `415`。
- R 样品按当前规则检查的峰面积未超过 `415`。
- R 样品没有纯度大于 `5%` 的峰，无法可靠套用现有分类规则。

`415` 是原程序沿用的面积筛选参数，不是纯度值或浓度值。本仓库没有提供其方法验证依据，不能仅凭触发该参数判定样品无效。

当前 R 面积筛选会假定首行是系统峰并排除首行。若报告首行实际是样品峰，应复核这一假定是否适用于该方法。LC/HC、NGHC、R-MP 及 NR 主峰的划分也使用峰间隔、迁移时间和纯度等规则，需要结合实验方法核对；成功导出不代表这些业务分类已经全部完成验证。

## 本地运行

本地开发环境已使用 Python 3.11 运行。下载仓库后，在仓库目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app_local.py
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app_local.py
```

打开终端显示的网址，通常是 `http://localhost:8501`。保持运行窗口打开；若已有其他实例占用端口，以本次启动输出的实际网址为准。

## Streamlit Community Cloud 部署与更新

仓库根目录包含以下文件：

```text
app_local.py
labchip_parser.py
requirements.txt
README.md
```

在 Streamlit Community Cloud 中连接此 GitHub 仓库，选择 `main` 分支，入口文件填写 `app_local.py`。

后续更新时，将修改后的文件提交到应用连接的同一仓库和分支。Cloud 会自动检测更新；修改 `requirements.txt` 时会重新安装依赖。更新后使用已核对过的报告复测导出行数及结果。详见 [Streamlit 官方更新说明](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `app_local.py` | 网页界面、R/NR 识别、结果计算、Excel 导出 |
| `labchip_parser.py` | 样品及孔位识别、按表头定位峰表字段、峰数及空表检查 |
| `requirements.txt` | Streamlit、pdfplumber、Pandas、openpyxl 依赖 |
| `README.md` | 使用、运行、部署及现有规则说明 |
