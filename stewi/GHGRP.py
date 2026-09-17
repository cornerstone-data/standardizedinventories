# GHGRP.py (stewi)
# !/usr/bin/env python3
# coding=utf-8
"""
Imports GHGRP data and processes to Standardized EPA output format.
This file requires parameters be passed like:

    Option -Y Year

Option:
    A - for downloading and processing GHGRP data from web and saving locally
    B - for generating inventory files for StEWI:
         - flowbysubpart
         - flowbyfacility
         - flows
         - facilities
        and validating flowbyfacility against national totals
    C - for downloading national totals for validation

Year:
    2011-2024

Reporting years EPA has published are built from the Envirofacts API. A year
EPA has not published - 2024 - is built from a local archive of the same
Envirofacts views, declared per year in config.yaml. Pass its path with
-A/--Archive or set GHGRP_EF_VIEWS_ARCHIVE; it is not redistributable, so it is
never downloaded.

Models with tables available at:
    https://www.epa.gov/enviro/greenhouse-gas-model
Envirofacts web services documentation can be found at:
    https://www.epa.gov/enviro/web-services
"""

import pandas as pd
import numpy as np
import os
import time
import argparse
import warnings
import zipfile
import io
import urllib
import urllib3
from pathlib import Path
from requests.exceptions import HTTPError
from xml.dom import minidom
from xml.parsers.expat import ExpatError

from esupy.processed_data_mgmt import read_source_metadata
from esupy.remote import make_url_request
from stewi.globals import write_metadata, compile_source_metadata, aggregate, \
    DATA_PATH, get_reliability_table_for_source, set_stewi_meta, config,\
    store_inventory, paths, log
from stewi.validate import update_validationsets_sources, validate_inventory,\
    write_validation_result
from stewi.formats import StewiFormat
import stewi.exceptions


_config = config()['databases']['GHGRP']
GHGRP_DATA_PATH = DATA_PATH / 'GHGRP'
EXT_DIR = 'GHGRP Data Files'
OUTPUT_PATH = paths.local_path / EXT_DIR

# Flow codes that are reported in validation in CO2e
flows_CO2e = ['PFC', 'HFC', 'Other', 'Very_Short', 'HFE', 'Other_Full']

# define GWPs
# (these values are from IPCC's AR4, which is consistent with GHGRP methodology)
CH4GWP = 25
N2OGWP = 298
HFC23GWP = 14800

# define column groupings
ghgrp_cols = pd.read_csv(GHGRP_DATA_PATH.joinpath('ghgrp_columns.csv'))
name_cols = list(ghgrp_cols[ghgrp_cols['ghg_name'] == 1]['column_name'])
alias_cols = list(ghgrp_cols[ghgrp_cols['ghg_alias'] == 1]['column_name'])
quantity_cols = list(ghgrp_cols[ghgrp_cols['ghg_quantity'] == 1]['column_name'])
co2_cols = list(ghgrp_cols[ghgrp_cols['co2'] == 1]['column_name'])
ch4_cols = list(ghgrp_cols[ghgrp_cols['ch4'] == 1]['column_name'])
n2o_cols = list(ghgrp_cols[ghgrp_cols['n2o'] == 1]['column_name'])
co2e_cols = list(ghgrp_cols[ghgrp_cols['co2e_quantity'] == 1]['column_name'])
subpart_c_cols = list(ghgrp_cols[ghgrp_cols['subpart_c'] == 1]['column_name'])
method_cols = list(ghgrp_cols[ghgrp_cols['method'] == 1]['column_name'])
base_cols = list(ghgrp_cols[ghgrp_cols['base_columns'] == 1]['column_name'])
info_cols = name_cols + quantity_cols + method_cols
group_cols = co2_cols + ch4_cols + n2o_cols
ghg_cols = base_cols + info_cols + group_cols

# Envirofacts view holding one row per facility per reporting year. Stands in
# for the data summary spreadsheets, which EPA publishes only for years it has
# released, when a year is built from an archive.
EF_FACILITIES_TABLE = 'V_GHG_EMITTER_FACILITIES'

# Envirofacts view the national totals validation is built from.
EF_VALIDATION_TABLE = 'V_GHG_EMITTER_SUBPART'

# V_GHG_EMITTER_FACILITIES column -> StEWI facility field. The view carries the
# same facility attributes as the data summary spreadsheets, under EF names.
EF_FACILITY_COLUMNS = {'FACILITY_ID': 'FacilityID',
                       'FACILITY_NAME': 'FacilityName',
                       'ADDRESS1': 'Address',
                       'CITY': 'City',
                       'STATE': 'State',
                       'ZIP': 'Zip',
                       'COUNTY': 'County',
                       'LATITUDE': 'Latitude',
                       'LONGITUDE': 'Longitude',
                       'PRIMARY_NAICS_CODE': 'NAICS',
                       }

# define filepaths for downloaded data
data_summaries_path = OUTPUT_PATH.joinpath(
    f"{_config['most_recent_year']}_data_summary_spreadsheets")
esbb_subparts_path = OUTPUT_PATH.joinpath(_config['esbb_subparts_url']
                                          .rsplit('/', 1)[-1])
lo_subparts_path = OUTPUT_PATH.joinpath(_config['lo_subparts_url']
                                        .rsplit('/', 1)[-1])


class MetaGHGRP:
    def __init__(self):
        self.time = []
        self.filename = []
        self.filetype = []
        self.url = []

    def add(self, time, filename: Path, filetype, url):
        self.time.append(time)
        self.filename.append(str(filename))
        self.filetype.append(filetype)
        self.url.append(url)


def archive_year_config(year):
    """Return the config block for a year built from an archive, else None.

    EPA publishes GHGRP through the Envirofacts API and the data summary
    spreadsheets. A reporting year it has not published is declared in
    config.yaml with an ``archive_file_name``, and is built from a local
    archive of the same Envirofacts views instead.
    """
    year_config = _config.get(str(year))
    if isinstance(year_config, dict) and year_config.get('archive_file_name'):
        return year_config
    return None


