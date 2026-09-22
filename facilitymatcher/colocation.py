# colocation.py (facilitymatcher)
# !/usr/bin/env python3
# coding=utf-8
"""
Resolve FRS registry records that describe one physical site.

The facility match list is the FRS bridge and nothing else: a program
identifier is matched to whatever ``REGISTRY_ID`` FRS filed it under. That
works whenever FRS has put every program at a site on one registry record, and
fails silently whenever it has not - a refinery whose Greenhouse Gas Reporting
Program identifier sits on one registry record and whose Emissions Inventory
System identifier sits on another is, to anything reading the match list, two
facilities. Code that sums inventories and deduplicates on ``FRS_ID`` then
counts that refinery twice.

FRS leaves no direct evidence of the split - a program identifier appears under
exactly one registry record, with no exceptions in the September 2026 national
file - so the duplicates have to be recognised from the facility attributes.

**The rule, and where its thresholds come from.** Graded against the 3,402
GHGRP/NEI facility pairs FRS *does* link in 2022, using each program's own
reported attributes rather than FRS's:

- the two programs' coordinates for one site differ by a median of 184 m, and
  agree within 1 km for only 90% of pairs - so coordinates identify a site at
  the kilometre scale, not the metre scale, and cannot carry a match alone;
- the normalised street address is identical for **77%** of them, which is the
  single strongest signal;
- the normalised name is identical for only **33%**, because a plant name and
  an operator name are both valid: *Wygen I* and *Neil Simpson Complex*.

An address on its own over-merges, and it over-merges in a specific way: the
false pairs are **tenants and contractors at a host site** - a slag processor
at a steel mill, an industrial gas plant at a refinery. Those are separate
facilities that happen to share a street address, and folding them together
would delete their emissions rather than deduplicate them. Two cheap tests
separate them. True pairs share at least one name token 91.8% of the time and
agree on the first two NAICS digits 94.9% of the time; tenant pairs, 38.4% and
47.1%. Requiring either one keeps 76.0% of the recoverable pairs at 97.1%
precision, against 77.1% at 95.0% with no corroboration at all.
"""

import numpy as np
import pandas as pd

from stewi.globals import log


CORPORATE_SUFFIXES = (
    'INCORPORATED', 'CORPORATION', 'COMPANY', 'LIMITED', 'HOLDINGS',
    'PARTNERSHIP', 'INC', 'CORP', 'LLC', 'LLP', 'LTD', 'PLC', 'LP', 'CO',
)
STREET_ABBREVIATIONS = {
    'STREET': 'ST', 'AVENUE': 'AVE', 'ROAD': 'RD', 'DRIVE': 'DR',
    'BOULEVARD': 'BLVD', 'HIGHWAY': 'HWY', 'PARKWAY': 'PKWY', 'LANE': 'LN',
    'COURT': 'CT', 'CIRCLE': 'CIR', 'PLACE': 'PL', 'TERRACE': 'TER',
    'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
    'NORTHEAST': 'NE', 'NORTHWEST': 'NW', 'SOUTHEAST': 'SE',
    'SOUTHWEST': 'SW', 'ROUTE': 'RT', 'RTE': 'RT', 'US': 'RT', 'SR': 'RT',
}
# Words too common in a facility name to count as agreement between two of them
NAME_STOPWORDS = frozenset((
    'PLANT', 'FACILITY', 'STATION', 'WORKS', 'MILL', 'PLT', 'SITE', 'CENTER',
    'CENTRE', 'COMPLEX', 'PROJECT', 'TERMINAL', 'REFINERY', 'GENERATING',
    'ENERGY', 'POWER', 'ELECTRIC', 'GAS', 'OIL', 'CHEMICAL', 'CHEMICALS',
    'STEEL', 'CEMENT', 'PAPER', 'MANUFACTURING', 'PRODUCTS', 'INDUSTRIES',
    'SERVICES', 'GROUP', 'NORTH', 'SOUTH', 'EAST', 'WEST', 'CITY', 'COUNTY',
    'THE', 'AND', 'OF', 'NO', 'NUMBER', 'UNIT', 'US', 'USA', 'INTERNATIONAL',
    'NATIONAL', 'AMERICAN', 'AMERICA', 'INC', 'LLC', 'CO', 'CORP',
))
UNINFORMATIVE = frozenset((
    '', 'NA', 'N A', 'NONE', 'UNKNOWN', 'NOT APPLICABLE', 'NOT AVAILABLE',
    'NOT REPORTED', 'UNNAMED', 'NO ADDRESS', 'SEE COMMENTS', 'TBD',
    'CONFIDENTIAL', 'VARIOUS', 'SAME',
))
EARTH_RADIUS_KM = 6371.0
NAICS_DIGITS = 3
MAX_BLOCK_SIZE = 40


def _normalize(series):
    """Upper-case, strip punctuation and collapse whitespace."""
    return (series.fillna('').astype(str).str.upper()
            .str.replace('&', ' AND ', regex=False)
            .str.replace(r'[^A-Z0-9]+', ' ', regex=True)
            .str.replace(r'\s+', ' ', regex=True).str.strip())


