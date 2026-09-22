"""Test the rules that fold FRS registry records describing one site.

The real national facility file is 2.3 GB, so these build the situations the
rules exist for: a site registered twice under two programs, a tenant at a
host site that must stay separate, and the junk FRS leaves behind when it has
no location.
"""

import pandas as pd
import pytest

import facilitymatcher.colocation as colo


def facility(registry, name, address, state='TX', lat=29.7, lon=-95.3,
             zipcode='77002'):
    return {'REGISTRY_ID': registry, 'PRIMARY_NAME': name,
            'LOCATION_ADDRESS': address, 'CITY_NAME': 'HOUSTON',
            'STATE_CODE': state, 'POSTAL_CODE': zipcode,
            'LATITUDE83': lat, 'LONGITUDE83': lon}


def program(registry, acronym, name, address, state='TX',
            zipcode='77002'):
    """A row of the national program file: no coordinates, one per registration."""
    return {'REGISTRY_ID': registry, 'PGM_SYS_ACRNM': acronym,
            'PGM_SYS_ID': f'{acronym}-{registry}', 'PRIMARY_NAME': name,
            'LOCATION_ADDRESS': address, 'CITY_NAME': 'HOUSTON',
            'STATE_CODE': state, 'POSTAL_CODE': zipcode}


def frame(*rows):
    return pd.DataFrame(list(rows))


def bridge(*pairs):
    return pd.DataFrame([{'REGISTRY_ID': r, 'PGM_SYS_ACRNM': p,
                          'PGM_SYS_ID': f'{p}-{r}'} for r, p in pairs])


def naics_frame(*pairs):
    return pd.DataFrame([{'REGISTRY_ID': r, 'PGM_SYS_ACRNM': 'EIS',
                          'NAICS_CODE': c, 'PRIMARY_INDICATOR': 'PRIMARY'}
                         for r, c in pairs])


def test_normalize_name_drops_trailing_corporate_suffixes():
    out = colo.normalize_name(pd.Series(['Acme Steel, LLC', 'ACME STEEL',
                                         'Acme & Co. Inc']))
    assert out[0] == out[1] == 'ACME STEEL'
    assert out[2] == 'ACME AND'


def test_normalize_address_folds_street_types_and_drops_units():
    out = colo.normalize_address(pd.Series(['100 North Main Street',
                                            '100 N MAIN ST',
                                            '100 N Main St., Suite 400']))
    assert set(out) == {'100 N MAIN ST'}


def test_uninformative_values_become_empty():
    assert colo.normalize_name(pd.Series(['Unknown']))[0] == ''
    assert colo.normalize_address(pd.Series(['N/A']))[0] == ''


def test_name_tokens_ignore_words_every_facility_has():
    assert colo.name_tokens('MIDLAND COGENERATION POWER PLANT') == {
        'MIDLAND', 'COGENERATION'}


def test_two_programs_at_one_address_are_one_site():
    facilities = frame(
        facility('1', 'Gulf Refining LLC', '1 Refinery Rd'),
        facility('2', 'Gulf Refining Company', '1 REFINERY ROAD'))
    pairs = colo.colocated_registry_pairs(facilities)
    assert list(zip(pairs['left'], pairs['right'])) == [('1', '2')]
    assert pairs['basis'].tolist() == ['address']


def test_a_tenant_at_the_host_address_is_left_alone():
    """The failure mode an address-only rule has: a contractor on the site."""
    facilities = frame(
        facility('1', 'US Steel Gary Works', '1 N Broadway'),
        facility('2', 'Fritz Enterprises', '1 North Broadway'))
    assert colo.colocated_registry_pairs(facilities).empty


def test_a_tenant_sharing_the_sector_is_folded_in():
    """NAICS is the other half of the corroboration, for a renamed operator."""
    facilities = frame(
        facility('1', 'Wygen I', '13151 Highway 51'),
        facility('2', 'Neil Simpson Complex', '13151 HWY 51'))
    naics = naics_frame(('1', '221112'), ('2', '221112'))
    pairs = colo.colocated_registry_pairs(facilities, naics)
    assert len(pairs) == 1
    assert colo.colocated_registry_pairs(facilities).empty


