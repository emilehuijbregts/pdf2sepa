# Batch 6 Round 1 — Final Report

| PDF | amount | invoice_number | invoice_date | vat | kvk | status |
|-----|--------|----------------|--------------|-----|-----|--------|
| Qblades INV_2026_00364.pdf | 397.24 | INV/2026/00364 | 2026-01-23 | NL860176113B01 | 75187760 | OK |
| Rems Rechnung-2612498.pdf | 375.66 | 2612498 | 2026-03-06 | NL001740777B35 | None | REGRESSION |
| Rensa 017650_26033768.pdf | 1627.81 | 017650 | 2026-01-14 | NL005977915B01 | 09051635 | OK |
| Rexel 113023143_0.pdf | 608.84 | 113023143 | 2026-01-16 | NL805119607B01 | 24267850 | OK |
| Roba INV-0396393.PDF | 2010.29 | INV-0396393 | 2026-02-04 | NL007469184B01 | 30073109 | OK |
| S for Software (IAprog B.V.) Factuur 2026001645.pdf | 121.0 | 2026001645 | 2026-02-05 | NL855554381B01 | 64172023 | OK |
| Salo VF1750913.pdf | 1198.87 | VF1750913 | 2026-01-08 | None | None | REGRESSION |
| Samedia R1126096.pdf | 353.87 | R1126096 | 2026-02-28 | DE141994165 | None | OK |
| Sanha REG-3461477.pdf | 1612.21 | REG20260000971 | 2026-02-24 | NL001740777B35 | None | OK |
| Schoonwijk Factuur 260241.pdf | 4831.53 | 260241 | 2026-03-20 | NL866632281B01 | 94090165 | OK |
| Sealeco 202630925.pdf | 990.43 | 202630925 | 2026-03-04 | NL805708480B01 | 05059245 | OK |
| SF inv26800314.pdf | 614.93 | 26800314 | 2026-02-20 | NL001740777B35 | 63734044 | OK |
| Solar_factuur_2911621756.pdf | 1452.92 | 2911621756 | 2026-02-03 | NL001302668B01 | 09013687 | OK |
| sst Factuur 26230303.pdf | 1249.91 | 26230303 | 2026-02-13 | NL817018116B01 | 17159422 | OK |
| Tegeka Factuur93557.pdf | 19880.86 | 93557 | 2026-03-18 | None | None | REGRESSION |
| Tibuplast 571223.pdf | 1034.61 | 571223 | 2026-02-17 | None | None | REGRESSION |
| Tilmar Factuur Tilmar 20260923.pdf | 2148.09 | 20260923 | 2026-03-12 | None | None | REGRESSION |
| Tu 636671785.PDF | 802.82 | 636671785 | 2026-01-06 | NL004502863B01 | 33235014 | OK |
| Ubbink INV_SIN_10567557_101900683_Origineel_0_M.pdf | 703.39 | SIN/10567557 | 2026-02-26 | NL001204907B01 | 09036422 | OK |
| Van den Borne Factuur_4126VF01369.PDF | 99.22 | 4126VF01369 | 2026-02-09 | NL007561726B01 | 17054352 | OK |
| Van Gestel dakbedekking VF260027.pdf | 232.93 | VF260027 | 2026-02-12 | NL860132894B01 | 75077558 | OK |
| van Walraven Factuur_801083_VP601987.pdf | 1410.07 | VP601987 | 2026-03-17 | NL009432395B01 | 16037183 | OK |
| vd donk Factuur 25041 Wouter Duister.pdf | 442.53 | 25041 | 2025-12-31 | NL148658982B01 | 17158094 | OK |
| Vent axia 26801599.PDF | 1246.69 | 26801599 | 2026-02-05 | NL807521942B01 | 17110442 | OK |
| Venttrade Factuur_1100_220_10020159.pdf | 605.0 | 1100/220/10020159 | 2026-01-14 | NL808406115B01 | 13043168 | OK |
| Vermetten Invoice 260800029.pdf | 64.86 | 260800029 | 2026-01-27 | NL857963661B01 | 69675171 | OK |
| VT accountants Factuur_26300023.pdf | 327.31 | 26300023 | 2026-02-01 | None | None | OK |
| Vt Factuur_263000189.pdf | 327.31 | 263000189 | 2026-04-01 | None | None | OK |
| vte Verkoopcreditnota VCR2600003+.pdf | 33.0 | VCR2600003 | 2026-01-13 | NL809101178B01 | None | OK |
| VTE Verkoopfactuur VF2600048+.pdf | 245.15 | VF2600048 | 2026-01-07 | NL809101178B01 | 09114244 | OK |
| Wasco 8714252002430_5660148.pdf | 65.51 | 5660148 | 2026-01-09 | NR08055426 | 08055426 | OK |
| Wavin Factuur 7012239207.pdf | 8.78 | 7012239207 | 2026-01-16 | NL813771213B01 | 05025930 | OK |
| Wentzel Verkoopfactuur_VF00269858.pdf | 5420.05 | VF00269858 | 2026-03-18 | NL808310288B01 | 34116979 | OK |
| Zettex Verkoopfactuur V012600089.pdf | 76.45 | V012600089 | 2026-01-09 | None | None | OK |

- Fully correct (amount + invoice_number + invoice_date): **34/34 (100.0%)**
- Partial: **0/34 (0.0%)**
- Missing core fields: **0/34**

## Regressions vs baseline

- Rems Rechnung-2612498.pdf: kvk_number '60250010' -> None
- Salo VF1750913.pdf: vat_number 'VO2237224ADRES' -> None
- Tegeka Factuur93557.pdf: vat_number 'AB410COPERBASE' -> None
- Tibuplast 571223.pdf: kvk_number '12993485' -> None
- Tilmar Factuur Tilmar 20260923.pdf: vat_number 'CA100METER' -> None
