# globals.py (facilitymatcher)
# !/usr/bin/env python3
# coding=utf-8
"""
Supporting variables and functions used in facilitymatcher
"""

import zipfile
import requests
import pandas as pd
import os
from datetime import datetime
from pathlib import Path

from stewi.globals import log, set_stewi_meta, source_metadata, config
import facilitymatcher.colocation as colocation
import facilitymatcher.quantity as quantity
import facilitymatcher.WriteFacilityMatchesforStEWI as write_fm
import facilitymatcher.WriteFRSNAICSforStEWI as write_naics
from esupy.processed_data_mgmt import Paths, load_preprocessed_output,\
    write_df_to_file, write_metadata_to_file, read_source_metadata,\
    download_from_remote
from esupy.util import strip_file_extension

MODULEPATH = Path(__file__).resolve().parent
DATA_PATH = MODULEPATH / 'data'

paths = Paths()
paths.local_path = paths.local_path / 'facilitymatcher'
output_dir = paths.local_path
ext_folder = 'FRS Data Files'
FRSpath = paths.local_path / ext_folder

FRS_config = config(config_path=MODULEPATH)['databases']['FRS']
CHUNK_SIZE = 1 << 20

inventory_to_FRS_pgm_acronymn = FRS_config['program_dictionary']
stewi_inventories = list(inventory_to_FRS_pgm_acronymn.keys())

_canonical_registry_map = None
_quantity_comparisons = []


def set_facilitymatcher_meta(file_name, category):
    """Create a class of esupy FileMeta."""
    facilitymatcher_meta = set_stewi_meta(file_name, category)
    facilitymatcher_meta.tool = "facilitymatcher"
    return facilitymatcher_meta


def resolve_frs_archive(archive=None):
    """Locate a local copy of the FRS combined national zip, or None.

    Checked in order: the ``archive`` argument, ``$FRS_COMBINED_ARCHIVE``, then
    ``archive_file_name`` from config.yaml under :data:`FRSpath`, which is where
    a download leaves it. None means no copy is on hand and the file has to be
    downloaded.
    """
    candidates = [archive,
                  os.environ.get('FRS_COMBINED_ARCHIVE'),
                  FRSpath / FRS_config['archive_file_name']]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def download_FRS_combined_national(archive_path):
    """Stream the FRS combined national zip from source to ``archive_path``.

    Streamed to disk rather than held in memory: the file is over 1.3 GB, and
    keeping it means reading a second FRS file out of it does not pull the whole
    archive down again.
    """
    url = FRS_config['url']
    log.info('initiating url request from %s', url)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    partial = archive_path.with_name(archive_path.name + '.part')
    with requests.get(url, stream=True) as response:
        response.raise_for_status()
        with open(partial, 'wb') as f:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)
    partial.replace(archive_path)
    log.info('saved %s (%.2f GB)', archive_path,
             archive_path.stat().st_size / 1e9)
    return archive_path


def frs_archive_version(archive_path):
    """Return the FRS build date, read from the zip's own member timestamps.

    FRS publishes no version string, and the combined national file is replaced
    in place at the same URL. The timestamp the zip carries for its members is
    the one property that identifies which refresh a copy came from, and it
    survives being moved to another machine, which the HTTP ``Last-Modified``
    header and the file's own mtime do not.
    """
    with zipfile.ZipFile(archive_path) as zip_file:
        stamps = [info.date_time for info in zip_file.infolist()]
    if not stamps:
        return 'NA'
    return datetime(*max(stamps)).strftime('%d-%b-%Y')


def download_extract_FRS_combined_national(file=None, archive=None):
    """Extract a file from the FRS combined national zip, downloading if needed.

    :param file: str, member to extract; None extracts the whole archive
    :param archive: str or Path, a local copy of the zip to use instead of
        downloading. See :func:`resolve_frs_archive` for the search order.
    """
    archive_path = resolve_frs_archive(archive)
    if archive_path is None:
        archive_path = download_FRS_combined_national(
            FRSpath / FRS_config['archive_file_name'])
    else:
        log.info('using FRS combined national file already at %s', archive_path)
    source_dict = dict(source_metadata)
    source_dict['SourceType'] = 'Zip file'
    source_dict['SourceURL'] = FRS_config['url']
    source_dict['SourceVersion'] = frs_archive_version(archive_path)
    with zipfile.ZipFile(archive_path) as zip_file:
        if file is None:
            log.info(f'extracting all FRS files from {archive_path}')
            name = 'FRS_Files'
            zip_file.extractall(FRSpath)
        else:
            log.info('extracting %s from %s', file, archive_path)
            zip_file.extract(file, path=FRSpath)
            source_dict['SourceFileName'] = file
            name = strip_file_extension(file)
    source_dict['SourceAcquisitionTime'] = datetime.now().strftime('%d-%b-%Y')
    write_fm_metadata(name, source_dict, category=ext_folder)


