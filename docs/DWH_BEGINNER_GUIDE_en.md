# 🏛️ Data Refinery DWH & P&L Mart: Complete Beginner's Practical Guide (A to Z)

> **Audience**: Business practitioners in Finance, Accounting, Sales Operations, and SCM without database or programming experience.  
> **Goal**: Consolidate disparate ERP transactions and master data into a high-performance, error-free **Data Warehouse (DWH)**, connect via **Excel Power Query**, and build automated financial dashboards.

---

## 📑 Table of Contents
1. [Understand DWH & P&L Mart in 1 Minute](#1-understand-dwh--pl-mart-in-1-minute)
2. [Prerequisites: 3 Essential Source File Specifications](#2-prerequisites-3-essential-source-file-specifications)
3. [5 Golden Rules to Prevent Common Mistakes (Must Read!)](#3-5-golden-rules-to-prevent-common-mistakes-must-read)
4. [Step-by-Step Execution Guide (Steps 1 to 3)](#4-step-by-step-execution-guide-steps-1-to-3)
5. [Microsoft Excel Power Query 1-Second Connection](#5-microsoft-excel-power-query-1-second-connection)
6. [Self-Healing Troubleshooting Manual](#6-self-healing-troubleshooting-manual)

---

## 1. Understand DWH & P&L Mart in 1 Minute

### 💡 Why Build a DWH Instead of Using Raw Excel Files?
* **Overcoming Excel's 1.04 Million Row Limit**: When ERP sales and expense data grow into hundreds of thousands or millions of rows, Excel freezes or crashes. Data Refinery utilizes the **DuckDB** analytical engine to slice through 1,000,000 rows in just 2 seconds.
* **Strict Foreign Key Integrity (Fail-Fast)**: If new customers or products appear in transactions without being registered in masters, calculations become distorted. Data Refinery halts and flags this early to guarantee 100% financial accuracy.
* **Automated Waterfall P&L Aggregation**: Over 100 complex ERP account columns are automatically rolled up into standard financial statements (Gross Revenue ➔ Deductions ➔ COGS ➔ Logistics ➔ Marketing ➔ Contribution Margin ➔ Operating Profit).

```
[Raw Sources (CSV)]
 ├── Customer Master (master_customers.csv)
 ├── Product Master (master_products.csv)
 └── P&L Transactions (raw_pnl_*.csv)
       │
       ▼ [Data Refinery 1-Click Pipeline]
 ├── 1. Staging (Entity isolation & data validation)
 ├── 2. Dimensions (Conformed master dimensions, SCD Type 1)
 ├── 3. Integrity (Anti-Join verification for unmapped codes)
 ├── 4. Fact P&L (114 accounts roll-up & multi-currency FX conversion)
 └── 5. Serving OBT View (Single denormalized mart free of fan-out traps)
       │
       ▼ [High-Speed Columnar Storage (Parquet)]
[Microsoft Excel Power Query / Power BI / AI Analytics]
```

---

## 2. Prerequisites: 3 Essential Source File Specifications

To build the DWH, prepare the following **3 CSV files** in the `data/01_raw/` directory or use the in-app **`[📂 Import File]`** helper buttons:

### ① Customer Master (`data/01_raw/masters/master_customers.csv`)
The complete registry of all accounts and client companies.
* **Required Columns**:
  * `bill_to_code`: Unique customer account code (e.g. `000100200301`)
  * `customer_name`: Name of customer (e.g. `MediaExpert Polska`)
  * `channel_group`: Channel category (e.g. `Retail`, `Online`, `B2B`)
  * `country_code`: 2-letter ISO country code (e.g. `PL`)
  * `city`: City name (e.g. `Warszawa`)

### ② Product Master (`data/01_raw/masters/master_products.csv`)
The complete list of SKU and model items.
* **Required Columns**:
  * `model_code`: Unique product model code (e.g. `OLED65C6P`)
  * `product_name`: Product description (e.g. `OLED TV 65 C6`)
  * `division`: Business division (e.g. `HE`, `HA`)
  * `category`: Product category (e.g. `OLED TV`, `Washing Machine`)

### ③ P&L Transaction File (`data/01_raw/pnl/*.csv`)
Raw periodic sales and expense transaction records downloaded from ERP.
* **Core Required Columns**:
  * `period`: Target period in `YYYY-MM` format (e.g. `2026-01`)
  * `entity_code`: Subsidiary entity code (e.g. `PL`)
  * `bill_to_code`: Customer code (must exist in customer master)
  * `model_code`: Product code (must exist in product master)
  * `currency_code`: Currency ISO code (`PLN`, `EUR`, etc.)
  * `sales_qty`: Sold unit quantity (numeric)
* **Account Columns**:
  * Defined in `config/account_mapping.yaml` (e.g. `REV_001`~`REV_010`, `COG_001`~`COG_020`, `LOG_001`~`LOG_015`, etc.)

---

## 3. 5 Golden Rules to Prevent Common Mistakes (Must Read!)

| Mistake Area | Symptom | Golden Rule & Best Practice |
| :--- | :--- | :--- |
| **1. Exporting as Standard CSV** | Non-English letters become corrupted or replaced with `???` | When saving from Excel, always select **`CSV UTF-8 (Comma delimited) (*.csv)`**. Legacy ANSI/CP949 corrupts non-ASCII text. |
| **2. Truncating Leading Zeros** | Account code `000100` gets converted to number `100`, causing FK mismatch | Keep code columns formatted as Text in Excel. Never double-click CSV directly; verify in a text editor like Notepad. |
| **3. Period Format Mixing** | Formats like `2026.01`, `2026/1`, or `202601` cause missing aggregations | Strictly adhere to **`YYYY-MM`** (e.g. `2026-01`, `2026-02`). Always pad single-digit months with zero (`2026-03`). |
| **4. Unregistered New Codes** | "Foreign Key Integrity Violation (Fail-Fast)" error halts the pipeline | If a new client or model appears in transactions, **add the code to master files first** before running the pipeline. |
| **5. Building with Open Excel Windows** | `Permission Denied` file lock error | If Excel holds an exclusive lock on CSV or Parquet files, the pipeline cannot write. **Close all Excel windows before building.** |

---

## 4. Step-by-Step Execution Guide (Steps 1 to 3)

### [Step 1] Source Verification & Setup
1. Launch Data Refinery. The app opens to **`1. 🏛️ P&L Mart Builder (DWH)`**.
2. If testing for the first time, click **`[✨ Generate Sample Sources]`** to create realistic masters and P&L sample files.
3. To supply real company data:
   - Option A: Use **`[📂 Import File]`** buttons beside each master row to pick CSV files from your PC.
   - Option B: Click **`[Open Folder]`** and place CSV files directly into `masters/` and `pnl/`.
4. Ensure all source status rows show green checkmarks (`✔`).

### [Step 2] 1-Click DWH Pipeline Build
1. Once all source files are ready, the blue **`[★ 1-Click Run Complete DWH Pipeline]`** button becomes enabled.
2. Click to run the 5-step background worker:
   - `1. Staging`: Extract and isolate target subsidiary records (PL).
   - `2. Dimensions`: Build master dimensions (SCD Type 1).
   - `3. FK Integrity`: Validate that all transaction codes exist in masters.
   - `4. Fact P&L`: Roll up 114 accounts and convert FX rates into reporting currency.
   - `5. OBT & Parquet`: Export denormalized high-speed columnar Parquet mart.
3. Upon completion, a summary popup displays elapsed time, total revenue, and operating profit.

### [Step 3] Validate & Connect to Excel
1. Click **`[📊 Quick Preview Mart Data]`** to view the top 50 rows directly inside the app without opening Excel!
2. Click **`[⚡ Copy Power Query M-Code]`** to copy the ready-to-use M-script to your clipboard.

---

## 5. Microsoft Excel Power Query 1-Second Connection

### Method A. 1-Second M-Code Connection (Fastest & Recommended)
1. In Card 3, click **`[⚡ Copy Power Query M-Code]`**.
2. Open **Microsoft Excel** with a blank workbook.
3. Go to the ribbon: **Data ➔ Get Data ➔ From Other Sources ➔ Blank Query**.
4. In the Power Query Editor, click **Advanced Editor** on the ribbon.
5. Select all existing text and paste (`Ctrl + V`) the clipboard snippet:
   ```powerquery
   let
       Source = Parquet.Document(File.Contents("C:\Users\...\v_ai_pnl_summary.parquet")),
       #"Navigated" = Source
   in
       #"Navigated"
   ```
6. Click **Done**, then click **Close & Load To... ➔ PivotTable Report**.
7. Drag fields like `period`, `channel_group`, `gross_revenue_reporting`, and `operating_profit_reporting` to analyze results immediately!

---

## 6. Self-Healing Troubleshooting Manual

### 🚨 Scenario 1: "Foreign Key Integrity Violation (Fail-Fast)"
* **Cause**: Transaction files contain customer or product codes not present in master CSVs.
* **Solution**: Check the **`Missing codes hint`** displayed on the error card. Click **`[📝 Open Masters Folder to Add Code]`**, append the missing row to `master_customers.csv` or `master_products.csv`, save, and click Build again.

### 🚨 Scenario 2: "Permission Denied: ... v_ai_pnl_summary.parquet"
* **Cause**: An active Excel process is locking the output file.
* **Solution**: Close all Excel instances and retry building.
