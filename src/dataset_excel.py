"""Excel Power Query integration and macro-free template generator for datasets.

Generates standalone macro-free (.xlsx) templates connected to the shared published CSV
using Microsoft 365 Power Query (M code).
Consumers need only Excel 64-bit with default Power Query—no Python, DuckDB, or ODBC required.
"""

from __future__ import annotations

import re
import sys
import gc
from pathlib import Path
from typing import List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from src.dataset_config import DatasetDefinition


def generate_powerquery_m_code(
    csv_path: Path | str,
    dataset: DatasetDefinition,
    columns: Optional[List[str]] = None,
) -> str:
    """
    Generate clean, robust Power Query M code for Microsoft 365 Excel.
    Preserves leading zeros for identifiers, sets correct types, and supports UTF-8.
    """
    # Windows path for M query: use forward slashes
    norm_path = Path(csv_path).resolve().as_posix()
    query_name = dataset.name.replace('"', '""')

    # Build type transformations
    # Default is type text for safety, especially codes and dates
    # Numeric columns become type number
    col_transforms: List[str] = []
    seen_cols = set()

    # 1. Period column -> type text
    p_col = dataset.period_column.replace('"', '""')
    col_transforms.append(f'{{"{p_col}", type text}}')
    seen_cols.add(dataset.period_column)

    # 2. Key columns -> strictly type text to preserve leading zeros
    for k in dataset.key_columns:
        if k in seen_cols:
            continue
        clean_k = k.replace('"', '""')
        col_transforms.append(f'{{"{clean_k}", type text}}')
        seen_cols.add(k)

    # 3. Explicit column types
    for col, ctype in dataset.column_types.items():
        if col in seen_cols:
            continue
        clean_c = col.replace('"', '""')
        ctype_upper = ctype.strip().upper()
        if any(ctype_upper.startswith(prefix) for prefix in ("DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "BIGINT", "INTEGER", "INT", "REAL")):
            col_transforms.append(f'{{"{clean_c}", type number}}')
        else:
            col_transforms.append(f'{{"{clean_c}", type text}}')
        seen_cols.add(col)

    # 4. Numeric columns
    for num_col in dataset.numeric_columns:
        if num_col in seen_cols:
            continue
        clean_num = num_col.replace('"', '""')
        col_transforms.append(f'{{"{clean_num}", type number}}')
        seen_cols.add(num_col)

    # 5. Full column list fallback
    if columns:
        for col in columns:
            if col not in seen_cols:
                clean_c = col.replace('"', '""')
                col_transforms.append(f'{{"{clean_c}", type text}}')
                seen_cols.add(col)

    transforms_str = ",\n        ".join(col_transforms)

    m_code = f"""let
    // {query_name} - Data Refinery 배포 데이터셋 연결
    // 공유 폴더에 배포된 최신 완성본 CSV를 읽어옵니다.
    SourcePath = "{norm_path}",
    Source = Csv.Document(
        File.Contents(SourcePath),
        [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]
    ),
    #"승격된 헤더" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    #"형식 변환" = Table.TransformColumnTypes(#"승격된 헤더", {{
        {transforms_str}
    }}, "en-US")
in
    #"형식 변환"
"""
    return m_code


def get_powerquery_guide_text(csv_path: Path | str, dataset: DatasetDefinition) -> str:
    """Return Korean markdown/text instructions for manually connecting Power Query in Excel."""
    norm_path = str(Path(csv_path).resolve())
    m_code = generate_powerquery_m_code(csv_path, dataset)

    return f"""======================================================================
[Data Refinery] Microsoft 365 Excel Power Query 연결 가이드
데이터셋명: {dataset.name}
공유 CSV 경로: {norm_path}
======================================================================

소비자 PC에서는 Python, DuckDB, ODBC 등 어떠한 추가 프로그램도 설치할 필요가 없습니다.
Excel의 기본 기능인 '파워 쿼리(Power Query)'만으로 항상 최신 데이터를 조회할 수 있습니다.

[방법 1: 30초 간편 복사 연결 (고급 편집기)]
1. Excel 실행 후 새 통합 문서(빈 워크북)를 엽니다.
2. 상단 리본 메뉴에서 [데이터] > [데이터 가져오기] > [기타 원본에서] > [빈 쿼리]를 클릭합니다.
3. 파워 쿼리 편집기 창이 열리면, 상단 [고급 편집기] 버튼을 클릭합니다.
4. 기존 내용을 모두 지우고 아래 M 쿼리 전체를 그대로 붙여넣은 뒤 [완료]를 누릅니다:

------------------------- [ M 쿼리 코드 시작 ] -------------------------
{m_code}
------------------------- [ M 쿼리 코드 끝 ] ---------------------------

5. 좌측 상단 [닫기 및 로드]를 클릭합니다.
6. 이제 이 Excel 파일을 사용자의 PC나 원하는 폴더 어디에 저장해도,
   상단 [데이터] > [모두 새로 고침]을 누르면 공유 폴더의 최신 배포본을 항상 자동으로 읽어옵니다!

[보안 및 권한 안내]
- 회사 보안 정책에 따라 최초 실행 시 상단에 '외부 데이터 연결 사용' 노란색 알림줄이 뜰 수 있습니다.
  이때 [콘텐츠 사용]을 한 번 클릭해 주시면 정상 연결됩니다.
- 본 템플릿을 열람하는 모든 소비자는 공유 폴더({Path(csv_path).parent})에 대한 네트워크 읽기 권한이 있어야 합니다.
"""


def create_excel_template_workbook(
    template_save_path: Path | str,
    target_csv_path: Path | str,
    dataset: DatasetDefinition,
    columns: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    Attempt to create an Excel template workbook (.xlsx).
    First tries Excel COM automation via win32com to natively inject Power Query table.
    If Excel is not installed or COM is unavailable, generates a beautiful guide workbook via openpyxl.

    Returns:
        (is_native_com, message)
    """
    target_path = Path(template_save_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    csv_resolved = Path(target_csv_path).resolve()
    if not csv_resolved.exists():
        raise FileNotFoundError(
            f"배포된 데이터셋 CSV 파일이 존재하지 않습니다: {csv_resolved}\n먼저 데이터셋을 배포(Publish)한 후 템플릿을 생성하세요."
        )
    m_code = generate_powerquery_m_code(csv_resolved, dataset, columns)

    # 1. Try Excel COM automation if on Windows (using isolated DispatchEx instance)
    if sys.platform == "win32":
        excel = None
        wb = None
        pythoncom_initialized = False
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            pythoncom_initialized = True

            # DispatchEx ensures a completely separate Excel process without touching user's open workbooks
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False

            try:
                wb = excel.Workbooks.Add()

                # Add Power Query M query
                query_name = f"Query_{dataset.id[:8]}"
                wb.Queries.Add(Name=query_name, Formula=m_code)

                # Add Data Model Connection using Add2 (CreateModelConnection=True)
                conn_str = f'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location={query_name};Extended Properties=""'
                conn = wb.Connections.Add2(
                    f"Query - {query_name}",
                    f"Connection to {query_name}",
                    conn_str,
                    query_name,
                    2,  # xlCmdTable
                    True,  # CreateModelConnection (loads into Excel Data Model)
                    False,  # ImportRelationships
                )

                # Clean sheet name for Excel (max 31 chars, no forbidden chars)
                clean_name = re.sub(r"[:\\/?*\[\]]", "_", dataset.name.strip())[:20]
                sheet1 = wb.Sheets(1)
                sheet1.Name = f"{clean_name}_피벗분석"

                # Create Data Model PivotTable
                pc = wb.PivotCaches().Create(SourceType=2, SourceData=conn)
                pt = pc.CreatePivotTable(TableDestination=sheet1.Range("A4"), TableName="PivotTable1")

                # Model pivots use cube hierarchies rather than plain worksheet fields.
                table_name = wb.Model.ModelTables.Item(1).Name
                def hierarchy(column):
                    table = table_name.replace("]", "]]")
                    field = column.replace("]", "]]")
                    return f"[{table}].[{field}]"

                pt.CubeFields.Item(hierarchy(dataset.period_column)).Orientation = 1
                for column in dataset.numeric_columns:
                    measure = pt.CubeFields.GetMeasure(
                        hierarchy(column), -4157, f"합계 : {column}"
                    )
                    measure.Orientation = 4  # xlDataField
                if not dataset.numeric_columns:
                    measure = pt.CubeFields.GetMeasure(
                        hierarchy(dataset.period_column), -4112, "자료 행 수"
                    )
                    measure.Orientation = 4

                # Add descriptive title in worksheet
                sheet1.Range("A1").Value = f"[{dataset.name}] 데이터 모델 피벗 분석"
                sheet1.Range("A1").Font.Bold = True
                sheet1.Range("A1").Font.Size = 13
                sheet1.Range("A2").Value = f"연결된 공유 CSV: {csv_resolved}"
                sheet1.Range("A2").Font.Size = 9

                # Sheet 2: Guide & Info
                sheet2 = wb.Sheets.Add(After=sheet1)
                sheet2.Name = "템플릿 안내"
                sheet2.Range("A1").Value = f"[{dataset.name}] 분석 템플릿 사용 안내"
                sheet2.Range("A1").Font.Bold = True
                sheet2.Range("A1").Font.Size = 14

                sheet2.Range("A3").Value = "연결된 공유 배포 파일:"
                sheet2.Range("B3").Value = str(csv_resolved)

                sheet2.Range("A5").Value = "최신 데이터 새로고침:"
                sheet2.Range("B5").Value = "상단 리본 메뉴 [데이터] > [모두 새로 고침]을 클릭하세요."

                sheet2.Range("A7").Value = "데이터 모델(PowerPivot):"
                sheet2.Range("B7").Value = "본 템플릿은 Excel 데이터 모델에 연결되어 있어 시트 행 제한(104만 행) 없이 대용량 데이터도 피벗 테이블로 즉시 분석 가능합니다."

                # Select analysis sheet
                sheet1.Activate()

                # Refresh data once to verify connection (do not pass silently on failure!)
                wb.RefreshAll()
                excel.CalculateUntilAsyncQueriesDone()

                # Save as macro-free .xlsx (51 = xlOpenXMLWorkbook)
                wb.SaveAs(str(target_path), FileFormat=51)
                wb.Close(SaveChanges=False)
                wb = None

                excel.Quit()
                excel = None

                return (
                    True,
                    f"Excel 데이터 모델(PowerPivot) 및 기본 피벗 분석이 내장된 공식 템플릿이 생성되었습니다:\n{target_path}",
                )

            except Exception as com_err:
                if wb:
                    try:
                        wb.Close(SaveChanges=False)
                    except Exception:
                        pass
                    wb = None
                if excel:
                    try:
                        excel.Quit()
                    except Exception:
                        pass
                    excel = None
                raise RuntimeError(f"Excel COM 데이터 모델 템플릿 생성 실패: {com_err}") from com_err

        except Exception as e:
            if excel:
                try:
                    excel.Quit()
                except Exception:
                    pass
                excel = None
            if "데이터 모델 템플릿 생성 실패" in str(e):
                raise e
        finally:
            if pythoncom_initialized:
                # Release model/sheet proxies while their COM apartment is active.
                # Releasing them after CoUninitialize can leave a hidden Excel process.
                pt = pc = conn = measure = sheet1 = sheet2 = None
                gc.collect()
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    # 2. Fallback: generate guide workbook with openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "PowerQuery_가이드"

    # Styling
    navy_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="Malgun Gothic", size=14, bold=True, color="FFFFFF")
    bold_font = Font(name="Malgun Gothic", size=10, bold=True)
    code_font = Font(name="Consolas", size=9)
    regular_font = Font(name="Malgun Gothic", size=10)
    Border(
        left=Side(style="thin", color="D3D3D3"),
        right=Side(style="thin", color="D3D3D3"),
        top=Side(style="thin", color="D3D3D3"),
        bottom=Side(style="thin", color="D3D3D3"),
    )

    ws.merge_cells("A1:G1")
    title_cell = ws["A1"]
    title_cell.value = f" [Data Refinery] {dataset.name} - Excel 파워 쿼리(Power Query) 연결 템플릿"
    title_cell.fill = navy_fill
    title_cell.font = header_font
    title_cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 36

    ws["A3"] = "데이터셋 이름"
    ws["B3"] = dataset.name
    ws["A4"] = "공유 배포 CSV 경로"
    ws["B4"] = str(csv_resolved)
    ws["A5"] = "권장 소비자 환경"
    ws["B5"] = "Microsoft 365 Excel 64비트 (추가 프로그램/드라이버 설치 불필요)"

    for r in range(3, 6):
        ws[f"A{r}"].font = bold_font
        ws[f"B{r}"].font = regular_font

    ws["A7"] = "[파워 쿼리 30초 연결 방법]"
    ws["A7"].font = Font(name="Malgun Gothic", size=11, bold=True, color="1F4E79")

    steps = [
        "1. 새 엑셀 통합 문서를 열고 상단 메뉴 [데이터] > [데이터 가져오기] > [기타 원본에서] > [빈 쿼리]를 클릭합니다.",
        "2. 파워 쿼리 창이 열리면 [고급 편집기]를 열고 아래 M 코드를 전체 붙여넣기한 뒤 [완료]를 누릅니다.",
        "3. 좌측 상단 [닫기 및 로드]를 누르면 공유 폴더의 최신 누적 데이터가 엑셀 표로 바로 불러와집니다.",
        "4. 이후 상단 [데이터] > [모두 새로 고침]만 누르면 언제나 최신 배포본으로 자동 갱신됩니다.",
    ]
    for idx, step in enumerate(steps, start=8):
        ws[f"A{idx}"] = step
        ws[f"A{idx}"].font = regular_font

    ws["A13"] = "[아래 M 쿼리 코드를 복사하여 고급 편집기에 붙여넣으세요]"
    ws["A13"].font = bold_font

    # Put M code into rows
    code_lines = m_code.splitlines()
    for l_idx, line in enumerate(code_lines, start=14):
        ws[f"A{l_idx}"] = line
        ws[f"A{l_idx}"].font = code_font

    ws.column_dimensions["A"].width = 85
    ws.column_dimensions["B"].width = 60

    wb.save(str(target_path))

    return (
        False,
        f"Excel COM 미사용 환경이므로 '수동 연결 가이드 템플릿'이 생성되었습니다:\n{target_path}\n"
        f"(공식 자동 연결 템플릿 생성을 위해서는 제작자 PC에 Microsoft 365 Excel 환경이 필요합니다.)",
    )