def resolve_archive(year, archive=None):
    """Locate the Envirofacts views archive for a year built from an archive.

    Checked in order: the ``archive`` argument, ``$GHGRP_EF_VIEWS_ARCHIVE``,
    then ``archive_file_name`` from config.yaml under :data:`OUTPUT_PATH`. The
    archive is not redistributable and is never downloaded - it has to already
    be on the machine.
    """
    year_config = archive_year_config(year)
    if year_config is None:
        raise stewi.exceptions.InventoryNotAvailableError(
            message=f'GHGRP {year} is not built from an archive')
    candidates = [archive,
                  os.environ.get('GHGRP_EF_VIEWS_ARCHIVE'),
                  OUTPUT_PATH / year_config['archive_file_name']]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise stewi.exceptions.DataNotFoundError(
        message=(f"Envirofacts views archive for GHGRP {year} not found. Put "
                 f"{year_config['archive_file_name']} in {OUTPUT_PATH}, pass "
                 "-A/--Archive, or set GHGRP_EF_VIEWS_ARCHIVE."))


def read_archive_table(archive_path, member, year):
    """Read one Envirofacts view out of the archive, filtered to the year.

    The archive holds every reporting year in one file per view. Everything is
    read as text and written back as text, so that a zero-padded code survives
    staging - inferring types here turns a ZIP of ``07031`` into ``7031`` and a
    ``COUNTY_FIPS`` of ``01117`` into ``1117``. The staged CSV is then read with
    ordinary type inference, exactly as an API download would be.

    Identifiers and the reporting year are the exception: the archive writes
    numeric columns with ten decimal places, and a ``FacilityID`` of
    ``1006069.0`` joins to nothing downstream.
    """
    with zipfile.ZipFile(archive_path) as zip_file:
        with zip_file.open(member) as f:
            df = pd.read_csv(f, low_memory=False, dtype=str)
    df.columns = df.columns.str.upper()
    year_col = next((c for c in ('REPORTING_YEAR', 'YEAR') if c in df.columns),
                    None)
    if year_col is None:
        raise stewi.exceptions.StewiQueryError(
            message=f'{member} carries no reporting year column')
    for col in [c for c in df.columns if c == year_col or c.endswith('_ID')]:
        numeric = pd.to_numeric(df[col], errors='coerce')
        if numeric.isna().sum() == df[col].isna().sum():
            df[col] = numeric.astype('Int64')
        else:
            log.debug(f'leaving {col} as read; it is not wholly numeric')
    return df[df[year_col] == int(year)].reset_index(drop=True)


def stage_archive_tables(year, archive_path, m, tables):
    """Write year-filtered Envirofacts views from the archive to the tables dir.

    Writes into the same ``tables/<year>`` directory an API download would
    fill, so everything downstream - subpart assignment, parsing, validation -
    runs unchanged. Tables already staged are left alone.
    """
    tables_dir = OUTPUT_PATH.joinpath('tables', str(year))
    tables_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as zip_file:
        members = {Path(n).stem.upper(): n for n in zip_file.namelist()
                   if n.lower().endswith('.csv')}
    absent = [t for t in tables if t not in members]
    if absent:
        raise stewi.exceptions.DataNotFoundError(
            message=(f'{archive_path.name} is missing tables needed for GHGRP '
                     f'{year}: {", ".join(absent)}'))
    log.info(f'staging GHGRP {year} tables from {archive_path}')
    for table in tables:
        filepath = tables_dir.joinpath(f'{table}.csv')
        if filepath.is_file():
            log.debug(f'{table} already staged in {tables_dir}')
            continue
        df = read_archive_table(archive_path, members[table], year)
        if df.empty:
            log.warning(f'{table} has no {year} rows in {archive_path.name}')
        else:
            log.info('staged %s (rows: %i)', table, len(df))
        df.to_csv(filepath, index=False)
    year_config = archive_year_config(year) or {}
    m.add(time=time.ctime(archive_path.stat().st_mtime), filename=archive_path,
          filetype='Static File',
          url=year_config.get('source_url', str(archive_path)))


def generate_url(table, report_year='', row_start=0, row_end=9999,
                 output_ext='JSON'):
    """Input a specific table name to generate the query URL to submit."""
    request_url = _config['enviro_url'] + table
    if report_year != '':
        request_url += f'/REPORTING_YEAR/=/{report_year}'
    if row_start != '':
        request_url += f'/ROWS/{str(row_start)}:{str(row_end)}'
    request_url += f'/{output_ext}'
    return request_url


def get_row_count(table, report_year):
    """Return number of rows from API for specific table."""
    count_url = _config['enviro_url'] + table
    if report_year != '':
        count_url += f'/REPORTING_YEAR/=/{report_year}'
    count_url += '/COUNT'
    try:
        count_request = make_url_request(count_url)
        count_xml = minidom.parseString(count_request.text)
        table_count = count_xml.getElementsByTagName('REQUESTRECORDCOUNT')
        table_count = int(table_count[0].firstChild.nodeValue)
    except IndexError as e:
        raise IndexError(f'error accessing table count for {table}') from e
    except (ExpatError, HTTPError) as e:
        raise HTTPError(f'{table} not found') from e
    return table_count


def download_chunks(table, table_count, m, row_start=0, report_year='',
                    filepath=''):
    """Download data from envirofacts in chunks."""
    # Generate URL for each 5,000 row grouping and add to DataFrame
    output_table = pd.DataFrame()
    output_list = []
    while row_start <= table_count:
        row_end = row_start + 4999
        table_url = generate_url(table=table, report_year=report_year,
                                 row_start=row_start, row_end=row_end,
                                 output_ext='csv')
        log.debug(f'url: {table_url}')
        table_temp, temp_time = import_table(table_url, get_time=True)
        output_list.append(table_temp)
        row_start += 5000
    output_table = pd.concat(output_list)
    output_table.columns=output_table.columns.str.upper()
    m.add(time=temp_time, url=generate_url(table, report_year=report_year,
                                           row_start='', output_ext='csv'),
          filetype='Database', filename=filepath)
    if filepath:
        output_table.to_csv(filepath, index=False)
    return output_table


def get_facilities(year):
    """Return GHGRP facilities for the reporting year.

    Facilities normally come from the data summary spreadsheet EPA publishes
    per year. A year built from an archive has no such spreadsheet, so the
    ``V_GHG_EMITTER_FACILITIES`` view is used instead - the same facility
    attributes, out of the same database.
    """
    if archive_year_config(year):
        return facilities_from_ef_view(year)
    return facilities_from_data_summaries(year)