def normalize_name(series):
    """Normalise a facility name to a comparison key.

    Corporate suffixes are dropped from the end, because FRS holds the same
    site as both ``ACME STEEL LLC`` and ``ACME STEEL``. Only from the end:
    ``CO`` inside a name is usually part of it.
    """
    out = _normalize(series)
    pattern = r'(?:\s+(?:%s))+$' % '|'.join(CORPORATE_SUFFIXES)
    out = out.str.replace(pattern, '', regex=True).str.strip()
    return out.where(~out.isin(UNINFORMATIVE), '')


def normalize_address(series):
    """Normalise a street address to a comparison key.

    Street types and directions are folded onto one spelling, and anything from
    a unit designator onwards is dropped - two programs at one site routinely
    report different suite or building numbers.
    """
    out = _normalize(series)
    out = out.str.replace(r'\s(?:SUITE|STE|APT|UNIT|BLDG|BUILDING|RM|ROOM|'
                          r'FLOOR|FL|PO BOX|BOX)\s.*$', '', regex=True)
    out = out.str.split().apply(
        lambda words: ' '.join(STREET_ABBREVIATIONS.get(w, w) for w in words))
    return out.where(~out.isin(UNINFORMATIVE), '')


def name_tokens(name):
    """The tokens of a normalised name that say which facility it is."""
    return frozenset(token for token in name.split()
                     if len(token) > 2 and token not in NAME_STOPWORDS
                     and not token.isdigit())


def kilometres_between(lat1, lon1, lat2, lon2):
    """Great-circle distance in km, elementwise over four series."""
    lat1, lon1, lat2, lon2 = (pd.to_numeric(s, errors='coerce')
                              for s in (lat1, lon1, lat2, lon2))
    dlat, dlon = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    haversine = (np.sin(dlat / 2) ** 2
                 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2))
                 * np.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(haversine.clip(0, 1)))


def prepare_facilities(facilities, naics=None):
    """Add the comparison keys the rules are expressed in.

    :param facilities: df of the FRS national facility file
    :param naics: df of the FRS NAICS file, used for the NAICS
        corroboration; None leaves it out and the name token test carries
        the rule
    """
    out = facilities.copy()
    out['name_key'] = normalize_name(out['PRIMARY_NAME'])
    out['addr_key'] = normalize_address(out['LOCATION_ADDRESS'])
    out['tokens'] = out['name_key'].map(name_tokens)
    out['lat'] = pd.to_numeric(out['LATITUDE83'], errors='coerce')
    out['lon'] = pd.to_numeric(out['LONGITUDE83'], errors='coerce')
    out.loc[(out['lat'] == 0) & (out['lon'] == 0), ['lat', 'lon']] = None
    out['sector'] = ''
    if naics is not None:
        primary = naics.sort_values(['PRIMARY_INDICATOR', 'NAICS_CODE'])
        primary = primary.drop_duplicates('REGISTRY_ID')
        codes = primary.set_index('REGISTRY_ID')['NAICS_CODE']
        out['sector'] = (out['REGISTRY_ID'].map(codes)
                         .fillna('').astype(str).str[:NAICS_DIGITS])
    return out


def _pairs_in_blocks(prepared, key, max_block_size=MAX_BLOCK_SIZE):
    """All registry pairs sharing a blocking key, as a two-column frame.

    A key held by more than *max_block_size* registry records is an address or
    a name too generic to identify a site - a state's worth of ``1 MAIN ST`` -
    and is dropped rather than turned into thousands of pairs.
    """
    block = prepared[(prepared[key] != '') & prepared[key].notna()]
    if key == 'addr_key':
        # An address with no house number is a description, not a location
        block = block[block[key].str.match(r'^\d')]
    sizes = (block.groupby(['STATE_CODE', key])['REGISTRY_ID']
             .transform('size'))
    dropped = block[sizes > max_block_size]
    if not dropped.empty:
        log.info('%s registry records sit on a %s shared by more than %s '
                 'records in one state and are left alone',
                 f'{len(dropped):,}', key, max_block_size)
    block = block[(sizes > 1) & (sizes <= max_block_size)]
    pairs = block.merge(block, on=['STATE_CODE', key], suffixes=('_a', '_b'))
    return pairs[pairs['REGISTRY_ID_a'] < pairs['REGISTRY_ID_b']]


def _within(pairs, max_km, required=False):
    """Pairs whose coordinates agree to within *max_km*.

    :param required: when False a missing coordinate is not held against a
        pair - it is absence of evidence, and the rule that allows it has a
        street address carrying it. The name rule has nothing else, so it
        requires the coordinates: a name repeated within one state is common
        enough that unlocated pairs of them are mostly not one site.
    """
    distance = kilometres_between(pairs['lat_a'], pairs['lon_a'],
                                  pairs['lat_b'], pairs['lon_b'])
    if required:
        return pairs[distance <= max_km]
    return pairs[distance.isna() | (distance <= max_km)]


