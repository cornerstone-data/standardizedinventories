"""Test the GHGRP archive build path on a synthetic Envirofacts views archive.

The real RY2024 archive is ~2.6 GB and not redistributable, so these build a
three-row stand-in that reproduces the properties the code has to cope with:
more than one reporting year in one file, numeric columns written with ten
decimal places, and a top-level directory inside the zip.
"""

import zipfile

import pandas as pd
import pytest

import stewi.GHGRP as GHGRP
import stewi.exceptions


SUBPART_TABLE = 'D_SUBPART_LEVEL_INFORMATION'
FACILITIES = """FACILITY_ID,FACILITY_NAME,ADDRESS1,CITY,STATE,ZIP,COUNTY,\
LATITUDE,LONGITUDE,YEAR,PRIMARY_NAICS_CODE
1000001.0000000000,ALPHA PLANT,1 MAIN ST,FERNDALE,WA,07031,WHATCOM COUNTY,\
48.828707,-122.685533,2024.0000000000,221112.0
1000002.0000000000,BETA PLANT,2 OAK AVE,DUNKIRK,IN,47336,JAY COUNTY,\
40.371053,-85.198134,2024.0000000000,327213.0
1000001.0000000000,ALPHA PLANT,1 MAIN ST,FERNDALE,WA,07031,WHATCOM COUNTY,\
48.828707,-122.685533,2023.0000000000,221112.0
"""
SUBPART = """FACILITY_ID,REPORTING_YEAR,GHG_NAME,GHG_QUANTITY,FACILITY_NAME
1000001.0000000000,2024.0000000000,Carbon Dioxide,1157043.4000000000,ALPHA PLANT
1000002.0000000000,2024.0000000000,Methane,12.5000000000,BETA PLANT
1000001.0000000000,2023.0000000000,Carbon Dioxide,999999.0000000000,ALPHA PLANT
"""


@pytest.fixture
def archive(tmp_path):
    """Write a stand-in archive, one CSV per view under a top-level directory."""
    path = tmp_path / 'EF_Views_test.zip'
    with zipfile.ZipFile(path, 'w') as zip_file:
        zip_file.writestr(f'EF_Views_test/{SUBPART_TABLE.lower()}.csv', SUBPART)
        zip_file.writestr(
            f'EF_Views_test/{GHGRP.EF_FACILITIES_TABLE.lower()}.csv', FACILITIES)
    return path


@pytest.fixture
def archive_year(monkeypatch, tmp_path):
    """Declare 1999 as an archive year and point OUTPUT_PATH at tmp_path."""
    monkeypatch.setitem(GHGRP._config, '1999',
                        {'archive_file_name': 'EF_Views_test.zip',
                         'file_version': '',
                         'source_url': 'https://www.epa.gov/foia',
                         'omitted_subparts': ['E']})
    monkeypatch.setattr(GHGRP, 'OUTPUT_PATH', tmp_path)
    return '1999'


def test_archive_year_config_only_for_declared_years(archive_year):
    assert GHGRP.archive_year_config(archive_year)['omitted_subparts'] == ['E']
    assert GHGRP.archive_year_config(1999)['file_version'] == ''
    assert GHGRP.archive_year_config('2019') is None


def test_resolve_archive_prefers_the_argument(archive_year, archive, monkeypatch):
    monkeypatch.setenv('GHGRP_EF_VIEWS_ARCHIVE', 'no-such-file.zip')
    assert GHGRP.resolve_archive(archive_year, str(archive)) == archive


def test_resolve_archive_falls_back_to_the_environment(archive_year, archive,
                                                       monkeypatch):
    monkeypatch.setenv('GHGRP_EF_VIEWS_ARCHIVE', str(archive))
    assert GHGRP.resolve_archive(archive_year) == archive


def test_resolve_archive_falls_back_to_the_output_path(archive_year, archive,
                                                       monkeypatch, tmp_path):
    monkeypatch.delenv('GHGRP_EF_VIEWS_ARCHIVE', raising=False)
    (tmp_path / 'EF_Views_test.zip').write_bytes(archive.read_bytes())
    assert GHGRP.resolve_archive(archive_year) == tmp_path / 'EF_Views_test.zip'