def facilities_from_ef_view(year):
    """Parse GHGRP facilities from the staged V_GHG_EMITTER_FACILITIES view."""
    filepath = OUTPUT_PATH.joinpath('tables', str(year),
                                    f'{EF_FACILITIES_TABLE}.csv')
    if not filepath.is_file():
        raise stewi.exceptions.DataNotFoundError(
            message=(f'{EF_FACILITIES_TABLE} has not been staged for GHGRP '
                     f'{year}; run Option A first'))
    log.info(f'loading facilities from {filepath}')
    # Zip is read as text so that facilities in a 0-prefixed ZCTA keep it.
    facilities_df = (pd.read_csv(filepath, low_memory=False, dtype={'ZIP': str})
                     .rename(columns=EF_FACILITY_COLUMNS))
    facilities_df = facilities_df[
        StewiFormat.FACILITY.subset_fields(facilities_df)]
    return facilities_df.drop_duplicates()


def facilities_from_data_summaries(year):
    """Load and parse GHGRP data by facility from the data summary spreadsheet.

    Parses data to create dataframe of GHGRP facilities along with identifying
    information such as address, zip code, lat and long.
    """
    facilities_file = data_summaries_path / f'ghgp_data_{year}.xlsx'
    if not(facilities_file.exists()):
        # folder structure may be duplicated
        facilities_file = (data_summaries_path / data_summaries_path.name /
                           f'ghgp_data_{year}.xlsx')
    # load .xlsx file from filepath
    facilities_dict = pd.read_excel(facilities_file, sheet_name=None,
                                    skiprows=3)
    # drop excel worksheets that we do not need
    for s in ['Industry Type', 'FAQs about this Data']:
        facilities_dict.pop(s, None)

    # for all remaining worksheets, concatenate into single dataframe
    # certain columns need to be renamed for consistency

    col_dict = {'Reported Address': 'Address',
                'Reported City': 'City',
                'Reported County': 'County',
                'Reported Latitude': 'Latitude',
                'Reported Longitude': 'Longitude',
                'Reported State': 'State',
                #'State where Emissions Occur':'State',
                'Reported Zip Code': 'Zip Code',
                }
    facilities_df = pd.DataFrame()
    for s in facilities_dict.keys():
        for k in col_dict.keys():
            if k in facilities_dict[s]:
                facilities_dict[s].rename(columns={k: col_dict[k]},
                                          inplace=True)
        facilities_df = pd.concat([facilities_df,
                                   facilities_dict[s]]).reset_index(drop=True)

    # rename certain columns
    facilities_df = facilities_df.rename(columns={'Facility Id': 'FacilityID',
                                                  'Primary NAICS Code': 'NAICS',
                                                  'Facility Name': 'FacilityName',
                                                  'Zip Code': 'Zip'})
    # keep only those columns we are interested in retaining
    facilities_df = facilities_df[StewiFormat.FACILITY.subset_fields(facilities_df)]

    # drop any duplicates
    facilities_df.drop_duplicates(inplace=True)

    return facilities_df


def download_excel_tables(m):
    # define required tables for download
    required_tables = [[data_summaries_path,
                        _config['url'] + _config['data_summaries_url'],
                        'Zip File'],
                       [esbb_subparts_path,
                        _config['url'] + _config['esbb_subparts_url'],
                        'Static File'],
                       [lo_subparts_path,
                        _config['url'] + _config['lo_subparts_url'],
                        'Static File'],
                       ]

    # download each table from web and save locally
    for table in required_tables:
        temp_time = download_table(filepath=table[0], url=table[1],
                                   get_time=True)
        # record metadata
        m.add(time=temp_time, filename=table[0], url=table[1], filetype=table[2])


def import_or_download_table(filepath, table, year, m):
    # if data already exists on local network, import the data
    if filepath.is_file():
        log.info(f'Importing data from {table}')
        table_df, creation_time = import_table(filepath, get_time=True)
        m.add(time=creation_time, filename=filepath, filetype='Database',
              url=generate_url(table, report_year=year, row_start='',
                               output_ext='CSV'))

    # otherwise, download the data and save to the network
    else:
        # determine number of rows in subpart emissions table
        row_count = get_row_count(table, report_year=year)
        log.info('Downloading %s (rows: %i)', table, row_count)
        # download data in chunks
        table_df = download_chunks(table=table, table_count=row_count, m=m,
                                   report_year=year, filepath=filepath)

    if table_df is None:
        return None

    # drop any unnamed columns
    table_df = table_df.drop(columns=table_df.columns[
            table_df.columns.str.contains('unnamed', case=False)])

    # for all columns, remove subpart-specific prefixes if present
    cols = table_df.columns.str.extract(f'.*{table}\\.(.*)', expand=False)
    table_df.columns = np.where(cols.isna(),
                                table_df.columns,
                                cols)

    return table_df


def download_table(filepath: Path, url: str, get_time=False):
    """Download file at url to Path if it does not exist."""
    if not filepath.exists():
        if url.lower().endswith('zip'):
            r = make_url_request(url)
            zip_file = zipfile.ZipFile(io.BytesIO(r.content))
            zip_file.extractall(filepath)
        elif 'xls' in url.lower() or url.lower().endswith('excel'):
            r = make_url_request(url)
            with open(filepath, "wb") as f:
                f.write(r.content)
        elif 'json' in url.lower():
            pd.read_json(url).to_csv(filepath, index=False)
        if get_time:
            try:
                retrieval_time = filepath.stat().st_ctime
            except OSError:
                retrieval_time = time.time()
            return time.ctime(retrieval_time)
    elif get_time:
        return time.ctime(filepath.stat().st_ctime)


def import_table(path_or_reference, get_time=False):
    """Read and return time of csv from url or Path."""
    try:
        df = pd.read_csv(path_or_reference, low_memory=False)
    except (urllib.error.URLError, urllib3.exceptions.HTTPError) as exception:
        log.warning(exception.reason)
        log.info('retrying url...')
        time.sleep(3) # at times increasing this for large tables can be useful
        df = pd.read_csv(path_or_reference, low_memory=False)
    if get_time and isinstance(path_or_reference, Path):
        retrieval_time = path_or_reference.stat().st_ctime
        return df, time.ctime(retrieval_time)
    elif get_time:
        retrieval_time = time.time()
        return df, time.ctime(retrieval_time)
    return df


