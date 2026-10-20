# 🏛️ Data Refinery DWH & Mart P&L: Kompletny Poradnik Praktyczny (A do Z)

> **Odbiorcy**: Specjaliści działów Finansów, Księgowości, Kontrolingu, Sprzedaży i SCM bez doświadczenia w programowaniu i bazach danych.  
> **Cel**: Połączenie rozproszonych transakcji ERP i kartotek w wydajną, bezbłędną **Hurtownię Danych (DWH)**, połączenie przez **Excel Power Query** oraz tworzenie zautomatyzowanych pulpitów menedżerskich.

---

## 📑 Spis treści
1. [Zrozum DWH i Mart P&L w 1 minutę](#1-zrozum-dwh-i-mart-pl-w-1-minut)
2. [Wymagania wstępne: 3 kluczowe pliki źródłowe](#2-wymagania-wstpne-3-kluczowe-pliki-zrdlowe)
3. [5 Złotych Zasad zapobiegania błędom (Wymagane!)](#3-5-zlotych-zasad-zapobiegania-bldom-wymagane)
4. [Instrukcja krok po kroku (Kroki 1 do 3)](#4-instrukcja-krok-po-kroku-kroki-1-do-3)
5. [Błyskawiczne połączenie z programem Microsoft Excel Power Query](#5-blyskawiczne-polaczenie-z-programem-microsoft-excel-power-query)
6. [Instrukcja rozwiązywania problemów (Troubleshooting)](#6-instrukcja-rozwiazywania-problemow-troubleshooting)

---

## 1. Zrozum DWH i Mart P&L w 1 minutę

### 💡 Dlaczego warto budować hurtownię zamiast zwykłych plików Excel?
* **Pokonanie limitu 1 048 576 wierszy w programie Excel**: Gdy dane transakcyjne osiągają miliony wierszy, Excel zawiesza się. Data Refinery wykorzystuje silnik analityczny **DuckDB**, aby przetworzyć 1 000 000 wierszy w zaledwie 2 sekundy.
* **Ścisła integralność kluczy obcych (Fail-Fast)**: Jeśli w transakcjach pojawią się nowi klienci lub produkty bez wcześniejszej rejestracji w kartotekach, wyliczenia ulegają zniekształceniu. Data Refinery wykrywa to natychmiast i chroni przed błędami raportowymi.
* **Automatyczna kaskada wyników P&L**: Ponad 100 kont kosztowych ERP jest automatycznie agregowanych do standardowej struktury finansowej (Przychody brutto ➔ Odliczenia ➔ Koszt wytworzenia ➔ Logistyka ➔ Marketing ➔ Marża I / II ➔ Wynik operacyjny).

```
[Pliki źródłowe (CSV)]
 ├── Kartoteka klientów (master_customers.csv)
 ├── Kartoteka produktów (master_products.csv)
 └── Transakcje P&L (raw_pnl_*.csv)
       │
       ▼ [Data Refinery 1-Click Pipeline]
 ├── 1. Staging (Izolacja podmiotu i walidacja)
 ├── 2. Wymiary (Zharmonizowane wymiary kartotekowe, SCD Typ 1)
 ├── 3. Integralność (Weryfikacja kodów metodą Anti-Join)
 ├── 4. Fakty P&L (Agregacja 114 kont i przeliczenia walutowe FX)
 └── 5. Widok OBT (Pojedyncza tabela analityczna bez zniekształceń Fan-out)
       │
       ▼ [Szybki magazyn kolumnowy (Parquet)]
[Microsoft Excel Power Query / Power BI / Raportowanie AI]
```

---

## 2. Wymagania wstępne: 3 kluczowe pliki źródłowe

Przygotuj poniższe **3 pliki CSV** w folderze `data/01_raw/` lub skorzystaj z przycisków **`[📂 Pobierz plik...]`** w aplikacji:

### ① Kartoteka klientów (`data/01_raw/masters/master_customers.csv`)
Rejestr kontrahentów i odbiorców faktur.
* **Wymagane kolumny**:
  * `bill_to_code`: Unikalny kod klienta (np. `000100200301`)
  * `customer_name`: Nazwa kontrahenta (np. `MediaExpert Polska`)
  * `channel_group`: Kanał dystrybucji (np. `Retail`, `Online`, `B2B`)
  * `country_code`: 2-literowy kod kraju (np. `PL`)
  * `city`: Miasto (np. `Warszawa`)

### ② Kartoteka produktów (`data/01_raw/masters/master_products.csv`)
Rejestr indeksów towarowych i modeli.
* **Wymagane kolumny**:
  * `model_code`: Unikalny kod modelu (np. `OLED65C6P`)
  * `product_name`: Nazwa artykułu (np. `OLED TV 65 C6`)
  * `division`: Pion handlowy (np. `HE`, `HA`)
  * `category`: Kategoria asortymentowa (np. `OLED TV`, `Pralki`)

### ③ Transakcje P&L (`data/01_raw/pnl/*.csv`)
Raporty sprzedaży i kosztów wyeksportowane z systemu ERP.
* **Podstawowe kolumny**:
  * `period`: Okres w formacie `YYYY-MM` (np. `2026-01`)
  * `entity_code`: Kod jednostki gospodarczej (np. `PL`)
  * `bill_to_code`: Kod klienta (musi istnieć w kartotece klientów)
  * `model_code`: Kod produktu (musi istnieć w kartotece produktów)
  * `currency_code`: Kod waluty (`PLN`, `EUR` itp.)
  * `sales_qty`: Ilość sprzedana (wartość numeryczna)
* **Kolumny kont**:
  * Zdefiniowane w pliku konfiguracyjnym (np. `REV_001`~`REV_010`, `COG_001`~`COG_020` itp.)

---

## 3. 5 Złotych Zasad zapobiegania błędom (Wymagane!)

| Obszar | Objaw błędu | Złota Zasada i Dobre Praktyki |
| :--- | :--- | :--- |
| **1. Zapis do zwykłego CSV** | Polskie znaki (ą, ę, ś, ć) zamieniają się w znaki zapytania `???` | W programie Excel wybierz **`CSV UTF-8 (rozdzielany przecinkami) (*.csv)`**. Starszy format ANSI niszczy kodowanie znaków. |
| **2. Utrata wiodących zer** | Kod `000100` staje się liczbą `100`, powodując błąd braku w kartotece | Zachowaj format kolumny jako Tekst w programie Excel. |
| **3. Mieszanie formatów daty** | Formaty takie jak `2026.01`, `2026/1` powodują brak agregacji | Zawsze stosuj ścisły format **`YYYY-MM`** (np. `2026-01`, `2026-02`). Miesiące jednocyfrowe uzupełniaj zerem (`2026-03`). |
| **4. Niezarejestrowane kody** | Błąd "Naruszenie integralności (Fail-Fast)" zatrzymuje przetwarzanie | Wprowadź nowy kod do kartoteki **przed uruchomieniem potoku**. |
| **5. Otwarte pliki w programie Excel** | Błąd odmowy dostępu (`Permission Denied`) | Excel blokuje edytowane pliki. **Zamknij program Excel przed uruchomieniem budowy.** |

---

## 4. Instrukcja krok po kroku (Kroki 1 do 3)

### [Krok 1] Sprawdzenie i przygotowanie plików
1. Uruchom Data Refinery. Program otwiera zakładkę **`1. 🏛️ Generator Hurtowni (DWH)`**.
2. W celu przetestowania kliknij **`[✨ Wygeneruj dane przykładowe]`**.
3. Aby załadować dane firmowe:
   - Opcja A: Użyj przycisków **`[📂 Pobierz plik...]`** przy każdym wierszu.
   - Opcja B: Kliknij **`[Otwórz folder]`** i skopiuj pliki bezpośrednio.
4. Upewnij się, że wszystkie statusy mają zielony znacznik (`✔`).

### [Krok 2] Uruchomienie potoku DWH 1 kliknięciem
1. Gdy pliki są gotowe, niebieski przycisk **`[★ 1-Click Uruchom potok hurtowni DWH]`** staje się aktywny.
2. Kliknij, aby uruchomić przetwarzanie:
   - Wyodrębnienie danych jednostki (PL) ➔ Budowa wymiarów ➔ Kontrola integralności ➔ Kaskada P&L ➔ Generowanie pliku Parquet.
3. Po zakończeniu pojawi się podsumowanie przychodów i zysku.

### [Krok 3] Weryfikacja i podłączenie do programu Excel
1. Kliknij **`[📊 Szybki podgląd hurtowni]`**, aby wyświetlić pierwsze 50 wierszy bezpośrednio w aplikacji bez uruchamiania programu Excel!
2. Kliknij **`[⚡ Kopiuj M-Code Power Query]`**, aby skopiować gotowy kod połączenia do schowka.

---

## 5. Błyskawiczne połączenie z programem Microsoft Excel Power Query

### Metoda A. Połączenie 1-sekundowe M-Code (Najszybsza i zalecana)
1. W Karcie 3 kliknij **`[⚡ Kopiuj M-Code Power Query]`**.
2. Otwórz program **Microsoft Excel** z nowym skoroszytem.
3. Przejdź do wstążki: **Dane ➔ Pobierz dane ➔ Z innych źródeł ➔ Puste zapytanie**.
4. W edytorze Power Query kliknij **Edytor zaawansowany**.
5. Zastąp istniejący tekst skopiowanym kodem (`Ctrl + V`):
   ```powerquery
   let
       Source = Parquet.Document(File.Contents("C:\Users\...\v_ai_pnl_summary.parquet")),
       #"Navigated" = Source
   in
       #"Navigated"
   ```
6. Kliknij **Gotowe**, a następnie **Zamknij i załaduj do... ➔ Raport w formie tabeli przestawnej**.
7. Rozpocznij analizę w tabeli przestawnej!

---

## 6. Instrukcja rozwiązywania problemów (Troubleshooting)

### 🚨 Sytuacja 1: "Naruszenie integralności kluczy obcych (Fail-Fast)"
* **Przyczyna**: W plikach transakcyjnych znajdują się kody kontrahentów lub artykułów nieobecne w kartotekach.
* **Rozwiązanie**: Sprawdź wskazówkę **`Niezarejestrowane kody`** na karcie błędu. Kliknij **`[📝 Otwórz folder kartotek, aby dodać kod]`**, dopisz brakujący wiersz na końcu kartoteki, zapisz i ponownie uruchom potok.

### 🚨 Sytuacja 2: "Permission Denied: ... v_ai_pnl_summary.parquet"
* **Przyczyna**: Plik wynikowy jest zablokowany przez otwarty program Excel.
* **Rozwiązanie**: Zamknij wszystkie okna programu Excel i spróbuj ponownie.
