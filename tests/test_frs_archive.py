"""Test locating and reading the FRS combined national file.

The real file is 1.3 GB, so these build a two-member stand-in and check the
paths that decide whether it is downloaded again.
"""

import zipfile

import pandas as pd
import pytest

import facilitymatcher.globals as fmg


BRIDGE = """REGISTRY_ID,PGM_SYS_ACRNM,PGM_SYS_ID,INTEREST_TYPE
110000491735,E-GGRT,1001234,AIR MAJOR
110000491735,EIS,12663611,AIR MAJOR
"""
FACILITIES = """REGISTRY_ID,PRIMARY_NAME,LOCATION_ADDRESS,CITY_NAME,\
STATE_CODE,POSTAL_CODE,LATITUDE83,LONGITUDE83,SITE_TYPE_NAME
110000491735,FLINT HILLS,1076 OCEAN DOCK RD,ANCHORAGE,AK,995011199,\
61.229579,-149.893094,STATIONARY
"""


@pytest.fixture
def archive(tmp_path):
    """A stand-in for the FRS combined national zip, dated 08-Sep-2026."""
    path = tmp_path / 'national_combined.zip'
    stamp = (2026, 9, 8, 14, 41, 6)
    with zipfile.ZipFile(path, 'w') as zip_file:
        for name, body in ((fmg.FRS_config['FRS_bridge_file'], BRIDGE),
                           (fmg.FRS_config['FRS_facility_file'], FACILITIES)):
            zip_file.writestr(zipfile.ZipInfo(name, stamp), body)
    return path


@pytest.fixture
def frs_path(monkeypatch, tmp_path):
    """Point the FRS data directory at tmp_path."""
    monkeypatch.setattr(fmg, 'FRSpath', tmp_path / 'FRS Data Files')
    monkeypatch.delenv('FRS_COMBINED_ARCHIVE', raising=False)
    return fmg.FRSpath


def test_resolve_prefers_the_argument(archive, frs_path, monkeypatch):
    monkeypatch.setenv('FRS_COMBINED_ARCHIVE', 'no-such-file.zip')
    assert fmg.resolve_frs_archive(str(archive)) == archive


def test_resolve_falls_back_to_the_environment(archive, frs_path, monkeypatch):
    monkeypatch.setenv('FRS_COMBINED_ARCHIVE', str(archive))
    assert fmg.resolve_frs_archive() == archive


def test_resolve_falls_back_to_where_a_download_leaves_it(archive, frs_path):
    frs_path.mkdir(parents=True)
    kept = frs_path / 'national_combined.zip'
    kept.write_bytes(archive.read_bytes())
    assert fmg.resolve_frs_archive() == kept


def test_resolve_returns_none_when_there_is_no_copy(frs_path):
    assert fmg.resolve_frs_archive() is None


def test_the_version_is_the_date_inside_the_zip(archive):
    """Not the mtime: a copy moved to another machine keeps its FRS date."""
    assert fmg.frs_archive_version(archive) == '08-Sep-2026'


def test_a_local_copy_is_extracted_without_a_download(archive, frs_path,
                                                      monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError('the archive on hand should have been used')
    monkeypatch.setattr(fmg, 'download_FRS_combined_national', fail)
    monkeypatch.setenv('FRS_COMBINED_ARCHIVE', str(archive))
    frs_path.mkdir(parents=True)

    fmg.download_extract_FRS_combined_national(
        fmg.FRS_config['FRS_bridge_file'])
    extracted = frs_path / fmg.FRS_config['FRS_bridge_file']
    assert extracted.exists()
    assert pd.read_csv(extracted)['PGM_SYS_ACRNM'].tolist() == ['E-GGRT',
                                                                'EIS']


def test_only_the_columns_asked_for_are_read(archive, frs_path, monkeypatch):
    monkeypatch.setenv('FRS_COMBINED_ARCHIVE', str(archive))
    frs_path.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zip_file:
        zip_file.extract(fmg.FRS_config['FRS_facility_file'], path=frs_path)

    df = fmg.read_FRS_file(fmg.FRS_config['FRS_facility_file'],
                           {'REGISTRY_ID': 'str', 'LATITUDE83': 'float'})
    assert list(df.columns) == ['REGISTRY_ID', 'LATITUDE83']
    assert df['REGISTRY_ID'].tolist() == ['110000491735']
    assert df['LATITUDE83'].tolist() == [61.229579]
