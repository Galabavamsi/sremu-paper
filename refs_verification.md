# Reference verification record (paper/refs.bib)

WP12, 10 Oct 2026. `tools/check_refs.py` enforces that every key in `refs.bib` has exactly one
row in the table below, that the origin is consistent with `refs_sources/`, and that every
entry carries a `doi`, an arXiv `eprint` or an official URL.

## Sources and rules

- **source**: copied from `refs_sources/refs_verified_2026-10-10.bib`, a byte copy of
  `D:\projects\ris\research\refs_verified.bib` (sha256
  `9afd12b53a56339258de30a282337fd86e6e938099668bd695947c70a002b3ed`). Every entry of that file
  was verified on 2026-10-10 against Crossref/doi.org, the arXiv record, or the official page
  (see its header and `research/related_work.md` section 8). Entries are verbatim except the
  `note` field: in the source it holds verification remarks (some with bare URLs, which
  IEEEtran prints and which broke LaTeX), so in `refs.bib` a note is removed or reduced to
  print-safe content (access date, "Early access", or a code/documentation link in `\url`).
  `check_refs.py` compares author, title, year, doi, eprint, url and howpublished with the
  source.
- **source+augmented**: as above, plus fields added after a new primary-record check (noted).
- **new**: verified for this paper on 2026-10-10 against a primary record (method per row).
- Every entry was re-checked on 2026-10-10 with `tools/verify_refs_online.py` (Crossref REST
  API for DOIs, arXiv API for eprints, HTTP GET for URLs, DOI handle API for non-Crossref
  DOIs). Raw evidence: `refs_verification.json`. Pages that refuse scripted clients were
  checked with a rendering fetch (firecrawl) and are noted per row.
- Rule: no entry is added to `refs.bib` without a row here.

## Findings

1. **SigMF DOI.** The DOI listed in SigMF's `CITATION.cff` (10.5281/zenodo.1418396) resolves to
   "SigMF Release v0.0.1" (2018), not to the current specification (v1.2.6). The entry cites the
   official URL and version instead.
2. **Tang 2022** (`Tang2022mmWave`, IEEE TCOM, arXiv:2101.08607) is the millimeter-wave paper
   with RIS1/RIS2 at 27/33 GHz used by V-TANG; it is a different paper from `Tang2021PathLoss`
   (IEEE TWC 2021). Which tables and figures V-TANG digitizes must be checked against the
   published TCOM version by WP10 (not checked here).
3. **Ellingson** is cited by its PIMRC 2021 DOI; the arXiv version (1912.06759v3) links the same
   DOI. Equation numbers may differ between the two versions.
4. **RISTA white paper** DOI 10.12142/RISTA.202302001 is not a Crossref DOI; the DOI handle API
   confirms that it is registered (responseCode 1).
