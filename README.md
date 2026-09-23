# Standardized Emission and Waste Inventories (StEWI)
[![DOI - 10.3390/app12073447](https://img.shields.io/badge/DOI-10.3390%2Fapp12073447-blue)](https://doi.org/10.3390/app12073447)
[![DOI](https://zenodo.org/badge/101654522.svg)](https://zenodo.org/doi/10.5281/zenodo.1342761)
[![DOI - 10.23719/1526441](https://img.shields.io/badge/v1.0%20DataProducts-10.23719%2F1526441-blue)](https://doi.org/10.23719/1526441)
[![build](https://github.com/USEPA/standardizedinventories/actions/workflows/python-package.yml/badge.svg)](https://github.com/USEPA/standardizedinventories/actions/workflows/python-package.yml)

StEWI is a collection of Python modules that provide processed USEPA facility-based emission and waste generation inventory data in standard tabular formats.
 The standard outputs may be further aggregated or filtered based on given criteria, and can be combined based on common facility and flows
  across the inventories.

StEWI consists of a core module, `stewi`, that digests and provides the USEPA inventory data in standard formats. Two matcher modules, the `facilitymatcher`
and `chemicalmatcher`, provide commons IDs for facilities and flows across inventories, which is used by the `stewicombo` module
to combine the data, and optionally remove overlaps and remove double counting of groups of chemicals based on user preferences.

StEWI v1 was peer-reviewed internally at USEPA and externally through _Applied Sciences_.
An article describing StEWI was published in a special issue of Applied Sciences: [Advanced Data Engineering for Life Cycle Applications](https://doi.org/10.3390/app12073447).

## USEPA Inventories Covered By Data Reporting Year (current version)

|Source|2011|2012|2013|2014|2015|2016|2017|2018|2019|2020|2021|2022|2023|2024|
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
|[Discharge Monitoring Reports](https://echo.epa.gov/tools/data-downloads/icis-npdes-dmr-and-limit-data-set)* |x|x|x|x|x|x|x|x|x|x|x|x|x|
|[Greenhouse Gas Reporting Program](https://www.epa.gov/ghgreporting) |x|x|x|x|x|x|x|x|x|x|x|x|x|
|[Emissions & Generation Resource Integrated Database](https://www.epa.gov/energy/emissions-generation-resource-integrated-database-egrid) | | | |x| |x| |x|x|x|x|x|x|x|
|[National Emissions Inventory](https://www.epa.gov/air-emissions-inventories/national-emissions-inventory-nei)** |x|i|i|x|i|i|x|i|i|x|i|i|x| |
|[RCRA Biennial Report](https://www.epa.gov/hwgenerators/biennial-hazardous-waste-report)* |x| |x| |x| |x| |x| |x| |x|
|[Toxic Release Inventory](https://www.epa.gov/toxics-release-inventory-tri-program)* |x|x|x|x|x|x|x|x|x|x|x|x|x|

*Earlier data exist and are accessible but have not been validated

**Only point sources included at this time from NEI; _i_ interim years between triennial releases, accessed through the Emissions Inventory System. Note 2022 dataset is not able to validated at this time.

## Standard output formats

The core `stewi` module produces the following output formats:

[Flow-By-Facility](./format%20specs/FlowByFacility.md): Each row represents the total amount of release or waste of a single type in a given year from the given facility.

[Flow-By-Process](./format%20specs/FlowByProcess.md): Each row represents the total amount of release or waste of a single type in a given year from a specific process within the given facility.
Applicable only to NEI and GHGRP.

[Activity-By-Process](./format%20specs/ActivityByProcess.md): Each row gives the activity a process's emissions were calculated from - fuel burned, material processed - in the source's own units.
Applicable only to NEI.

[Facility](./format%20specs/Facility.md): Each row represents a unique facility in a given inventory and given year

[Flow](./format%20specs/Flow.md): Each row represents a unique flow (substance or waste) in a given inventory and given year

The `chemicalmatcher` module produces:

[Chemical Matches](./format%20specs/ChemicalMatches.md): Each row provides a common identifier for an inventory flow chemical

The `facilitymatcher` module produces:

[Facility Matches](./format%20specs/FacilityMatches.md): Each row provides a common identifier for an inventory facility

The `stewicombo` module produces:

[Flow-By-Facility Combined](./format%20specs/FlowByFacilityCombo.md): Analagous to the flowbyfacility, with chemical and facilitymatches added

## Data Processing

The following describes details related to dataset access, processing, and validation

### DMR

Processing of the DMR uses the custom search option of the [Water Pollutant Loading Tool](https://echo.epa.gov/trends/loading-tool/get-data/custom-search/) with the following parameters:
- Parameter grouping: On - applies a parameter grouping function to avoid double-counting loads for pollutant parameters that represent the same pollutant
- Detection limit: Half - set all non-detects to ½ the detection limit
- Estimation: On - estimates loads when monitoring data are not reported for one or more monitoring periods in a reporting year
- Nutrient Aggregation: On - Nitrogen and Phosphorous flows are converted to N and P equivalents

For validation, the sum of facility releases (excluding N & P) are compared against reported state totals.
Some validation issues are expected due to differences in default parameters used by the water pollutant loading tool for calculating state totals.

### eGRID

eGRID data for 2014-2023 are sourced from EPA's [eGRID](https://www.epa.gov/egrid) site.
The 2024 inventory uses the workbook from Cornerstone on [Zenodo](https://zenodo.org/records/18968658).
For validation, the sum of facility releases are compared against reported U.S. totals by flow.

### GHGRP

GHGRP data for the reporting years EPA has published (2011-2023) are sourced from EPA's [Envirofacts API](https://enviro.epa.gov/),
together with the data summary spreadsheets and the aggregated subpart spreadsheets from EPA's [Data Sets](https://www.epa.gov/ghgreporting/data-sets) page.
For validation, the sum of facility releases by subpart are compared against reported U.S. totals by subpart and flow.
The validation of some flows (HFC, HFE, and PFCs) are reported in carbon dioxide equivalents.
Mixed reporting of these flows in the source data in units of mass or carbon dioxide equivalents results in validation issues.

EPA has not published reporting year 2024. That year is built instead from a local
archive of the same Envirofacts views, declared under `'2024'` in `config.yaml`.
The archive is not redistributable and is never downloaded: pass its path with
`-A/--Archive`, set `GHGRP_EF_VIEWS_ARCHIVE`, or put it in the `GHGRP Data Files`
directory. Two differences follow from the source:

- Facilities come from the `V_GHG_EMITTER_FACILITIES` view rather than the
  per-year data summary spreadsheet. The view reports EPA's facility location
  where the spreadsheet reports the facility's own, so latitude, longitude and
  county differ for some facilities, and `Zip` keeps a leading zero.
- Subparts E, BB, CC, L and O are absent. They reach StEWI only through the
  aggregated subpart spreadsheets, which stop at the last published year. In 2024
  they are 2.2 Mt CO2e of 2,564 Mt, 0.09% of the programme total.

### NEI

NEI data are downloaded from the EPA Emissions Inventory System (EIS) Gateway and hosted on EPA [Data Commons](https://dmap-data-commons-ord.s3.amazonaws.com/index.html?prefix=#stewi/) for access by StEWI.
For validation, the sum of facility releases are compared against reported totals by flow.

The point exports also carry the parameter each emission was calculated from, which is
written out as [Activity-By-Process](./format%20specs/ActivityByProcess.md). The 2011 and
2014 exports use a narrower layout without those fields, so no activity file is written for
those two years.

### RCRAInfo

RCRAInfo data are sourced from the [Public Data Files](https://rcrapublic.epa.gov/rcrainfoweb/action/main-menu/view)
For validation, the sum of facility waste generation are compared against reported state totals as calculated for the National Biennial Report.

### TRI

TRI data are sourced from the [Basic Plus Data files](https://www.epa.gov/toxics-release-inventory-tri-program/tri-data-and-tools)
For validation, the sum of facility releases are compared to national totals by flow from the TRI Explorer.

### Facility matching

`facilitymatcher` is built from the [FRS combined national
file](https://www.epa.gov/frs/epa-state-combined-csv-download-files), a 1.3 GB
zip that EPA replaces in place at one URL. It is streamed to
`FRS Data Files/national_combined.zip` and kept, so reading a second file out of
it does not download it again. To build from a copy already on hand — a pinned
snapshot, or a machine with no route to the FRS host — put it at that path, pass
it to `download_extract_FRS_combined_national(file, archive=...)`, or set
`FRS_COMBINED_ARCHIVE`. FRS publishes no version string, so `SourceVersion` in
the metadata is the build date read from the zip's own member timestamps, which
travels with a copy.

#### One site, two registry records

The match list is the FRS bridge: a program identifier matched to whatever
`REGISTRY_ID` FRS filed it under. Where FRS has filed two programs at one site
under two registry records, the bridge links neither to the other, and anything
that deduplicates on `FRS_ID` sees two facilities. FRS leaves no direct evidence
of the split — a program identifier appears under exactly one registry record,
with no exceptions in the September 2026 national file — so `colocation.py`
recognises them from the facility attributes.

Three rules, unioned; each requires the same state and a postcode that agrees
wherever both records carry one:

| rule | source file | test |
|---|---|---|
| `address` | `NATIONAL_FACILITY_FILE` | same street address, coordinates within 5 km where both have them, and either a shared name token or the same NAICS prefix |
| `name` | `NATIONAL_FACILITY_FILE` | same facility name, coordinates present on both sides and within 1 km |
| `program address` | `NATIONAL_PROGRAM_FILE` | same street address as **each program itself reported** it, corroborated as above |

The thresholds are graded against the 3,402 GHGRP/NEI facility pairs FRS *does*
link in 2022, using each program's own reported attributes: the two programs'
coordinates for one site differ by a median of 184 m and agree within 1 km for
90% of pairs; the normalised street address is identical for 77%; the normalised
name for only 33%. The corroboration is what keeps a **tenant** at a host site —
a slag processor at a steel mill, an industrial gas plant at a refinery — from
being folded into its host: true pairs share a name token 91.8% of the time and
agree on NAICS 94.9%, tenant pairs 38.4% and 47.1%. Requiring either keeps 76.0%
of the recoverable pairs at 97.1% precision, against 77.1% at 95.0% with no
corroboration.

On the September 2026 file this folds 188,835 registry records onto another over
140,323 sites, and takes the share of GHGRP registry records sharing an `FRS_ID`
with NEI from 36.5% to 52.5%, and with TRI from 27.7% to 33.8%. The surviving
identifier is always a real FRS registry record — the one of the group carrying
the most program registrations, ties broken by the lowest identifier so a
rebuild repeats. The same fold is applied to `FRS_NAICSforStEWI`. Set
`colocation: enabled: false` in `facilitymatcher/config.yaml` for the bridge
alone.

## Combined Inventories

`stewicombo` module combines inventory data from within and across selected inventories by matching facilities in the [Facility Registry Service](https://www.epa.gov/frs) and
chemical flows using the [Substance Registry Service](https://sor.epa.gov/sor_internet/registry/substreg/LandingPage.do).
If the `remove_overlap` parameter is set to True (default), `stewicombo` combines records using the following default logic:
- Records that share a common compartment, SRS ID and FRS ID _within_ an inventory are summed.
- Records that share a common compartment, SRS ID and FRS ID _across_ an inventory are assessed by compartment preference (see `INVENTORY_PREFERENCE_BY_COMPARTMENT`).
- Additional steps are taken to avoid overlap of:
    - nutrient flow releases to water between the TRI and DMR
    - particulate matter releases to air reflecting PM < 10 and PM < 2.5 in the NEI
    - [Volatile Organic Compound (VOC)](https://github.com/USEPA/standardizedinventories/blob/master/stewicombo/data/VOC_SRS_IDs.csv) releases to air for individually reported VOCs and grouped VOCs


## Installation Instructions

Install a release directly from github using pip. From a command line interface, run:
> pip install git+https://github.com/USEPA/standardizedinventories.git@v1.1.0#egg=StEWI

where you can replace 'v1.1.0' with the version you wish to use under [Releases](https://github.com/USEPA/standardizedinventories/releases).

Alternatively, to install from the most current point on the repository:
```
git clone https://github.com/USEPA/standardizedinventories.git
cd standardizedinventories
pip install . # or pip install -e . for devs
```

### Development setup

The repository does not include a virtual environment. Use a dedicated venv (or conda env)
so dependencies—especially [esupy](https://github.com/USEPA/esupy), installed from GitHub
via `setup.py`—are not mixed with system Python.

From the repository root:

```
python -m venv .venv
```

Activate the environment:

- Windows (PowerShell): `.\.venv\Scripts\Activate.ps1`
- Windows (cmd): `.venv\Scripts\activate.bat`
- Windows (git bash): `.venv\Scripts\activate`
- macOS/Linux: `source .venv/bin/activate`

Then install StEWI in editable mode and upgrade build tools:

```
python -m pip install --upgrade pip setuptools wheel
pip install -e .
```

Run the default test suite (same scope as CI):

```
pytest -m "not (combined or inventory)"
```

Inventory generation tests (`-m inventory`) and combined-inventory tests (`-m combined`)
download large source files and are skipped in routine CI; run them only when needed.

**Generated outputs** (downloaded source workbooks, parquet inventories, validation results)
are written under the esupy local data directory, not necessarily inside the clone. On
Windows this is typically `%LOCALAPPDATA%\stewi\` (for example `eGRID Data Files\`,
`flowbyfacility\`). Static reference files in the repository remain under `stewi/data/`.

### Secondary Context Installation Steps
In order to enable calculation and assignment of urban/rural secondary contexts, please refer to
[esupy's README.md](https://github.com/USEPA/esupy/tree/main#installation-instructions-for-optional-geospatial-packages) for installation instructions,
which may require a copy of the [`env_sec_ctxt.yaml`](https://github.com/USEPA/standardizedinventories/blob/master/env_sec_ctxt.yaml) file included here.

## Data Products
Output of StEWI can be accessed for selected releases without having to run StEWI.
See the [Data Product Links](https://github.com/USEPA/standardizedinventories/wiki/DataProductLinks) page for direct links to StEWI output files in Apache parquet format.

## Wiki
See the [Wiki](https://github.com/USEPA/standardizedinventories/wiki) for instructions on installation and use and for
citation and contact information.