def required_tables(year):
    """Return the Envirofacts tables carrying primary emissions for the year.

    Shared by the API download and the archive staging so that both fill
    ``tables/<year>`` with the same set, and the SUBPART column that becomes
    ``Process`` is read off the same rows either way.
    """
    # import list of all ghgrp tables
    ghgrp_tables_df = pd.read_csv(GHGRP_DATA_PATH
                                  .joinpath('all_ghgrp_tables_years.csv')
                                  ).fillna('')
    # filter to obtain only those tables included in the report year
    year_tables = ghgrp_tables_df[ghgrp_tables_df['REPORTING_YEAR'
                                                  ].str.contains(str(year))]
    if len(year_tables)==0:
        raise stewi.exceptions.InventoryNotAvailableError(
            inv='GHGRP', year=year)
    # filter to obtain only those tables that include primary emissions
    return year_tables[year_tables['PrimaryEmissions'] == 1
                       ].reset_index(drop=True)


def download_and_parse_subpart_tables(year, m):
    """
    Generates a list of required subpart tables, based on report year.
    Downloads all subpart tables in the list and saves them to local network.
    Parses subpart tables to standardized EPA format, and concatenates into
    master dataframe.
    """
    year_tables = required_tables(year)

    # data directory where subpart emissions tables will be stored
    tables_dir = OUTPUT_PATH.joinpath('tables', year)
    log.info(f'downloading and processing GHGRP data to {tables_dir}')
    tables_dir.mkdir(parents=True, exist_ok=True)
    ghgrp1 = pd.DataFrame(columns=ghg_cols)

    # for all subpart emissions tables listed...
    table_list = []
    for subpart_emissions_table in year_tables['TABLE']:
        # define filepath where subpart emissions table will be stored
        filepath = tables_dir.joinpath(f"{subpart_emissions_table}.csv")
        table_df = import_or_download_table(filepath, subpart_emissions_table,
                                            year, m)
        if table_df is None:
            continue
        # add 1-2 letter subpart abbreviation
        abbv = (year_tables.query('TABLE == @subpart_emissions_table')
                ['SUBPART'].iloc[0])
        table_df = table_df.assign(SUBPART_NAME = abbv)
        # drop empty columns
        table_df = table_df.dropna(axis=1, how='all')
        # concatenate temporary dataframe to master ghgrp1 dataframe
        table_list.append(table_df)
    ghgrp1 = pd.concat(table_list, ignore_index=True)

    ghgrp1 = ghgrp1.reset_index(drop=True)
    log.info('Parsing table data...')
    if 'C' in ghgrp1.SUBPART_NAME.unique():
        ghgrp1 = calculate_combustion_emissions(ghgrp1)
        # add these new columns to the list of 'group' columns
        expanded_group_cols = group_cols + ['c_co2', 'c_co2_b', 'c_ch4', 'c_n2o']
    else:
        expanded_group_cols = group_cols

    # combine all GHG name columns from different tables into one
    ghgrp1['Flow Description'] = ghgrp1[name_cols].fillna('').sum(axis=1)

    # use alias if it exists and flow is Other
    alias = [c for c in ghgrp1.columns if c in alias_cols]
    for col in alias:
        mask = ((ghgrp1['Flow Description'] == 'Other') & ~(ghgrp1[col].isna()))
        ghgrp1.loc[mask, 'Flow Description'] = ghgrp1[col]

    # combine all GHG quantity columns from different tables into one
    ghgrp1['FlowAmount'] = ghgrp1[quantity_cols].astype('float').fillna(0).sum(axis=1)
    # combine all method equation columns from different tables into one
    ghgrp1['METHOD'] = ghgrp1[method_cols].fillna('').sum(axis=1)

    # split dataframe into two separate dataframes based on flow description
    # if flow description has been populated:
    ghgrp1a = ghgrp1.loc[ghgrp1['Flow Description'] != ''].reset_index(drop=True)
    # if flow description is blank:
    ghgrp1b = ghgrp1.loc[ghgrp1['Flow Description'] == ''].reset_index(drop=True)

    # parse data where flow description has been populated (ghgrp1a)
    # keep only the necessary columns; drop all others
    ghgrp1a = ghgrp1a.drop(columns=ghgrp1a.columns.difference(
        base_cols + ['Flow Description',
                     'FlowAmount',
                     'METHOD',
                     'SUBPART_NAME']))

    # parse data where flow description is blank (ghgrp1b)
    # keep only the necessary columns; drop all others
    ghgrp1b = ghgrp1b.drop(columns=ghgrp1b.columns.difference(
        base_cols + expanded_group_cols +
        ['METHOD', 'SUBPART_NAME', 'UNIT_NAME', 'FUEL_TYPE']))
    # 'unpivot' data to create separate line items for each group column
    ghgrp1b = ghgrp1b.melt(id_vars=base_cols + ['METHOD', 'SUBPART_NAME',
                                                'UNIT_NAME', 'FUEL_TYPE'],
                           var_name='Flow Description',
                           value_name='FlowAmount')

    # combine data for same generating unit and fuel type
    ghgrp1b['UNIT_NAME'] = ghgrp1b['UNIT_NAME'].fillna('tmp')
    ghgrp1b['FUEL_TYPE'] = ghgrp1b['FUEL_TYPE'].fillna('tmp')
    ghgrp1b = (ghgrp1b
               .groupby(['FACILITY_ID', 'REPORTING_YEAR',
                         'SUBPART_NAME', 'UNIT_NAME', 'FUEL_TYPE',
                         'Flow Description'])
               .agg({'FlowAmount': ['sum'], 'METHOD': ['sum']})
               .reset_index()
               )
    ghgrp1b.columns = ghgrp1b.columns.droplevel(level=1)
    ghgrp1b = ghgrp1b.drop(columns=['UNIT_NAME', 'FUEL_TYPE'])

    # re-join split dataframes
    ghgrp1 = pd.concat([ghgrp1a, ghgrp1b]).reset_index(drop=True)

    # drop those rows where flow amount is confidential
    ghgrp1 = ghgrp1[ghgrp1['FlowAmount'] != 'confidential']

    return ghgrp1