5. **Linear-time optimal discrete beamforming** (DESIGN 5.13): `Zhang2022OptimalBinary` (binary
   phases solved optimally in linear time, per its abstract) and `Zhao2024OptimalDiscrete` (the
   WCL letter's ref. [6]). A further candidate, Sanchez et al., GLOBECOM 2021
   (10.1109/GLOBECOM46510.2021.9685226), appeared in a Crossref search but its content was not
   checked, so it is not added.
6. **Not added from DESIGN 13.6:** none of the listed items is missing. TSDSI STD 5003 stays as
   in the source (members-only text not accessed; DESIGN 8.9 wording applies).

## Entries of the source file not copied

| Key | Reason |
|---|---|
| `Jiang2022E2E` | not a simulator; the source keeps it only if cited for its content |
| `Xu2021STARRIS` | documents an ANTS misattribution; not used |
| `Ajewole2012FMNiger` | ANTS FM-coverage context; not used |
| `Ali2014Wearable` | ANTS FM-antenna context; not used |
| `Petrov2008ME` | ANTS FM-antenna context; not used |
| `Xing2005CFO` | content mismatch noted in the source; Kay1989Freq is the closer reference |
| `Toor2017Survey` | generic network-simulator survey; not used |

## Per-entry record

| Key | Origin | Identifier | Verification (2026-10-10) |
|---|---|---|---|
| `ITU2023M2160` | source | https://www.itu.int/rec/R-REC-M.2160-0-202311-I | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `DiRenzo2020SRE` | source | doi:10.1109/JSAC.2020.3007211 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2020 -> match. |
| `WuZhang2020IRS` | source | doi:10.1109/MCOM.001.1900107 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2020 -> match. |
| `Basar2019Access` | source | doi:10.1109/ACCESS.2019.2935192 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2019 -> match. |
| `Gkonis2020Sim5G` | source | doi:10.3390/electronics9030468 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2020 -> match. |
| `Gomez2023Survey` | source | doi:10.1016/j.comnet.2023.110054 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `ETSI_GR_RIS001_v121` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/001/01.02.01_60/gr_ris001v010201p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. firecrawl PDF parse of page 1 (HTTP 200): 'ETSI GR RIS 001 V1.2.1 (2025-02)', title matches. |
| `ETSI_GR_RIS002_v121` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/002/01.02.01_60/gr_ris002v010201p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. firecrawl PDF parse of page 1 (HTTP 200): 'ETSI GR RIS 002 V1.2.1 (2025-02)', title matches. |
| `ETSI_GR_RIS003_v121` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/003/01.02.01_60/gr_ris003v010201p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. firecrawl PDF parse of page 1 (HTTP 200): 'ETSI GR RIS 003 V1.2.1 (2025-02)', title matches. |
| `ETSI_GR_RIS004` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/004/01.01.01_60/gr_ris004v010101p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `ETSI_GR_RIS005` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/005/01.01.01_60/gr_ris005v010101p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `ETSI_GR_RIS006` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/006/01.01.01_60/gr_ris006v010101p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `ETSI_GR_RIS007` | source | https://www.etsi.org/deliver/etsi_gr/RIS/001_099/007/01.01.01_60/gr_ris007v010101p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `ETSI_GS_RIS008` | source | https://www.etsi.org/deliver/etsi_gs/RIS/001_099/008/01.01.01_60/gs_ris008v010101p.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. firecrawl PDF parse of pages 1-2 (HTTP 200): 'ETSI GS RIS 008 V1.1.1 (2026-02)', Group Specification, title matches (39 pages). |
| `TSDSI_STD5003` | source | https://tsdsi.in/published-standards/ | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `TSDSI_TR6060` | source | https://tsdsi.in/tr/ | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `TGPP_TR38867` | source | https://www.3gpp.org/dynareport/38867.htm | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `TGPP_Rel20` | source | https://www.3gpp.org/specifications-technologies/releases/release-20 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `FuTURE2025RISStd` | source | https://www.risalliance.com/wp-content/uploads/2025/05/White-Paper-on-Potential-Standardization-Work-for-Reconfigurable-Intelligent-Surface.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `RISTA2023WP` | source | doi:10.12142/RISTA.202302001 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: DOI handle API responseCode 1 (not a Crossref DOI) -> match. |
| `SixGBRICKS_D32` | source | https://6g-bricks.eu/wp-content/uploads/2024/03/D3.2_Initial_report_enablers_RIS_PaaS_FINAL.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `SixGBRICKS_D34` | source | https://6g-bricks.eu/wp-content/uploads/2025/02/D3.4_Final_report_enablers_RIS_PaaS_v1.0.pdf | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Mishra2025WCL` | source | doi:10.1109/LWC.2025.3535557 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. Crossref record re-read: title, authors (Mishra, Kherani), IEEE WCL 14(4):1139-1143, Apr 2025. |
| `Mishra2025NCC` | source | doi:10.1109/NCC63735.2025.10983019 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. Crossref record re-read: title, authors, NCC 2025 pp. 1-6 (issued 2025-03-06). |
| `Khan2025INDICON` | source | doi:10.1109/INDICON68490.2025.11392908 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Kaur2026SLR` | source | doi:10.1016/j.adhoc.2026.104330 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2026 -> match. |
| `Basar2020SimRIS` | source | doi:10.1109/LATINCOM50620.2020.9282349 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2020 -> match. |
| `Basar2021TCOM` | source | doi:10.1109/TCOMM.2021.3113954 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021 -> match. |
| `SimRISRepo` | source | https://github.com/ifyildirim/SimRIS-Channel-Simulator | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Burtakov2023QRIS` | source | doi:10.1109/ACCESS.2023.3306954 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `QRISPage` | source | http://wnlab.ru/ris-quadriga/ | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Jaeckel2014QuaDRiGa` | source | doi:10.1109/TAP.2014.2310220 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2014 -> match. |
| `Habib2023NYUSIMRIS` | source | doi:10.1109/EuCNC/6GSummit58263.2023.10188290 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Sun2017NYUSIM` | source | doi:10.1109/ICC.2017.7996792 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2017 -> match. |
| `Bjornson2022SPM` | source | doi:10.1109/MSP.2021.3130549 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `GarciaSanchez2023RISSTAR` | source | doi:10.1109/INFOCOM53939.2023.10228868 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Hao2023ViennaRIS` | source | doi:10.1109/EuCNC/6GSummit58263.2023.10188354 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Sihlbom2023SLS` | source | doi:10.1109/MWC.015.2100668 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Hoydis2022Sionna` | source | doi:10.48550/arXiv.2203.11854<br>arXiv:2203.11854 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2203.11854v2 title ratio 1.0 -> match. |
| `Hoydis2023SionnaRT` | source | doi:10.1109/GCWkshps58843.2023.10465179 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `AitAoudia2025SionnaRTReport` | source | doi:10.48550/arXiv.2504.21719<br>arXiv:2504.21719 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2504.21719v3 title ratio 1.0 -> match. |
| `SionnaRTReleases` | source | https://github.com/NVlabs/sionna-rt/releases | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `DegliEsposti2022Reradiation` | source | doi:10.1109/TAP.2022.3149660 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `Vitucci2023RayRIS` | source | doi:10.36227/techrxiv.23041643 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `DeJong2022EES` | source | doi:10.1109/TAP.2021.3137191 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `RemcomEES` | source | https://www.remcom.com/wireless-insite-em-propagation-software/engineered-electromagnetic-surfaces-ees | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Habib2025CloudRT` | source | doi:10.23919/EuCAP63536.2025.10999995 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Sandh2024RTRIS` | source | doi:10.48550/arXiv.2402.13034<br>arXiv:2402.13034 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2402.13034v1 title ratio 1.0 -> match. |
| `Herold2023RISRayTracing` | source | doi:10.1109/IRMMW-THz57677.2023.10299214 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Faqiri2023PhysFad` | source | doi:10.1109/TWC.2022.3196834 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Gradoni2021MutualCoupling` | source | doi:10.1109/LWC.2021.3050826 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021 -> match. |
| `Papadopoulos2022OpenPlatform` | source | doi:10.23919/CNSM55787.2022.9965098 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `Tang2021PathLoss` | source | doi:10.1109/TWC.2020.3024887 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021 -> match. |
| `Ellingson2021PathLoss` | source+augmented | doi:10.1109/PIMRC50174.2021.9569465<br>arXiv:1912.06759 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021; arXiv 1912.06759v3 title ratio 1.0 -> match. Crossref record of the PIMRC DOI re-read; arXiv API record 1912.06759v3 (updated 2021-11-23) carries journal_ref PIMRC 2021 and the same DOI, so the eprint was added. Equation numbers may differ between arXiv v3 and the PIMRC version; quote the PIMRC numbering. |
| `MathWorksRectangularRIS` | source | https://www.mathworks.com/help/phased/ref/phased.rectangularris-system-object.html | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 403 -> unreachable. Scripted GET is refused (HTTP 403). firecrawl render (HTTP 200): page 'phased.RectangularRIS', 'Introduced in R2025a', UnitCell may be any Phased Array or Antenna Toolbox element; RISReflector example reflects a sample vector. |
| `MathWorksRISCDL` | source | https://www.mathworks.com/help/5g/ug/model-reconfigurable-intelligent-surfaces-with-cdl-channels.html | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 403 -> unreachable. Scripted GET is refused (HTTP 403). firecrawl render (HTTP 200): 'Model Reconfigurable Intelligent Surfaces with CDL Channels', 'Since R2024a', cites ETSI GR RIS 003. |
| `Segata2024CoopeRIS` | source | doi:10.1016/j.comnet.2024.110443 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2024 -> match. |
| `Pagin2022IRSAF` | source | doi:10.1109/MedComNet55087.2022.9810370 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `Grieco2024IoDSimIRS` | source | doi:10.1109/TVT.2023.3342298 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2024 -> match. |
| `Zubow2024Ns3Sionna` | source | doi:10.48550/arXiv.2412.20524<br>arXiv:2412.20524 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2412.20524v1 title ratio 1.0 -> match. |
| `Heimann2021RISVehicular` | source | doi:10.1145/3479239.3485700 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021 -> match. |
| `ElGhalbzouri2025GoSimRIS` | source | doi:10.1109/LNET.2025.3527683 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Kayraklik2026ORIX` | source | doi:10.1109/MWC.2026.3701653 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2026 -> match. |
| `Sahin2025RISmeetsORAN` | source | doi:10.1109/EuCNC/6GSummit63408.2025.11037049 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Gurses2026ACHEM` | source | doi:10.48550/arXiv.2604.04742<br>arXiv:2604.04742 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2604.04742v1 title ratio 1.0 -> match. arXiv API record 2604.04742v1 re-read (published 2026-04-06, eess.SP, authors Gurses and Sichitiu). |
| `Iye2025OWDT` | source | doi:10.1109/ACCESS.2025.3619105 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Mastronarde2022RFSITL` | source | doi:10.1109/WoWMoM54355.2022.00076 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2022 -> match. |
| `Bonati2021Colosseum` | source | doi:10.1109/DySPAN53946.2021.9677430 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2021 -> match. |
| `Polese2024ColosseumDT` | source | doi:10.1109/OJCOMS.2024.3447472 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2024 -> match. |
| `Tsampazi2025RISColosseum` | source | doi:10.1109/TVT.2025.3563184 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `Tsampazi2025ORISing` | source | doi:10.1109/INFOCOMWKSHPS65812.2025.11152828 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2025 -> match. |
| `OAIrfsim` | source | https://github.com/openairinterface/openairinterface5g/blob/develop/radio/rfsimulator/README.md | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `srsRANZMQ` | source | https://docs.srsran.com/projects/4g/en/next/app_notes/source/zeromq/source/ | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `AODTRepo` | source | https://github.com/NVIDIA/aerial-omniverse-digital-twin | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `OpenRISRepo` | source | https://github.com/jimrains/OpenRIS | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Heinrichs2023OpenSourceRIS` | source | doi:10.1109/ISAP57493.2023.10389095 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2023 -> match. |
| `Chen2026OpenRIS` | source | doi:10.48550/arXiv.2608.09352<br>arXiv:2608.09352 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2608.09352v1 title ratio 1.0 -> match. |
| `Rossanese2024OpenMeas` | source | doi:10.1109/MIC.2024.3376772 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2024 -> match. |
| `Euchner2025ESPARGOS` | source | doi:10.48550/arXiv.2502.09405<br>arXiv:2502.09405 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2502.09405v1 title ratio 1.0 -> match. |
| `Karakelle2026DTRIS` | source | doi:10.48550/arXiv.2604.17929<br>arXiv:2604.17929 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: arXiv 2604.17929v1 title ratio 1.0 -> match. |
| `Lichtman_PySDR` | source | https://pysdr.org | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP URLError -> unreachable. Scripted GET failed (URLError). firecrawl render (HTTP 200): 'PySDR: A Guide to SDR and DSP using Python', 'By Dr. Marc Lichtman'. |
| `EttusUHDPython` | source | https://kb.ettus.com/UHD_Python_API | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP URLError -> unreachable. Scripted GET failed (URLError). firecrawl render (HTTP 200): 'UHD Python API - Ettus Knowledge Base'. |
| `Kay1989Freq` | source | doi:10.1109/29.45547 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 1989 -> match. Crossref record re-read (IEEE TASSP 37(12):1987-1990, 1989). |
| `Moose1994CFO` | source | doi:10.1109/26.328961 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 1994 -> match. |
| `Bettstetter2003RWP` | source | doi:10.1109/TMC.2003.1233531 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2003 -> match. Crossref record re-read (IEEE TMC 2(3):257-269, Jul 2003). |
| `Nain2005RD` | source | doi:10.1109/INFCOM.2005.1498468 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year None -> match. |
| `Ariyakhajorn2006GM` | source | doi:10.1109/ISCIT.2006.339866 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2006 -> match. |
| `Chiang2004RW` | source | doi:10.1109/TVT.2004.823544 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 2004 -> match. |
| `VanDerPol1946FM` | source | doi:10.1049/ji-3-2.1946.0024 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: Crossref title ratio 1.0, year 1946 -> match. |
| `ITU_BS412` | source | https://www.itu.int/rec/R-REC-BS.412-9-199812-I/en | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `ITU_P1546` | source | https://www.itu.int/rec/R-REC-P.1546 | refs_verified.bib (primary record fetched 2026-10-10). Script re-check: HTTP 200 -> match. |
| `Tang2022mmWave` | new | doi:10.1109/TCOMM.2022.3193400<br>arXiv:2101.08607 | arXiv API record 2101.08607v2 (title, 9 authors; arXiv DOI field = 10.1109/TCOMM.2022.3193400); Crossref record of that DOI (IEEE TCOM 70(9):6259-6276, Sep 2022); doi.org 302 to IEEE Xplore 9837936. Script re-check: Crossref title ratio 1.0, year 2022; arXiv 2101.08607v2 title ratio 1.0 -> match. |
| `Kumar2026TRACE` | new | doi:10.48550/arXiv.2608.11798<br>arXiv:2608.11798 | arXiv API record 2608.11798v1 (published 2026-08-12, eess.SP, 4 authors); 10.48550 DOI resolves to arxiv.org/abs/2608.11798; facts used in the text checked against the local full text data/papers/ext/trace_2608.11798.txt (UDP sockets; i.i.d. Rayleigh; 5.2 ms control-plane RTT over 500 tracking intervals; code 'upon acceptance'). Script re-check: arXiv 2608.11798v1 title ratio 1.0 -> match. |
| `WuZhang2020Discrete` | new | doi:10.1109/TCOMM.2019.2958916<br>arXiv:1906.03165 | arXiv API record 1906.03165v4; Crossref record 10.1109/TCOMM.2019.2958916 (IEEE TCOM 68(3):1838-1851, Mar 2020). Title match 1.0 after normalization. Script re-check: Crossref title ratio 1.0, year 2020; arXiv 1906.03165v4 title ratio 1.0 -> match. |
| `Abeywickrama2020Practical` | new | doi:10.1109/TCOMM.2020.3001125<br>arXiv:2002.10112 | arXiv API record 2002.10112v1; Crossref record 10.1109/TCOMM.2020.3001125 (IEEE TCOM 68(9):5849-5863, Sep 2020). Script re-check: Crossref title ratio 1.0, year 2020; arXiv 2002.10112v1 title ratio 1.0 -> match. |
| `Matthiesen2021Mobility` | new | doi:10.1109/LWC.2020.3024781<br>arXiv:2006.06991 | arXiv API record 2006.06991v3 (arXiv DOI field = 10.1109/LWC.2020.3024781); Crossref record (IEEE WCL 10(2):216-220, Feb 2021). Script re-check: Crossref title ratio 1.0, year 2021; arXiv 2006.06991v3 title ratio 1.0 -> match. |
| `Laakso1996FracDelay` | new | doi:10.1109/79.482137 | Crossref record 10.1109/79.482137 (IEEE SPM 13(1):30-60, 1996); title kept verbatim from Crossref. Crossref spells the second author 'V. Valimaki' without diacritics; the bib uses V{\"a}lim{\"a}ki. Script re-check: Crossref title ratio 1.0, year 1996 -> match. |
| `Zhao2024OptimalDiscrete` | new | doi:10.1109/LWC.2023.3331232 | The Mishra-Kherani WCL letter's ref. [6] (local text data/papers/mishra2025_wcl_binary_ris_mobility.txt, line 621). Crossref bibliographic search returned 10.1109/LWC.2023.3331232 as top hit with identical title/authors (IEEE WCL 13(2):427-431, Feb 2024); doi.org 302 to IEEE Xplore 10311572. Script re-check: Crossref title ratio 1.0, year 2024 -> match. |
| `Zhang2022OptimalBinary` | new | doi:10.1109/JSTSP.2022.3176479<br>arXiv:2112.02260 | Crossref record 10.1109/JSTSP.2022.3176479 (IEEE JSTSP 16(5):967-979, Aug 2022); arXiv API record 2112.02260v1 with the same DOI. Abstract: binary phase beamforming solved optimally in linear time (the 'linear-time optimal discrete beamforming' reference of DESIGN 5.13). Script re-check: Crossref title ratio 1.0, year 2022; arXiv 2112.02260v1 title ratio 1.0 -> match. |
| `SigMF` | new | https://sigmf.org | Spec page https://sigmf.org/index.html (firecrawl, HTTP 200): 'SigMF Specification Version v1.2.6', CC-BY-SA. Repository CITATION.cff: author 'The GNU Radio Foundation, Inc.', version 1.2.6, date-released 2025-05-16. The DOI in that CITATION.cff (10.5281/zenodo.1418396) resolves to Zenodo record 'SigMF Release v0.0.1' (2018), so it is NOT used for v1.2.6; the entry cites the official URL and version, as the spec's own 'Citing SigMF' section recommends. Script re-check: HTTP 200 -> match. |
| `UHDManual` | new | https://files.ettus.com/manual/ | https://files.ettus.com/manual/ (firecrawl, HTTP 200; urllib HTTP 200 on re-run): page 'USRP Hardware Driver and Device Manual', 'Version: 4.11.0.0'. No release year stated on the page; none given in the entry. Script re-check: HTTP 200 -> match. |