def test_resolve_archive_names_what_to_do_when_absent(archive_year, monkeypatch):
    monkeypatch.delenv('GHGRP_EF_VIEWS_ARCHIVE', raising=False)
    with pytest.raises(stewi.exceptions.DataNotFoundError, match='EF_Views_test.zip'):
        GHGRP.resolve_archive(archive_year)


def test_resolve_archive_rejects_a_published_year():
    with pytest.raises(stewi.exceptions.InventoryNotAvailableError):
        GHGRP.resolve_archive('2019')


def test_read_archive_table_filters_the_year_and_keeps_ids_integral(archive):
    with zipfile.ZipFile(archive) as zip_file:
        member = next(n for n in zip_file.namelist()
                      if n.endswith(f'{SUBPART_TABLE.lower()}.csv'))
    df = GHGRP.read_archive_table(archive, member, 2024)
    assert len(df) == 2
    assert set(df['REPORTING_YEAR']) == {2024}
    # a FacilityID of '1000001.0' joins to nothing downstream
    assert sorted(df['FACILITY_ID'].astype(str)) == ['1000001', '1000002']
    # everything else stays as written, and is typed when the staged CSV is read
    assert list(df['GHG_QUANTITY']) == ['1157043.4000000000', '12.5000000000']


def test_staged_table_reads_back_with_the_types_the_build_expects(archive,
                                                                 archive_year,
                                                                 tmp_path):
    GHGRP.stage_archive_tables(2024, archive, GHGRP.MetaGHGRP(), [SUBPART_TABLE])
    staged = pd.read_csv(tmp_path / 'tables' / '2024' / f'{SUBPART_TABLE}.csv')
    assert staged['FACILITY_ID'].dtype.kind == 'i'
    assert staged['GHG_QUANTITY'].sum() == pytest.approx(1157055.9)


def test_stage_archive_tables_writes_what_the_api_would_have(archive, archive_year,
                                                             tmp_path):
    GHGRP.stage_archive_tables(archive_year, archive, GHGRP.MetaGHGRP(),
                               [SUBPART_TABLE])
    staged = tmp_path / 'tables' / archive_year / f'{SUBPART_TABLE}.csv'
    assert staged.is_file()
    # a year with no rows still gets a file, so the build reports it as empty
    # rather than trying to download it
    assert len(pd.read_csv(staged)) == 0


def test_stage_archive_tables_records_the_archive_as_the_source(archive,
                                                               archive_year):
    m = GHGRP.MetaGHGRP()
    GHGRP.stage_archive_tables(2024, archive, m, [SUBPART_TABLE])
    assert m.filename == [str(archive)]
    assert m.filetype == ['Static File']


def test_stage_archive_tables_says_which_tables_are_missing(archive, archive_year):
    with pytest.raises(stewi.exceptions.DataNotFoundError, match='C_FUEL_LEVEL_INFORMATION'):
        GHGRP.stage_archive_tables(2024, archive, GHGRP.MetaGHGRP(),
                                   [SUBPART_TABLE, 'C_FUEL_LEVEL_INFORMATION'])


def test_facilities_from_ef_view_maps_to_the_stewi_facility_format(archive,
                                                                  archive_year):
    GHGRP.stage_archive_tables(2024, archive, GHGRP.MetaGHGRP(),
                               [GHGRP.EF_FACILITIES_TABLE])
    facilities = GHGRP.facilities_from_ef_view(2024)
    assert list(facilities.columns) == ['FacilityID', 'FacilityName', 'Address',
                                        'City', 'State', 'Zip', 'Latitude',
                                        'Longitude', 'County', 'NAICS']
    assert len(facilities) == 2
    alpha = facilities.set_index('FacilityID').loc[1000001]
    assert alpha['State'] == 'WA'
    # the data summary spreadsheet path reads Zip as a number and loses this
    assert alpha['Zip'] == '07031'


def test_facilities_from_ef_view_says_to_stage_first(archive_year):
    with pytest.raises(stewi.exceptions.DataNotFoundError, match='Option A'):
        GHGRP.facilities_from_ef_view(2024)