def test_the_same_address_in_two_towns_is_not_one_site():
    facilities = frame(
        facility('1', 'Acme Plant', '100 Main St', lat=29.7, lon=-95.3),
        facility('2', 'Acme Plant', '100 MAIN STREET', lat=32.8, lon=-96.8))
    assert colo.colocated_registry_pairs(facilities).empty


def test_an_address_with_no_house_number_is_not_a_location():
    facilities = frame(
        facility('1', 'Alpha', 'Industrial Park'),
        facility('2', 'Beta', 'INDUSTRIAL PARK'))
    assert colo.colocated_registry_pairs(facilities).empty


def test_the_name_rule_needs_coordinates_on_both_sides():
    near = frame(
        facility('1', 'Springfield Cogen', '1 A St', lat=29.700, lon=-95.300),
        facility('2', 'SPRINGFIELD COGEN', '2 B St', lat=29.702, lon=-95.302))
    assert colo.colocated_registry_pairs(near)['basis'].tolist() == ['name']

    unlocated = near.copy()
    unlocated.loc[1, ['LATITUDE83', 'LONGITUDE83']] = None
    assert colo.colocated_registry_pairs(unlocated).empty


def test_the_name_rule_stops_at_the_distance_threshold():
    far = frame(
        facility('1', 'Springfield Cogen', '1 A St', lat=29.70, lon=-95.30),
        facility('2', 'SPRINGFIELD COGEN', '2 B St', lat=29.95, lon=-95.30))
    assert colo.colocated_registry_pairs(far).empty


def test_a_missing_coordinate_does_not_block_the_address_rule():
    facilities = frame(
        facility('1', 'Gulf Refining', '1 Refinery Rd'),
        facility('2', 'Gulf Refining', '1 REFINERY ROAD'))
    facilities.loc[1, ['LATITUDE83', 'LONGITUDE83']] = None
    assert len(colo.colocated_registry_pairs(facilities)) == 1


def test_null_island_is_not_a_coordinate():
    facilities = frame(
        facility('1', 'Springfield Cogen', '1 A St', lat=0.0, lon=0.0),
        facility('2', 'SPRINGFIELD COGEN', '2 B St', lat=0.0, lon=0.0))
    assert colo.colocated_registry_pairs(facilities).empty


def test_a_generic_address_held_by_many_records_is_left_alone():
    facilities = frame(*[facility(str(i), f'Store {i}', '1 Main St')
                         for i in range(60)])
    assert colo.colocated_registry_pairs(facilities,
                                         max_block_size=40).empty


def test_the_record_with_the_most_programs_survives():
    facilities = frame(
        facility('900', 'Gulf Refining', '1 Refinery Rd'),
        facility('100', 'Gulf Refining', '1 REFINERY ROAD'))
    bridges = bridge(('900', 'E-GGRT'), ('900', 'TRIS'), ('100', 'EIS'))
    assert colo.canonical_registry_map(bridges, facilities) == {'100': '900'}


def test_ties_break_on_the_lowest_identifier_so_a_rebuild_repeats():
    facilities = frame(
        facility('900', 'Gulf Refining', '1 Refinery Rd'),
        facility('100', 'Gulf Refining', '1 REFINERY ROAD'))
    bridges = bridge(('900', 'E-GGRT'), ('100', 'EIS'))
    assert colo.canonical_registry_map(bridges, facilities) == {'900': '100'}


def test_a_campus_of_many_records_is_left_alone():
    facilities = frame(*[facility(str(i), 'Big Campus', '1 Campus Dr')
                         for i in range(20)])
    bridges = bridge(*[(str(i), 'EIS') for i in range(20)])
    assert colo.canonical_registry_map(bridges, facilities,
                                       max_cluster_size=12) == {}