def calculate_combustion_emissions(df):
    """For subpart C, calculate total stationary fuel combustion emissions by GHG.
    emissions are calculated as the sum of four methodological alternatives for
    calculating emissions from combustion (Tier 1-4), plus an alternative to any
    of the four tiers for units that report year-round heat input data to EPA (Part 75)
    """
    df[subpart_c_cols] = df[subpart_c_cols].replace(np.nan, 0.0)
    # nonbiogenic carbon:
    # NOTE: 'PART_75_CO2_EMISSIONS_METHOD' includes biogenic carbon emissions,
    # so there will be a slight error here, but biogenic/nonbiogenic emissions
    # for Part 75 are not reported separately.
    df = (df.assign(c_co2 = lambda x:
                        x['TIER1_CO2_COMBUSTION_EMISSIONS'] +
                        x['TIER2_CO2_COMBUSTION_EMISSIONS'] +
                        x['TIER3_CO2_COMBUSTION_EMISSIONS'] +
                        x['TIER_123_SORBENT_CO2_EMISSIONS'] +
                        x['TIER_4_TOTAL_CO2_EMISSIONS'] -
                        x['TIER_4_BIOGENIC_CO2_EMISSIONS'] +
                        x['PART_75_CO2_EMISSIONS_METHOD'] -
                        x['TIER123_BIOGENIC_CO2_EMISSIONS'])
    # biogenic carbon:
            .assign(c_co2_b = lambda x:
                        x['TIER123_BIOGENIC_CO2_EMISSIONS'] +
                        x['TIER_4_BIOGENIC_CO2_EMISSIONS'])
    # methane:
            .assign(c_ch4 = lambda x:
                        x['TIER1_CH4_COMBUSTION_EMISSIONS'] +
                        x['TIER2_CH4_COMBUSTION_EMISSIONS'] +
                        x['TIER3_CH4_COMBUSTION_EMISSIONS'] +
                        x['T4CH4COMBUSTIONEMISSIONS'] +
                        x['PART_75_CH4_EMISSIONS_CO2E']/CH4GWP)
    # nitrous oxide:
            .assign(c_n2o = lambda x:
                        x['TIER1_N2O_COMBUSTION_EMISSIONS'] +
                        x['TIER2_N2O_COMBUSTION_EMISSIONS'] +
                        x['TIER3_N2O_COMBUSTION_EMISSIONS'] +
                        x['T4N2OCOMBUSTIONEMISSIONS'] +
                        x['PART_75_N2O_EMISSIONS_CO2E']/N2OGWP)
    # drop subpart C columns because they are no longer needed
            .drop(columns=subpart_c_cols)
            )
    return df


def parse_additional_suparts_data(addtnl_subparts_path, subpart_cols_file, year):
    log.info(f'loading additional subpart data from {addtnl_subparts_path}')

    with warnings.catch_warnings():
        # Avoid the UserWarning for openpyxl "Unknown extension is not supported"
        warnings.filterwarnings("ignore", category=UserWarning)
        # load .xslx data for additional subparts from filepath
        addtnl_subparts_dict = pd.read_excel(addtnl_subparts_path,
                                             sheet_name=None)
    # import column headers data for additional subparts
    subpart_cols = pd.read_csv(GHGRP_DATA_PATH.joinpath(subpart_cols_file))
    # get list of tabs to process
    addtnl_tabs = subpart_cols['tab_name'].unique()
    for key, df in list(addtnl_subparts_dict.items()):
        if key in addtnl_tabs:
            for column in df:
                df.rename(columns={column: column.replace('\n',' ')}, inplace=True)
                df.rename(columns={'Facility ID': 'GHGRP ID',
                                   'Reporting Year': 'Year'}, inplace=True)
            addtnl_subparts_dict[key] = df
        else:
            del addtnl_subparts_dict[key]
    # initialize dataframe
    ghgrp = pd.DataFrame()
    addtnl_base_cols = ['GHGRP ID', 'Year']

    # for each of the tabs in the excel workbook...
    for tab in addtnl_tabs:
        cols = subpart_cols[subpart_cols['tab_name'] == tab].reset_index(drop=True)
        col_dict = {}

        for i in cols['column_type'].unique():
            col_dict[i] = list(cols.loc[cols['column_type'] == i]['column_name'])

        # create temporary dataframe from worksheet, using just the desired columns
        subpart_df = addtnl_subparts_dict[tab][
            addtnl_base_cols + list(set().union(*col_dict.values()))]
        # keep only those data for the specified report year
        subpart_df = subpart_df[subpart_df['Year'] == int(year)]

        if 'method' in col_dict.keys():
            # combine all method equation columns into one, drop old method columns
            subpart_df['METHOD'] = subpart_df[col_dict['method']
                                              ].fillna('').sum(axis=1)
            subpart_df = subpart_df.drop(col_dict['method'], axis=1)
        else:
            subpart_df['METHOD'] = ''

        if 'flow' in col_dict.keys():
            n = len(col_dict['flow'])
            i = 1
            subpart_df = subpart_df.rename(columns={col_dict['flow'][0]: 'Flow Name'})
            while i < n:
                subpart_df['Flow Name'] = (subpart_df['Flow Name']
                                           .fillna(subpart_df[col_dict['flow'][i]]))
                del subpart_df[col_dict['flow'][i]]
                i += 1
        fields = [c for c in subpart_df.columns if c in ['METHOD', 'Flow Name']]

        # 'unpivot' data to create separate line items for each quantity column
        temp_df = subpart_df.melt(id_vars=addtnl_base_cols + fields,
                                  var_name='Flow Description',
                                  value_name='FlowAmount')

        # drop those rows where flow amount is confidential
        temp_df = temp_df[temp_df['FlowAmount'] != 'confidential']

        # add 1-2 letter subpart abbreviation
        temp_df['SUBPART_NAME'] = cols['subpart_abbr'][0]

        # concatentate temporary dataframe with master dataframe
        ghgrp = pd.concat([ghgrp, temp_df], ignore_index=True)

    if 'Flow Name' in ghgrp:
        ghgrp['Flow Name'] = (ghgrp['Flow Name']
                              .replace('\n','', regex=True)
                              .str.strip())
    # drop those rows where flow amount is negative, zero, or NaN
    ghgrp = ghgrp[ghgrp['FlowAmount'] > 0]
    ghgrp = ghgrp[ghgrp['FlowAmount'].notna()]

    def strip_if_string(value):
        if isinstance(value, str):
            return value.strip()
        return value
    ghgrp['GHGRP ID'] = ghgrp['GHGRP ID'].apply(strip_if_string).astype(int)

    ghgrp = ghgrp.rename(columns={'GHGRP ID': 'FACILITY_ID',
                                  'Year': 'REPORTING_YEAR'})

    return ghgrp


