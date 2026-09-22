# quantity.py (facilitymatcher)
# !/usr/bin/env python3
# coding=utf-8
"""
Recognise one site from two programs reporting the same quantity at it.

:mod:`facilitymatcher.colocation` folds registry records that FRS describes the
same way. This handles the ones it describes differently. Where two programs
report the same facility, they frequently report the **same number** for it -
an inventory often carries another's figure rather than an independent
measurement - and that number is evidence the addresses and names in FRS do not
contain.

⚠️ **A quantity is never the key, only the fourth axis.** Two facilities a
thousand miles apart reporting the same mass is a coincidence, not a match, and
at national scale coincidences are common. Measured on GHGRP against NEI in
2022, carbon dioxide, using the pairs FRS already links as the answer key:

======================  ==========  =============  =========
gate                    confirmed   contradicted   precision
======================  ==========  =============  =========
state only              658         23             96.6%
state + within 25 km    657         1              99.8%
state + within 10 km    654         **0**          **100%**
======================  ==========  =============  =========

The two populations barely overlap: pairs FRS confirms sit a **median of
0.19 km** apart, pairs FRS contradicts a **median of 293 km**, and the closest
contradicted pair is 11 km. So proximity is what makes the quantity safe to
use, and without it the rule pairs a Texas power station with a Texas liquefied
natural gas terminal because both reported 3.0 Mt.

The rule is therefore: same state, coordinates within *max_km*, quantity within
*tolerance*, **and** the same corroboration the address rule uses - a shared
name token or the same NAICS prefix. On the generic form below, 2022 carbon
dioxide, that is 99.7% precision over 2,282 graded pairs and finds 518 pairs
FRS does not have.

⚠️ **This needs the inventories themselves**, unlike everything else in
facilitymatcher, which is built from the FRS national files alone. A comparison
whose inventory-years are not available locally is skipped with a warning
rather than downloaded, and the metadata records which ones ran.

⚠️ **The match list carries no year, and a quantity does.** A pair is taken
whenever it is found in *any* declared year: a site is a site, and a facility
that fails the threshold in one year is not a different place in the next. The
pairs are stable enough for that to be sound - 85 of 2021's 115 pairs recur in
2022 - but the years used are recorded so a rebuild can repeat them.
"""

import pandas as pd

from stewi.globals import log
from facilitymatcher.colocation import (
    kilometres_between, name_tokens, normalize_name,
)

NAICS_DIGITS = 3


def _facility_frame(inventory, year, flow, min_quantity):
    """One side of a comparison: where a facility is, and what it reported.

    Returns None when the inventory-year is not on hand. Nothing is downloaded
    - a build should not pull gigabytes of inventory because a matching rule is
    switched on.
    """
    import stewi

    # Asked for rather than fetched: stewi GENERATES an inventory it cannot
    # find, which for a year whose sources have gone stale means a long build
    # that fails at the end. A matching rule must not set that off.
    for form in ('flowbyfacility', 'facility'):
        served = stewi.getAvailableInventoriesandYears(form).get(inventory, [])
        if str(year) not in [str(y) for y in served]:
            log.warning('%s %s has no %s stored locally, so quantity matching '
                        'skips it', inventory, year, form)
            return None
    try:
        flows = stewi.getInventory(inventory, year, stewiformat='flowbyfacility')
        facilities = stewi.getInventoryFacilities(inventory, year)
    except Exception as err:  # noqa: BLE001 - any failure means "not on hand"
        log.warning('%s %s could not be read, skipping it in quantity '
                    'matching: %s', inventory, year, err)
        return None
    if flows is None or facilities is None:
        log.warning('%s %s not available locally, skipping it in quantity '
                    'matching', inventory, year)
        return None
    reported = flows[flows['FlowName'] == flow]
    if reported.empty:
        log.warning('%s %s reports no %s, skipping it', inventory, year, flow)
        return None
    quantity = reported.groupby('FacilityID')['FlowAmount'].sum()
    quantity.index = quantity.index.astype(str)

    out = facilities.copy()
    out['FacilityID'] = out['FacilityID'].astype(str)
    out['quantity'] = out['FacilityID'].map(quantity)
    out = out[out['quantity'] > min_quantity]
    out['tokens'] = normalize_name(out['FacilityName']).map(name_tokens)
    out['sector'] = (out['NAICS'].fillna('').astype(str).str[:NAICS_DIGITS])
    for axis, column in (('lat', 'Latitude'), ('lon', 'Longitude')):
        out[axis] = pd.to_numeric(out[column], errors='coerce')
    out = out.dropna(subset=['lat', 'lon', 'State'])
    return out[['FacilityID', 'State', 'quantity', 'tokens', 'sector', 'lat',
                'lon']]