def test_registry_records_with_no_stewi_program_are_not_considered():
    facilities = frame(
        facility('1', 'Gulf Refining', '1 Refinery Rd'),
        facility('2', 'Gulf Refining', '1 REFINERY ROAD'))
    assert colo.canonical_registry_map(bridge(('1', 'E-GGRT')),
                                       facilities) == {}


def test_apply_canonical_map_rewrites_only_what_moved():
    df = pd.DataFrame({'REGISTRY_ID': ['100', '900', '700']})
    out = colo.apply_canonical_map(df, {'100': '900'})
    assert out['REGISTRY_ID'].tolist() == ['900', '900', '700']
    assert colo.apply_canonical_map(df, {})['REGISTRY_ID'].tolist() == [
        '100', '900', '700']


@pytest.mark.parametrize('settings, expected', [
    ({'enabled': False, 'max_cluster_size': 12}, None),
    ({}, None),
    ({'enabled': True, 'max_cluster_size': 12}, {'max_cluster_size': 12}),
])
def test_colocation_config_reads_the_switch(settings, expected):
    assert colo.colocation_config({'colocation': settings}) == expected


def test_the_program_file_finds_what_the_curated_record_hides():
    """FRS holds one curated address per registry; each program reported its own.

    The failure this exists for: two registry records whose curated addresses
    were normalised apart, where the programs both reported the same street.
    """
    facilities = frame(
        facility('1', 'Gulf Refining', 'Refinery Road'),
        facility('2', 'Gulf Refining Terminal', 'State Highway 12'))
    assert colo.colocated_registry_pairs(facilities).empty

    programs = frame(
        program('1', 'E-GGRT', 'Gulf Refining', '1 Refinery Rd'),
        program('2', 'EIS', 'Gulf Refining Terminal', '1 REFINERY ROAD'))
    pairs = colo.colocated_registry_pairs(facilities, programs=programs)
    assert pairs['basis'].tolist() == ['program address']


def test_the_program_rule_keeps_the_tenant_out_too():
    facilities = frame(facility('1', 'Alpha', 'A St'),
                       facility('2', 'Beta', 'B St'))
    programs = frame(
        program('1', 'E-GGRT', 'US Steel Gary Works', '1 N Broadway'),
        program('2', 'EIS', 'Fritz Enterprises', '1 North Broadway'))
    assert colo.colocated_registry_pairs(facilities, programs=programs).empty


def test_the_same_address_in_two_postcodes_is_not_one_site():
    """What stands in for coordinates on the program file, which has none."""
    programs = frame(
        program('1', 'E-GGRT', 'Acme Plant', '100 Main St', zipcode='77002'),
        program('2', 'EIS', 'Acme Plant', '100 MAIN STREET', zipcode='75201'))
    facilities = frame(facility('1', 'Alpha', 'A St'),
                       facility('2', 'Beta', 'B St'))
    assert colo.colocated_registry_pairs(facilities, programs=programs).empty


def test_a_missing_postcode_is_not_held_against_a_pair():
    programs = frame(
        program('1', 'E-GGRT', 'Acme Plant', '100 Main St', zipcode='77002'),
        program('2', 'EIS', 'Acme Plant', '100 MAIN STREET', zipcode=None))
    facilities = frame(facility('1', 'Alpha', 'A St'),
                       facility('2', 'Beta', 'B St'))
    assert len(colo.colocated_registry_pairs(facilities,
                                             programs=programs)) == 1


def test_the_program_file_needs_no_coordinates():
    programs = frame(
        program('1', 'E-GGRT', 'Gulf Refining', '1 Refinery Rd'),
        program('2', 'EIS', 'Gulf Refining', '1 REFINERY ROAD'))
    assert 'LATITUDE83' not in programs
    assert len(colo.colocated_registry_pairs(
        frame(facility('1', 'A', 'A St'), facility('2', 'B', 'B St')),
        programs=programs)) == 1