def parse_subpart_O(year):
    """Parse emissions data for subpart O."""
    df = parse_additional_suparts_data(lo_subparts_path,
                                       'o_subparts_columns.csv', year)
    # convert subpart O data from CO2e to mass of HFC23 emitted,
    # maintain CO2e for validation
    df['AmountCO2e'] = df['FlowAmount'] * 1000
    df.loc[df['SUBPART_NAME'] == 'O', 'FlowAmount'] =\
        df['FlowAmount'] / HFC23GWP
    df.loc[df['SUBPART_NAME'] == 'O', 'Flow Description'] =\
        'Total Reported Emissions Under Subpart O (metric tons HFC-23)'
    return df


def parse_subpart_L(year):
    """Parse emissions data for subpart L."""
    df = parse_additional_suparts_data(lo_subparts_path,
                                       'l_subparts_columns.csv', year)
    subpart_L_GWPs = load_subpart_l_gwp()
    df = df.merge(subpart_L_GWPs, how='left', on=['Flow Name', 'Flow Description'])
    df['CO2e_factor'] = df['CO2e_factor'].fillna(1)
    df = (df
          # drop old Flow Description column
          .drop(columns=['Flow Description'])
          # Flow Name column becomes new Flow Description
          .rename(columns={'Flow Name': 'Flow Description'})
          )
    # calculate mass flow amount based on emissions in CO2e and GWP
    df['FlowAmount'] = df['FlowAmount'] / df['CO2e_factor']
    return df.drop(columns=['CO2e_factor'])


def generate_national_totals_validation(
        year,
        validation_table=EF_VALIDATION_TABLE
        ):
    # define filepath for reference data. A year built from an archive has the
    # view staged per year already; the API path keeps its single shared file.
    if archive_year_config(year):
        ref_filepath = OUTPUT_PATH.joinpath('tables', str(year),
                                            f'{validation_table}.csv')
    else:
        ref_filepath = OUTPUT_PATH.joinpath('GHGRP_reference.csv')
    m = MetaGHGRP()
    reference_df = import_or_download_table(ref_filepath, validation_table,
                                            year, m)
    # parse reference dataframe to prepare it for validation
    reference_df['YEAR'] = reference_df['YEAR'].astype('str')
    reference_df = (reference_df.query('YEAR == @year')
                                .reset_index(drop=True))
    co2e_dict = {
        'SF6': 22800,
        'BIOCO2': 1,
        'NF3': 17200,
     }
    # Update some values with known CO2e
    reference_df['co2e_factor'] = reference_df['GAS_CODE'].map(co2e_dict)
    reference_df['GHG_QUANTITY'] = np.where(
        reference_df['GHG_QUANTITY'].isna(),
        reference_df['CO2E_EMISSION'] / reference_df['co2e_factor'],
        reference_df['GHG_QUANTITY'])
    reference_df['FlowAmount'] = reference_df['GHG_QUANTITY'].astype(float) * 1000

    # Maintain some flows in CO2e for validation
    reference_df.loc[reference_df['GAS_CODE'].isin(flows_CO2e),
                                  'FlowAmount'] =\
        reference_df['CO2E_EMISSION'].astype(float) * 1000
    reference_df.loc[reference_df['GAS_CODE'].isin(flows_CO2e),
                                  'GAS_NAME'] =\
        reference_df['GAS_NAME'] + ' (CO2e)'

    reference_df = reference_df[['FlowAmount', 'GAS_NAME', 'GAS_CODE',
                                 'FACILITY_ID', 'SUBPART_NAME']]
    reference_df = (reference_df
                    .rename(columns={'FACILITY_ID': 'FacilityID',
                                     'GAS_NAME': 'FlowName',
                                     'GAS_CODE': 'FlowCode'})
                    .groupby(['FlowName', 'FlowCode', 'SUBPART_NAME'])
                    .agg({'FlowAmount': ['sum']})
                    .reset_index())
    reference_df.columns = reference_df.columns.droplevel(level=1)
    # save reference dataframe to network
    reference_df.to_csv(DATA_PATH / f'GHGRP_{year}_NationalTotals.csv',
                        index=False)

    # Update validationSets_Sources.csv
    date_created = time.strptime(time.ctime(ref_filepath.stat().st_ctime))
    date_created = time.strftime('%d-%b-%Y', date_created)
    year_config = archive_year_config(year)
    validation_dict = {'Inventory': 'GHGRP',
                       #'Version':'',
                       'Year': year,
                       'Name': f'GHGRP Table {validation_table}',
                       'URL': (year_config['source_url'] if year_config else
                               generate_url(validation_table, report_year='',
                                            row_start='', output_ext='CSV')),
                       'Criteria': (year_config.get('source_note', '')
                                    if year_config else ''),
                       'Date Acquired': date_created,
                       }
    update_validationsets_sources(validation_dict, date_acquired=True)


def validate_national_totals_by_subpart(tab_df, year):
    log.info('validating flowbyfacility against national totals')
    # AmountCO2e is set only by subpart O, which a year built from an archive
    # does not carry
    if 'AmountCO2e' not in tab_df:
        tab_df = tab_df.assign(AmountCO2e=np.nan)
    # apply CO2e factors for some flows
    mask = (tab_df['AmountCO2e'].isna() & tab_df['FlowID'].isin(flows_CO2e))
    tab_df.loc[mask, 'Flow Description'] = 'Fluorinated GHG Emissions (mt CO2e)'
    subpart_L_GWPs = (load_subpart_l_gwp(required=False)
                      .rename(columns={'Flow Name': 'FlowName'}))
    if subpart_L_GWPs.empty:
        tab_df['CO2e_factor'] = 1
    else:
        tab_df = tab_df.merge(subpart_L_GWPs, how='left',
                              on=['FlowName', 'Flow Description'],
                              validate="m:1")
        tab_df['CO2e_factor'] = tab_df['CO2e_factor'].fillna(1)
    tab_df.loc[mask, 'AmountCO2e'] = tab_df['FlowAmount'] * tab_df['CO2e_factor']

    # for subset of flows, use CO2e for validation
    mask = tab_df['FlowID'].isin(flows_CO2e)
    tab_df.loc[mask, 'FlowAmount'] = tab_df['AmountCO2e']

    # parse tabulated data
    tab_df.drop(columns=['FacilityID', 'DataReliability', 'FlowName'],
                inplace=True)
    tab_df.rename(columns={'Process': 'SubpartName',
                           'FlowID': 'FlowName'}, inplace=True)

    # import and parse reference data
    totals_path = DATA_PATH / f'GHGRP_{year}_NationalTotals.csv'
    if not totals_path.exists():
        generate_national_totals_validation(year)
    ref_df = (pd.read_csv(totals_path)
              .drop(columns=['FlowName'])
              .rename(columns={'SUBPART_NAME': 'SubpartName',
                               'FlowCode': 'FlowName'})
              )

    validation_result = validate_inventory(tab_df, ref_df,
                                           group_by=['FlowName', 'SubpartName'])
    # Update flow names to indicate which are in CO2e
    validation_result.loc[validation_result['FlowName'].isin(flows_CO2e),
                          'FlowName'] = validation_result['FlowName'] + ' (CO2e)'
    write_validation_result('GHGRP', year, validation_result)


