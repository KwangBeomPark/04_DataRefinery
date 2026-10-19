"""Main pipeline entrypoint for Data Refinery Kimball Star Schema DWH & AI Mart."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
import duckdb

# Reconfigure stdout/stderr to UTF-8 on Windows consoles to prevent charmap encoding errors
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from src.config_loader import ConfigLoader
from src.dimension_builder import DimensionBuilder
from src.extractor import DataExtractor
from src.fact_builder import FactBuilder
from src.loader import DwhLoader


def run_pipeline(
    config_path: str | None = None,
    generate_sample: bool = False,
    dry_run: bool = False,
    lang: str = "ko",
) -> int:
    """Run the complete end-to-end DWH ETL and Serving Mart pipeline."""
    start_time = time.time()
    is_ko = lang == "ko"
    is_pl = lang == "pl"

    title = "데이터 정제소(Data Refinery) - Kimball 스타 스키마 DWH & AI 마트 파이프라인" if is_ko else (
        "Data Refinery - Hurtownia DWH i Mart P&L" if is_pl else "Data Refinery - Kimball Star Schema DWH & AI Mart Pipeline"
    )

    print("=" * 76)
    print(f"   {title}")
    if dry_run:
        print("   [MODE: DRY-RUN (사전 무결성 점검 - 변경사항 커밋 안 됨)]" if is_ko else "   [MODE: DRY-RUN (Integrity Check Only - Changes will not be committed)]")
    print("=" * 76)

    # 1. Config Loading
    base_dir = Path(__file__).resolve().parent
    loader = ConfigLoader(base_dir=base_dir)
    try:
        settings = loader.load_settings(config_path)
        account_rules = loader.load_account_mapping()
        settings.ensure_directories()
    except Exception as e:
        err_lbl = "[오류] 설정 로드 실패:" if is_ko else "[ERROR] Configuration failed:"
        print(f"\n{err_lbl} {e}")
        return 1

    step1_title = "[1/5] 환경 설정 로드 완료:" if is_ko else "[1/5] Loaded Configuration:"
    print(f"\n{step1_title}")
    print(f"  * 대상 법인: {settings.entity_code} ({settings.entity_name})" if is_ko else f"  * Entity: {settings.entity_code} ({settings.entity_name})")
    print(f"  * 허용 통화: {settings.allowed_currencies} | 기준 통화: {settings.reporting_currency}" if is_ko else f"  * Currencies: Allowed={settings.allowed_currencies}, Reporting={settings.reporting_currency}")
    print(f"  * 손익 계정 매핑: {len(account_rules)}개 계정 규칙" if is_ko else f"  * Account Rules: {len(account_rules)} mapped accounts")
    print(f"  * DWH 데이터베이스: {settings.warehouse_db}" if is_ko else f"  * Warehouse DB: {settings.warehouse_db}")

    # Optional Sample Generation
    if generate_sample or not any(settings.masters_dir.glob("*.csv")):
        msg = "[*] 테스트용 마스터 및 P&L 샘플 원천 데이터 자동 생성 중..." if is_ko else "[*] Generating synthetic master and P&L sample data..."
        print(f"\n{msg}")
        from scripts.generate_sample_data import generate_sample_data
        generate_sample_data(base_dir=base_dir)

    # Connect to DuckDB Warehouse (Single DB for staging, core, mart)
    con = duckdb.connect(str(settings.warehouse_db))

    try:
        con.execute("BEGIN TRANSACTION;")

        # 2. Staging Extraction (Polars)
        step2_title = "[2/5] 원천 데이터 Staging 추출 및 법인 격리 (Polars 고속 처리)..." if is_ko else "[2/5] Extracting Raw Sources into Staging Layer (Polars)..."
        print(f"\n{step2_title}")
        extractor = DataExtractor(settings=settings, con=con)
        stg_results = extractor.extract_all()
        for name, stats in stg_results.items():
            if is_ko:
                print(f"  [성공] {stats.source_name.upper()}: {stats.filtered_entity_rows:,}행 적재 (원천: {stats.total_raw_rows:,}행, 타 법인 제외: {stats.excluded_entity_rows:,}행) -> {stats.staging_table}")
            else:
                print(f"  [OK] {stats.source_name.upper()}: {stats.filtered_entity_rows:,} rows staged (raw: {stats.total_raw_rows:,}, excluded: {stats.excluded_entity_rows:,}) -> {stats.staging_table}")

        # 3. Conformed Dimension Building (Kimball SCD 1)
        step3_title = "[3/5] 공식 마스터 기반 Conformed 공통 차원 빌드 (SCD Type 1)..." if is_ko else "[3/5] Building Conformed Master Dimensions (SCD Type 1)..."
        print(f"\n{step3_title}")
        dim_builder = DimensionBuilder(settings=settings, con=con)
        dim_results = dim_builder.build_all()
        for name, d_stats in dim_results.items():
            if is_ko:
                print(f"  [성공] {d_stats.table_name}: {d_stats.total_records:,}건 등록 (대리키: {d_stats.surrogate_key})")
            else:
                print(f"  [OK] {d_stats.table_name}: {d_stats.total_records:,} records (SK: {d_stats.surrogate_key})")

        # 4. Anti-Join Strict Fail-Fast Validation & Core Fact Building
        step4_title = "[4/5] Anti-Join 외래키 무결성 검증 (Strict Fail-Fast) 및 손익 롤업 계산..." if is_ko else "[4/5] Foreign Key Integrity Check (Anti-Join) & Fact Roll-up..."
        print(f"\n{step4_title}")
        fact_builder = FactBuilder(settings=settings, account_rules=account_rules, con=con)
        fact_stats = fact_builder.build_fact_pnl()
        if is_ko:
            print(f"  [성공] {fact_stats.table_name}: {fact_stats.total_records:,}행 팩트 생성 완료")
            print(f"    - 총매출액({settings.reporting_currency}): {fact_stats.total_gross_revenue_reporting:,.2f}")
            print(f"    - 영업이익({settings.reporting_currency}): {fact_stats.total_operating_profit_reporting:,.2f}")
        else:
            print(f"  [OK] {fact_stats.table_name}: {fact_stats.total_records:,} records generated")
            print(f"    - Gross Revenue ({settings.reporting_currency}): {fact_stats.total_gross_revenue_reporting:,.2f}")
            print(f"    - Operating Profit ({settings.reporting_currency}): {fact_stats.total_operating_profit_reporting:,.2f}")

        # 5. Data Mart Serving & Excel Power Query Parquet Export
        step5_title = "[5/5] AI OBT 마트 뷰 생성 및 엑셀(Power Query)용 Parquet 파일 내보내기..." if is_ko else "[5/5] Creating OBT Mart View & Exporting Parquet for Excel..."
        print(f"\n{step5_title}")
        dwh_loader = DwhLoader(settings=settings, con=con)
        loader_stats = dwh_loader.run_all()
        for v in loader_stats.views_created:
            if is_ko:
                print(f"  [성공] OBT 분석 뷰 생성: {v} ({loader_stats.total_obt_rows:,}개 평면 행)")
            else:
                print(f"  [OK] View created: {v} ({loader_stats.total_obt_rows:,} flat rows)")

        export_msg = f"  * 마트 저장 폴더: {settings.mart_export_dir}" if is_ko else f"  * Parquet export directory: {settings.mart_export_dir}"
        print(export_msg)
        for src, path in loader_stats.exported_parquet_files.items():
            print(f"    - {Path(path).name} ({src})")

        if dry_run:
            con.execute("ROLLBACK;")
            print("\n" + "-" * 76)
            print("[DRY-RUN 완료] 데이터 무결성 검증 통과 (테스트 롤백 완료, 실제 데이터는 변경되지 않았습니다)." if is_ko else "[DRY-RUN COMPLETED] Integrity verified (Rollback performed).")
            print("-" * 76 + "\n")
            return 0

        con.execute("COMMIT;")

        elapsed = time.time() - start_time
        print("\n" + "=" * 76)
        if is_ko:
            print(f"   [성공] DWH 파이프라인 전체 완료! (총 소요시간: {elapsed:.2f}초)")
            print("=" * 76)
            print("Microsoft Excel 파워 쿼리(Power Query) 연결 방법:")
            print("  1. 엑셀 실행 ➔ 상단 메뉴 [데이터] ➔ [데이터 가져오기] ➔ [파일에서] ➔ [Parquet에서] 선택")
            print(f"  2. 파일 선택 창에서 다음 경로를 지정: {settings.mart_export_dir / 'v_ai_pnl_summary.parquet'}")
            print("  3. [로드] 또는 [다음으로 로드... ➔ 피벗 테이블 보고서]를 선택하면 즉시 손익 분석 시작!")
        else:
            print(f"   [SUCCESS] Pipeline Completed Successfully in {elapsed:.2f} seconds!")
            print("=" * 76)
            print("Excel Power Query Connection Guide:")
            print("  1. Open Microsoft Excel -> Data -> Get Data -> From File -> From Parquet")
            print(f"  2. Select: {settings.mart_export_dir / 'v_ai_pnl_summary.parquet'}")
            print("  3. Click 'Load' or 'Load to Data Model / PivotTable'")
        print("=" * 76 + "\n")
        return 0

    except Exception as e:
        try:
            con.execute("ROLLBACK;")
        except Exception:
            pass
        fail_lbl = "[파이프라인 실행 중단 - Fail-Fast]" if is_ko else "[PIPELINE FAILED] Execution halted:"
        print("\n" + "!" * 76)
        print(f"{fail_lbl}")
        print(f"{e}")
        print("!" * 76 + "\n")
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Refinery Kimball Star Schema DWH & Mart Pipeline.")
    parser.add_argument("--config", default=None, help="Path to custom settings.yaml")
    parser.add_argument("--generate-sample", action="store_true", help="Generate sample data before running pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Perform integrity check without committing data")
    parser.add_argument("--lang", default="ko", choices=["ko", "en", "pl"], help="Console output language (default: ko)")
    args = parser.parse_args()

    sys.exit(run_pipeline(
        config_path=args.config,
        generate_sample=args.generate_sample,
        dry_run=args.dry_run,
        lang=args.lang,
    ))
