"""Test folding registry records on what two programs reported at one place.

The risk this rule carries is a coincidence - two facilities far apart that
happen to report the same number - so most of these pin the guards rather than
the match.
"""

import pandas as pd
import pytest

import facilitymatcher.quantity as quantity


def facility(facility_id, name, quantity_kg, state='TX', lat=29.70,
             lon=-95.30, sector='324'):
    return {'FacilityID': facility_id, 'State': state,
            'quantity': quantity_kg,
            'tokens': quantity.name_tokens(name),
            'sector': sector, 'lat': lat, 'lon': lon}


def side(*rows):
    return pd.DataFrame(list(rows)).set_index('FacilityID')


REGISTRY_A = {'a1': '110000000001', 'a2': '110000000003'}
REGISTRY_B = {'b1': '110000000002', 'b2': '110000000004'}


def pairs(left, right, **kwargs):
    return quantity.matched_registry_pairs(left, right, REGISTRY_A, REGISTRY_B,
                                           **kwargs)


def test_one_quantity_at_one_place_is_one_site():
    left = side(facility('a1', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining Company', 1_000_100_000))
    assert pairs(left, right) == {('110000000001', '110000000002')}


def test_the_same_quantity_far_away_is_a_coincidence():
    """The failure this rule would have without proximity."""
    left = side(facility('a1', 'Panda Temple Power', 3_000_000_000,
                         lat=31.10, lon=-97.34, sector='221'))
    right = side(facility('b1', 'Corpus Christi Liquefaction', 3_000_000_000,
                          lat=27.81, lon=-97.40, sector='221'))
    assert pairs(left, right) == set()
    assert pairs(left, right, max_km=500)


def test_a_quantity_that_disagrees_is_not_a_match():
    left = side(facility('a1', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining', 1_050_000_000))
    assert pairs(left, right) == set()
    assert pairs(left, right, tolerance=0.10)


def test_a_different_state_is_never_compared():
    left = side(facility('a1', 'Gulf Refining', 1_000_000_000, state='TX'))
    right = side(facility('b1', 'Gulf Refining', 1_000_000_000, state='AK',
                          lat=29.70, lon=-95.30))
    assert pairs(left, right) == set()


def test_neither_the_name_nor_the_sector_agreeing_blocks_it():
    left = side(facility('a1', 'Alpha Refining', 1_000_000_000, sector='324'))
    right = side(facility('b1', 'Beta Slag Services', 1_000_000_000,
                          sector='562'))
    assert pairs(left, right) == set()


def test_the_sector_alone_is_enough_when_the_operator_is_renamed():
    left = side(facility('a1', 'Wygen I', 1_000_000_000, sector='221'))
    right = side(facility('b1', 'Neil Simpson Complex', 1_000_000_000,
                          sector='221'))
    assert len(pairs(left, right)) == 1


def test_two_candidates_at_one_place_are_no_evidence():
    """An ambiguous quantity cannot identify which facility it belongs to."""
    left = side(facility('a1', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining One', 1_000_000_000),
                 facility('b2', 'Gulf Refining Two', 1_000_000_000))
    assert pairs(left, right) == set()


def test_a_facility_frs_never_registered_cannot_be_folded():
    left = side(facility('unknown', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining', 1_000_000_000))
    assert pairs(left, right) == set()


def test_records_already_on_one_registry_produce_no_pair():
    left = side(facility('a1', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining', 1_000_000_000))
    same = {'b1': REGISTRY_A['a1']}
    assert quantity.matched_registry_pairs(left, right, REGISTRY_A, same) == set()


def test_pairs_come_back_ordered_so_a_rebuild_repeats():
    left = side(facility('a2', 'Gulf Refining', 1_000_000_000))
    right = side(facility('b1', 'Gulf Refining', 1_000_000_000))
    assert quantity.matched_registry_pairs(
        left, right, REGISTRY_A, REGISTRY_B) == {
            ('110000000002', '110000000003')}


@pytest.mark.parametrize('settings, expected', [
    ({'enabled': False, 'max_km': 10}, None),
    ({}, None),
    ({'enabled': True, 'max_km': 10}, {'max_km': 10}),
])
def test_quantity_config_reads_the_switch(settings, expected):
    assert quantity.quantity_config({'quantity_matching': settings}) == expected


def test_a_missing_inventory_year_is_skipped_not_downloaded(monkeypatch):
    def absent(*args, **kwargs):
        raise FileNotFoundError('not built locally')
    monkeypatch.setattr('stewi.getInventory', absent)
    bridges = pd.DataFrame({'REGISTRY_ID': ['110000000001'],
                            'PGM_SYS_ACRNM': ['E-GGRT'],
                            'PGM_SYS_ID': ['a1']})
    found, ran = quantity.quantity_registry_pairs(
        bridges,
        {'comparisons': [{'inventories': ['GHGRP', 'NEI'],
                          'flow': 'Carbon Dioxide', 'years': [1999]}]},
        {'GHGRP': 'E-GGRT', 'NEI': 'EIS'})
    assert found == set()
    assert ran == []