def generate_metadata(year, m, datatype='inventory'):
    """Get metadata and writes to .json."""
    if datatype == 'source':
        source_path = m.filename
        source_meta = compile_source_metadata(source_path, _config, year)
        source_meta['SourceType'] = m.filetype
        source_meta['SourceURL'] = m.url
        source_meta['SourceAcquisitionTime'] = m.time
        write_metadata(f'GHGRP_{year}', source_meta,
                       category=EXT_DIR, datatype='source')
    else:
        source_meta = read_source_metadata(paths, set_stewi_meta(f'GHGRP_{year}',
                                           EXT_DIR),
                                           force_JSON=True)['tool_meta']
        write_metadata(f'GHGRP_{year}', source_meta, datatype=datatype)


def load_subpart_l_gwp(required=True):
    """Load global warming potentials for subpart L calculation.

    The e-GGRT help site the workbook is attached to has been migrated, and the
    attachment URL now answers with an HTML page, so a machine without the
    workbook already cached cannot read it. A caller that only needs the
    factors to report a validation figure passes ``required=False`` and gets an
    empty lookup; :func:`parse_subpart_L` cannot, because the factors divide
    its flow amounts.
    """
    subpart_L_GWPs_url = _config['subpart_L_GWPs_url']
    filepath = OUTPUT_PATH.joinpath('Subpart L Calculation Spreadsheet.xls')
    download_table(filepath=filepath, url=subpart_L_GWPs_url)
    try:
        pd.ExcelFile(filepath)
    except ValueError as e:
        # whatever answered is not a workbook; drop it so that a later run
        # retries the URL instead of reading back the cached error page
        filepath.unlink(missing_ok=True)
        if required:
            raise stewi.exceptions.DataNotFoundError(
                message=('subpart L global warming potentials unavailable from '
                         f'{subpart_L_GWPs_url}')) from e
        log.warning('subpart L global warming potentials unavailable from %s; '
                    'fluorinated GHG flows are validated against CO2e national '
                    'totals with a factor of 1 and will read short',
                    subpart_L_GWPs_url)
        return pd.DataFrame(columns=['Flow Name', 'CO2e_factor',
                                     'Flow Description'])
    table1 = pd.read_excel(filepath, sheet_name='Lookup Tables',
                           usecols="A,D")
    table1.rename(columns={'Global warming potential (100 yr.)': 'CO2e_factor',
                           'Name': 'Flow Name'},
                  inplace=True)
    # replace emdash with hyphen
    table1['Flow Name'] = table1['Flow Name'].str.replace('–', '-')
    table2 = pd.read_excel(filepath, sheet_name='Lookup Tables',
                           usecols="G,H", nrows=12)
    table2.rename(columns={'Default Global Warming Potential': 'CO2e_factor',
                           'Fluorinated GHG Groupd': 'Flow Name'},
                  inplace=True)

    # rename certain fluorinated GHG groups for consistency
    table2['Flow Name'] = table2['Flow Name'].str.replace(
        'Saturated HFCs with 2 or fewer carbon-hydrogen bonds',
        'Saturated hydrofluorocarbons (HFCs) with 2 or fewer '
        'carbon-hydrogen bonds')
    table2['Flow Name'] = table2['Flow Name'].str.replace(
        'Saturated HFEs and HCFEs with 1 carbon-hydrogen bond',
        'Saturated hydrofluoroethers (HFEs) and hydrochlorofluoroethers '
        '(HCFEs) with 1 carbon-hydrogen bond')
    table2['Flow Name'] = table2['Flow Name'].str.replace(
        'Unsaturated PFCs, unsaturated HFCs, unsaturated HCFCs, '
        'unsaturated halogenated ethers, unsaturated halogenated '
        'esters, fluorinated aldehydes, and fluorinated ketones',
        'Unsaturated perfluorocarbons (PFCs), unsaturated HFCs, '
        'unsaturated hydrochlorofluorocarbons (HCFCs), unsaturated '
        'halogenated ethers, unsaturated halogenated esters, '
        'fluorinated aldehydes, and fluorinated ketones')
    subpart_L_GWPs = pd.concat([table1, table2])
    subpart_L_GWPs['Flow Description'] = 'Fluorinated GHG Emissions (mt CO2e)'
    return subpart_L_GWPs