def download_and_read_frs_file(filetype):
    """
    Downloads, if necessary, and returns a df of the FRS filetype
    :param filetype: str, one of "FRS_bridge_file", "FRS_NAICS_file",
        "FRS_facility_file" or "FRS_program_file"
    """
    file = FRS_config[filetype]
    # Check to see if file exists
    if not (FRSpath / file).exists():
        download_extract_FRS_combined_national(file)
    # Import FRS bridge which provides ID matches
    if filetype == 'FRS_bridge_file':
        col_dict = {'REGISTRY_ID': "str",
                    'PGM_SYS_ACRNM': "str",
                    'PGM_SYS_ID': "str"}
    elif filetype == 'FRS_NAICS_file':
        col_dict = {'REGISTRY_ID': 'str',
                    'PGM_SYS_ACRNM': 'str',
                    'NAICS_CODE': 'str',
                    'PRIMARY_INDICATOR': 'str'}
    elif filetype == 'FRS_facility_file':
        col_dict = {'REGISTRY_ID': 'str',
                    'PRIMARY_NAME': 'str',
                    'LOCATION_ADDRESS': 'str',
                    'CITY_NAME': 'str',
                    'STATE_CODE': 'str',
                    'POSTAL_CODE': 'str',
                    'LATITUDE83': 'float',
                    'LONGITUDE83': 'float'}
    elif filetype == 'FRS_program_file':
        col_dict = {'REGISTRY_ID': 'str',
                    'PGM_SYS_ACRNM': 'str',
                    'PGM_SYS_ID': 'str',
                    'PRIMARY_NAME': 'str',
                    'LOCATION_ADDRESS': 'str',
                    'CITY_NAME': 'str',
                    'STATE_CODE': 'str',
                    'POSTAL_CODE': 'str'}
    df = read_FRS_file(file, col_dict)
    return df


def read_FRS_file(file_name, col_dict):
    """Retrieve FRS data file stored locally, reading only the columns wanted.

    The national files run to gigabytes - the facility file alone is 2.3 GB -
    and every caller wants a handful of their columns, so the selection is made
    by the reader rather than after the whole file is in memory.
    """
    log.info(f'loading {file_name} from {FRSpath}')
    return pd.read_csv(FRSpath / file_name, usecols=list(col_dict),
                       dtype=col_dict)


def store_fm_file(df, file_name, category='', sources=None):
    """Store the facilitymatcher file to local directory."""
    meta = set_facilitymatcher_meta(file_name, category)
    method_path = output_dir / meta.category
    try:
        log.info(f'saving {meta.name_data} to {method_path}')
        write_df_to_file(df, paths, meta)
        metadata_dict = {}
        if not sources:
            sources = []
        for source in sources:
            metadata_dict[source] = read_source_metadata(paths,
                set_facilitymatcher_meta(strip_file_extension(source),
                                         ext_folder),
                force_JSON=True)['tool_meta']
        write_fm_metadata(file_name, metadata_dict)
    except OSError:
        log.error('Failed to save inventory')


def get_fm_file(file_name, download_if_missing=False):
    """Read facilitymatcher file, if not present, generate it.
    :param file_name: str, can be 'FacilityMatchList_forStEWI' or
        'FRS_NAICSforStEWI'
    :param download_if_missing: bool, if True will attempt to load from
        remote server prior to generating if file not found locally
    """
    file_meta = set_facilitymatcher_meta(file_name, category='')
    df = load_preprocessed_output(file_meta, paths)
    if df is None:
        log.info(f'{file_name} not found in {output_dir}, '
                 'writing facility matches to file')
        if download_if_missing:
            download_from_remote(file_meta, paths)
        elif file_name == 'FacilityMatchList_forStEWI':
            write_fm.write_facility_matches()
        elif file_name == 'FRS_NAICSforStEWI':
            write_naics.write_NAICS_matches()
        df = load_preprocessed_output(file_meta, paths)
    col_dict = {"FRS_ID": "str",
                "FacilityID": "str",
                "NAICS": "str"}
    for k, v in col_dict.items():
        if k in df:
            df[k] = df[k].astype(v)
    return df