def test_published_spreadsheets_cover_stops_at_the_published_vintage():
    last = GHGRP._config['most_recent_year']
    assert GHGRP.published_spreadsheets_cover(last)
    assert GHGRP.published_spreadsheets_cover(int(last) - 1)
    assert not GHGRP.published_spreadsheets_cover(int(last) + 1)


def test_get_facilities_dispatches_on_the_published_vintage(archive, archive_year,
                                                           monkeypatch):
    """Past the published range the view stands in; inside it the spreadsheet wins."""
    GHGRP.stage_archive_tables(2024, archive, GHGRP.MetaGHGRP(),
                               [GHGRP.EF_FACILITIES_TABLE])
    monkeypatch.setitem(GHGRP._config, '2024', GHGRP._config[archive_year])
    monkeypatch.setitem(GHGRP._config, 'most_recent_year', '2023')
    assert len(GHGRP.get_facilities(2024)) == 2

    called = []
    monkeypatch.setattr(GHGRP, 'facilities_from_data_summaries',
                        lambda year: called.append(year) or pd.DataFrame())
    GHGRP.get_facilities('2019')
    assert called == ['2019']


def test_an_archive_year_inside_the_published_range_keeps_the_spreadsheets(
        archive, archive_year, monkeypatch):
    """Rebuilding a published year from the archive must not drop E/BB/CC/L/O.

    The archive replaces the Envirofacts tables only, so a year the spreadsheets
    still cover keeps its facility source and its spreadsheet-only subparts.
    """
    GHGRP.stage_archive_tables(2024, archive, GHGRP.MetaGHGRP(),
                               [GHGRP.EF_FACILITIES_TABLE])
    # declare 2023 an archive year, the way a rebuild of a published year does
    monkeypatch.setitem(GHGRP._config, '2023', GHGRP._config[archive_year])
    monkeypatch.setitem(GHGRP._config, 'most_recent_year', '2023')

    called = []
    monkeypatch.setattr(GHGRP, 'facilities_from_data_summaries',
                        lambda year: called.append(year) or pd.DataFrame())
    GHGRP.get_facilities('2023')
    assert called == ['2023'], 'an archive year in range must use the spreadsheet'

    monkeypatch.setattr(GHGRP, 'parse_additional_suparts_data',
                        lambda *a, **k: pd.DataFrame({'FlowAmount': [1.0]}))
    monkeypatch.setattr(GHGRP, 'parse_subpart_O',
                        lambda year: pd.DataFrame({'FlowAmount': [2.0]}))
    monkeypatch.setattr(GHGRP, 'parse_subpart_L',
                        lambda year: pd.DataFrame({'FlowAmount': [3.0]}))
    assert len(GHGRP.additional_subpart_frames('2023')) == 3
    # and nothing past the published range
    assert GHGRP.additional_subpart_frames('2024') == []


def test_subpart_L_is_omitted_only_when_the_caller_allows_it(archive_year,
                                                            monkeypatch):
    """A missing GWP lookup omits one subpart for an archive year, fails a normal one."""
    monkeypatch.setitem(GHGRP._config, 'most_recent_year', '2023')
    monkeypatch.setattr(GHGRP, 'parse_additional_suparts_data',
                        lambda *a, **k: pd.DataFrame({'FlowAmount': [1.0]}))
    monkeypatch.setattr(GHGRP, 'parse_subpart_O',
                        lambda year: pd.DataFrame({'FlowAmount': [2.0]}))

    def no_gwp(year):
        raise stewi.exceptions.DataNotFoundError(message='no workbook')

    monkeypatch.setattr(GHGRP, 'parse_subpart_L', no_gwp)
    assert len(GHGRP.additional_subpart_frames('2023', allow_missing_gwp=True)) == 2
    with pytest.raises(stewi.exceptions.DataNotFoundError):
        GHGRP.additional_subpart_frames('2023')


def test_required_tables_covers_2024():
    tables = GHGRP.required_tables('2024')
    assert len(tables) == 30
    # the subpart is what becomes Process, so every table has to carry one
    assert tables['SUBPART'].notna().all()
    assert 'C' in set(tables['SUBPART'])