def _corroborated(pairs):
    """Pairs that share a name token or a NAICS prefix.

    This is what separates a duplicate registration from a tenant at the same
    address. See the module docstring for the rates it was set on.
    """
    shares_token = [bool(a & b) for a, b in zip(pairs['tokens_a'],
                                                pairs['tokens_b'])]
    shares_sector = ((pairs['sector_a'] == pairs['sector_b'])
                     & (pairs['sector_a'] != ''))
    return pairs[pd.Series(shares_token, index=pairs.index) | shares_sector]


def colocated_registry_pairs(facilities, naics=None, address_max_km=5,
                             name_max_km=1, max_block_size=MAX_BLOCK_SIZE):
    """Return pairs of registry IDs that describe one physical site.

    Two rules, unioned:

    ``address``
        same state and street address, coordinates within *address_max_km*
        where both have them, and either a shared name token or the same NAICS
        prefix. The address is the strongest signal and the corroboration is
        what keeps tenants at a host site apart.
    ``name``
        same state and facility name, coordinates present on both sides and
        within *name_max_km*. A name repeated exactly within a kilometre does
        not need further corroboration; a name repeated somewhere in a state
        with no coordinates to place it is not evidence of anything, so this
        rule does not admit a missing coordinate the way the address rule
        does.

    :param facilities: df of the FRS national facility file, restricted to the
        registry records of interest
    :param naics: df of the FRS NAICS file, or None
    :return: df with columns 'left', 'right', 'basis'
    """
    prepared = prepare_facilities(facilities, naics)
    found = []
    for basis, key, max_km, corroborate in (
            ('address', 'addr_key', address_max_km, True),
            ('name', 'name_key', name_max_km, False)):
        pairs = _pairs_in_blocks(prepared, key, max_block_size)
        pairs = _within(pairs, max_km, required=not corroborate)
        if corroborate:
            pairs = _corroborated(pairs)
        log.info('%s registry pairs on the %s rule', f'{len(pairs):,}', basis)
        found.append(pd.DataFrame({'left': pairs['REGISTRY_ID_a'].values,
                                   'right': pairs['REGISTRY_ID_b'].values,
                                   'basis': basis}))
    out = pd.concat(found, ignore_index=True)
    return out.drop_duplicates(subset=['left', 'right'])


class _Union:
    """Union-find over registry IDs."""

    def __init__(self):
        self.parent = {}

    def find(self, item):
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root

    def groups(self):
        out = {}
        for item in self.parent:
            out.setdefault(self.find(item), []).append(item)
        return {root: members for root, members in out.items()
                if len(members) > 1}


def canonical_registry_map(bridges, facilities, naics=None,
                           max_cluster_size=12, **rule_settings):
    """Map each duplicated FRS registry ID onto the record its site folds onto.

    The surviving record is always a real FRS registry record - the one of the
    group carrying the most program registrations, ties broken by the lowest
    identifier so that a rebuild gives the same answer.

    :param bridges: df of the FRS bridge, already filtered to the programs of
        interest, with columns 'REGISTRY_ID' and 'PGM_SYS_ACRNM'
    :param facilities: df of the FRS national facility file
    :param naics: df of the FRS NAICS file, or None
    :param max_cluster_size: int, a group of more than this many registry
        records is an industrial park or a campus rather than one site, and is
        left alone
    :return: dict of registry ID -> surviving registry ID, holding only the
        records that move
    """
    of_interest = set(bridges['REGISTRY_ID'])
    facilities = facilities[facilities['REGISTRY_ID'].isin(of_interest)]
    if naics is not None:
        naics = naics[naics['REGISTRY_ID'].isin(of_interest)]
    log.info('resolving colocated registry records over %s registry records '
             'carrying a StEWI program', f'{len(facilities):,}')

    pairs = colocated_registry_pairs(facilities, naics, **rule_settings)
    union = _Union()
    for left, right in zip(pairs['left'], pairs['right']):
        union.union(left, right)

    weight = bridges.groupby('REGISTRY_ID').size().to_dict()
    mapping, oversized = {}, 0
    for members in union.groups().values():
        if len(members) > max_cluster_size:
            oversized += 1
            continue
        canonical = min(members, key=lambda rid: (-weight.get(rid, 0), rid))
        mapping.update({member: canonical for member in members
                        if member != canonical})
    if oversized:
        log.info('%s groups larger than %s registry records were left alone',
                 f'{oversized:,}', max_cluster_size)
    log.info('%s registry records fold onto another, over %s sites',
             f'{len(mapping):,}', f'{len(set(mapping.values())):,}')
    return mapping


def apply_canonical_map(df, mapping, column='REGISTRY_ID'):
    """Replace registry IDs with the record their site folds onto."""
    if not mapping:
        return df
    df = df.copy()
    df[column] = df[column].map(mapping).fillna(df[column])
    return df


def colocation_config(frs_config):
    """Read the colocation settings, or None when it is switched off."""
    settings = dict(frs_config.get('colocation') or {})
    if not settings.pop('enabled', False):
        return None
    return settings