def _corroborated(left, right):
    """A shared name token or the same NAICS prefix, as the address rule asks."""
    return bool(left.tokens & right.tokens) or (
        left.sector == right.sector != '')


def matched_registry_pairs(left, right, left_registry, right_registry,
                           tolerance=0.005, max_km=10):
    """Registry pairs whose two programs report one quantity in one place.

    :param left: df from :func:`_facility_frame` for one inventory, indexed by
        FacilityID
    :param right: df for the other, indexed the same way
    :param left_registry: dict of that inventory's FacilityID -> REGISTRY_ID.
        Held per inventory rather than merged: two programs can use the same
        identifier string for different facilities. A facility FRS has never
        registered cannot be folded onto anything
    :param right_registry: the same for the other inventory
    :param tolerance: float, the fractional agreement two quantities need
    :param max_km: float, how far apart the two reported points may be
    :return: set of (registry, registry) pairs, each ordered
    """
    pairs = set()
    by_state = {state: block for state, block in right.groupby('State')}
    for row in left.itertuples():
        block = by_state.get(row.State)
        if block is None:
            continue
        near = block[block['quantity'].between(row.quantity * (1 - tolerance),
                                               row.quantity * (1 + tolerance))]
        if near.empty:
            continue
        near = near[[_corroborated(row, other) for other in near.itertuples()]]
        if near.empty:
            continue
        distance = kilometres_between(
            pd.Series([row.lat] * len(near), index=near.index),
            pd.Series([row.lon] * len(near), index=near.index),
            near['lat'], near['lon'])
        near = near[distance <= max_km]
        # An ambiguous quantity is no evidence at all: two candidates at one
        # place reporting the same number cannot both be this facility.
        if len(near) != 1:
            continue
        a = left_registry.get(row.Index)
        b = right_registry.get(near.index[0])
        if a and b and a != b:
            pairs.add((a, b) if a < b else (b, a))
    return pairs


def quantity_registry_pairs(bridges, settings, inventory_to_program):
    """Run every comparison declared in config and return the pairs found.

    :param bridges: df of the FRS bridge, filtered to the programs of interest
    :param settings: the ``quantity_matching`` block of config.yaml
    :param inventory_to_program: dict of StEWI inventory -> FRS program acronym
    :return: (set of registry pairs, list of the comparisons that ran)
    """
    tolerance = settings.get('tolerance', 0.005)
    max_km = settings.get('max_km', 10)
    min_quantity = settings.get('min_quantity', 0)
    found, ran = set(), []
    for comparison in settings.get('comparisons') or []:
        inventories = comparison['inventories']
        flow = comparison['flow']
        registry_of = {}
        frames = {}
        for inventory in inventories:
            program = inventory_to_program.get(inventory)
            rows = bridges[bridges['PGM_SYS_ACRNM'] == program]
            registry_of[inventory] = dict(
                zip(rows['PGM_SYS_ID'].astype(str), rows['REGISTRY_ID']))
        for year in comparison.get('years') or []:
            for inventory in inventories:
                frames[inventory] = _facility_frame(inventory, year, flow,
                                                    min_quantity)
            if any(frames[i] is None for i in inventories):
                continue
            left, right = (frames[i].set_index('FacilityID')
                           for i in inventories)
            before = len(found)
            found |= matched_registry_pairs(
                left, right, registry_of[inventories[0]],
                registry_of[inventories[1]], tolerance, max_km)
            ran.append(f'{inventories[0]}-{inventories[1]} {flow} {year}')
            log.info('%s and %s %s, %s: %s registry pairs, %s of them new',
                     inventories[0], inventories[1], year, flow,
                     f'{len(found):,}', f'{len(found) - before:,}')
    if not ran:
        log.info('quantity matching declared no comparison that could run')
    return found, ran


def quantity_config(frs_config):
    """Read the quantity-matching settings, or None when it is switched off."""
    settings = dict(frs_config.get('quantity_matching') or {})
    if not settings.pop('enabled', False):
        return None
    return settings