def get_canonical_registry_map(bridges=None):
    """Registry records that fold onto another because they are one site.

    One site can hold more than one FRS registry record, which leaves the
    programmes registered under each of them looking like separate facilities.
    See :mod:`facilitymatcher.colocation`. The map is the same for every output
    that uses it, so it is computed once per process; switch it off with
    ``colocation: enabled: false`` in config.yaml and it is empty.

    :param bridges: df of the FRS bridge already filtered to the StEWI
        programs, to save reading the 1.1 GB file again when a caller has it
    """
    global _canonical_registry_map
    settings = colocation.colocation_config(FRS_config)
    if settings is None:
        log.info('colocated registry records are switched off')
        return {}
    if _canonical_registry_map is None:
        if bridges is None:
            bridges = filter_by_program_list(
                download_and_read_frs_file('FRS_bridge_file'),
                get_programs_for_inventory_list(stewi_inventories))
        facilities = download_and_read_frs_file('FRS_facility_file')
        naics = download_and_read_frs_file('FRS_NAICS_file')
        programs = filter_by_program_list(
            download_and_read_frs_file('FRS_program_file'),
            get_programs_for_inventory_list(stewi_inventories))
        extra = quantity_registry_pairs(bridges)
        _canonical_registry_map = colocation.canonical_registry_map(
            bridges, facilities, naics, programs, extra_pairs=extra, **settings)
    return _canonical_registry_map


def quantity_registry_pairs(bridges):
    """Registry pairs two inventories agree on a quantity for, or an empty set.

    The one rule in facilitymatcher that reads the inventories rather than the
    FRS files. See :mod:`facilitymatcher.quantity` for why a quantity is safe
    to use only alongside proximity, and what is recorded about which
    comparisons ran.
    """
    settings = quantity.quantity_config(FRS_config)
    if settings is None:
        log.info('quantity matching is switched off')
        return set()
    pairs, ran = quantity.quantity_registry_pairs(
        bridges, settings, inventory_to_FRS_pgm_acronymn)
    global _quantity_comparisons
    _quantity_comparisons = ran
    return pairs


def write_fm_metadata(file_name, metadata_dict, category=''):
    """Generate and store metadata for facility matcher file."""
    meta = set_facilitymatcher_meta(file_name, category=category)
    meta.tool_meta = metadata_dict
    write_metadata_to_file(paths, meta)


#Only can be applied before renaming the programs to inventories
def filter_by_program_list(df, program_list):
    df = df[df['PGM_SYS_ACRNM'].isin(program_list)]
    return df


#Only can be applied after renaming the programs to inventories
def filter_by_inventory_list(df, inventory_list):
    df = df[df['Source'].isin(inventory_list)].reset_index(drop=True)
    return df


#Only can be applied after renaming the programs to inventories
def filter_by_inventory_id_list(df, inventories_of_interest,
                                base_inventory, id_list):
    # Find FRS_IDs first
    FRS_ID_list = list(df.loc[(df['Source'] == base_inventory) &
                              (df['FacilityID'].isin(id_list)), "FRS_ID"])
    # Now use that FRS_ID list and list of inventories of interest to get decired matches
    df = df.loc[(df['Source'].isin(inventories_of_interest)) &
                (df['FRS_ID'].isin(FRS_ID_list))]
    return df


def filter_by_facility_list(df, facility_list):
    df = df[df['FRS_ID'].isin(facility_list)]
    return df


def get_programs_for_inventory_list(list_of_inventories):
    """Return list of program acronymns for passed inventories."""
    program_list = [p for i, p in inventory_to_FRS_pgm_acronymn.items() if
                    i in list_of_inventories]
    return program_list


def invert_inventory_to_FRS():
    FRS_to_inventory_pgm_acronymn = {v: k for k, v in
                                     inventory_to_FRS_pgm_acronymn.items()}
    return FRS_to_inventory_pgm_acronymn


def add_manual_matches(df_matches):
    #Read in manual matches
    manual_matches = pd.read_csv(DATA_PATH.joinpath('facilitymatches_manual.csv'),
                                 header=0,
                                 dtype={'FacilityID': 'str', 'FRS_ID': 'str'})
    #Append with list and drop any duplicates
    df_matches = pd.concat([df_matches, manual_matches], sort=False)
    df_matches = df_matches[~df_matches.duplicated(keep='first')]
    df_matches = df_matches.reset_index(drop=True)
    return df_matches