def main(**kwargs):

    parser = argparse.ArgumentParser(argument_default = argparse.SUPPRESS)

    parser.add_argument('Option',
                        help = 'What do you want to do:\
                        [A] Download and save GHGRP data\
                        [B] Generate inventory files for StEWI and validate\
                        [C] Download national totals data for validation',
                        type = str)

    parser.add_argument('-Y', '--Year', nargs = '+',
                        help = 'What GHGRP year do you want to retrieve',
                        type = str)

    parser.add_argument('-A', '--Archive',
                        help = 'Path to the Envirofacts views archive, for a \
                        reporting year EPA has not published',
                        type = str)

    if len(kwargs) == 0:
        kwargs = vars(parser.parse_args())

    for year in kwargs['Year']:
        year = str(year)
        pickle_file = OUTPUT_PATH.joinpath(f'GHGRP_{year}.pk')
        if kwargs['Option'] == 'A':

            m = MetaGHGRP()
            year_config = archive_year_config(year)
            if year_config:
                # stage the report year out of the archive into the same
                # tables directory an API download would have filled
                archive_path = resolve_archive(year, kwargs.get('Archive'))
                stage_archive_tables(
                    year, archive_path, m,
                    list(required_tables(year)['TABLE']) +
                    [EF_FACILITIES_TABLE, EF_VALIDATION_TABLE])
            else:
                download_excel_tables(m)

            # download subpart emissions tables for report year and save locally
            # parse subpart emissions data to match standardized EPA format
            ghgrp1 = download_and_parse_subpart_tables(year, m)

            if year_config:
                # Subparts E, BB, CC, L and O reach StEWI only through the
                # aggregated spreadsheets EPA publishes for released years, so
                # a year built from an archive cannot carry them.
                log.warning('GHGRP %s omits subparts %s: they are only '
                            'published in the aggregated spreadsheets, which '
                            'stop at %s', year,
                            ', '.join(year_config.get('omitted_subparts', [])),
                            _config['most_recent_year'])
                ghgrp = ghgrp1.reset_index(drop=True)
            else:
                # parse emissions data for subparts E, BB, CC, LL (S already accounted for)
                ghgrp2 = parse_additional_suparts_data(esbb_subparts_path,
                                                       'esbb_subparts_columns.csv', year)

                # parse emissions data for subpart O
                ghgrp3 = parse_subpart_O(year)

                # parse emissions data for subpart L
                ghgrp4 = parse_subpart_L(year)

                # concatenate ghgrp1, ghgrp2, ghgrp3, and ghgrp4
                ghgrp = pd.concat([ghgrp1, ghgrp2,
                                   ghgrp3, ghgrp4]).reset_index(drop=True)

            # map flow descriptions to standard gas names from GHGRP
            ghg_mapping = pd.read_csv(GHGRP_DATA_PATH.joinpath('ghg_mapping.csv'),
                                      usecols=['Flow Description', 'FlowName',
                                               'GAS_CODE'])
            ghgrp = pd.merge(ghgrp, ghg_mapping, on='Flow Description',
                             how='left', validate='m:1')
            missing = ghgrp[ghgrp['FlowName'].isna()]
            if len(missing) > 0:
                log.warning('some flows are unmapped')
            ghgrp = (ghgrp
                     .drop(columns=['Flow Description'])
                     .rename(columns={'FACILITY_ID': 'FacilityID',
                                      'NAICS_CODE': 'NAICS',
                                      'GAS_CODE': 'FlowCode'})
                     )

            # pickle data and save to network
            log.info(f'saving processed GHGRP data to {pickle_file}')
            ghgrp.to_pickle(pickle_file)

            generate_metadata(year, m, datatype='source')

        if kwargs['Option'] == 'B':
            log.info(f'extracting data from {pickle_file}')
            ghgrp = pd.read_pickle(pickle_file)

            # import data reliability scores
            ghgrp_reliability_table = get_reliability_table_for_source('GHGRPa')

            # add reliability scores
            ghgrp = pd.merge(ghgrp, ghgrp_reliability_table,
                             left_on='METHOD',
                             right_on='Code', how='left')

            # fill NAs with 5 for DQI reliability score
            ghgrp['DQI Reliability Score'] = ghgrp['DQI Reliability Score'
                                                   ].fillna(value=5)

            # convert metric tons to kilograms
            ghgrp['FlowAmount'] = 1000 * ghgrp['FlowAmount'].astype('float')

            # rename reliability score column for consistency
            ghgrp = (ghgrp
                     .rename(columns={'DQI Reliability Score': 'DataReliability',
                                      'SUBPART_NAME': 'Process',
                                      'FlowCode': 'FlowID'})
                     .assign(ProcessType = 'Subpart')
                     )
            log.info('generating flowbysubpart output')

            # generate flowbysubpart
            ghgrp_fbs = ghgrp[StewiFormat.FLOWBYPROCESS.subset_fields(ghgrp)
                              ].reset_index(drop=True)
            ghgrp_fbs = aggregate(ghgrp_fbs, ['FacilityID', 'FlowName', 'Process',
                                              'ProcessType'])
            store_inventory(ghgrp_fbs, f'GHGRP_{year}', 'flowbyprocess')

            log.info('generating flowbyfacility output')
            ghgrp_fbf = ghgrp[StewiFormat.FLOWBYFACILITY.subset_fields(ghgrp)
                              ].reset_index(drop=True)

            # aggregate instances of more than one flow for same facility and flow type
            ghgrp_fbf = aggregate(ghgrp_fbf, ['FacilityID', 'FlowName'])
            store_inventory(ghgrp_fbf, f'GHGRP_{year}', 'flowbyfacility')

            log.info('generating flows output')
            flow_columns = ['FlowName', 'FlowID']
            ghgrp_flow = ghgrp[flow_columns].drop_duplicates()
            ghgrp_flow = (ghgrp_flow
                          .dropna(subset=['FlowName'])
                          .sort_values(by=['FlowID', 'FlowName'])
                          .assign(Compartment = 'air')
                          .assign(Unit = 'kg')
                          )
            store_inventory(ghgrp_flow, f'GHGRP_{year}', 'flow')

            log.info('generating facilities output')
            facilities_df = get_facilities(year)

            # add facility information based on facility ID
            ghgrp = ghgrp.merge(facilities_df, on='FacilityID', how='left')

            # generate facilities output and save to network
            ghgrp_facility = ghgrp[StewiFormat.FACILITY.subset_fields(ghgrp)].drop_duplicates()
            ghgrp_facility = ghgrp_facility.dropna(subset=['FacilityName'])
            # ensure NAICS does not have trailing decimal/zero
            ghgrp_facility['NAICS'] = ghgrp_facility['NAICS'].fillna(0)
            ghgrp_facility['NAICS'] = ghgrp_facility['NAICS'].astype(int).astype(str)
            ghgrp_facility.loc[ghgrp_facility['NAICS'] == '0', 'NAICS'] = None
            ghgrp_facility.sort_values(by=['FacilityID'], inplace=True)
            store_inventory(ghgrp_facility, f'GHGRP_{year}', 'facility')

            validate_national_totals_by_subpart(ghgrp, year)

            # Record metadata compiled from all GHGRP files and tables
            generate_metadata(year, m=None, datatype='inventory')

        elif kwargs['Option'] == 'C':
            log.info('generating national totals for validation')
            if archive_year_config(year):
                stage_archive_tables(
                    year, resolve_archive(year, kwargs.get('Archive')),
                    MetaGHGRP(), [EF_VALIDATION_TABLE])
            generate_national_totals_validation(year)


if __name__ == '__main__':
    main(Option='C', Year=range(2021, 2025))
    main(Option='A', Year=range(2021, 2025))
    main(Option='B', Year=range(2021, 2025))
