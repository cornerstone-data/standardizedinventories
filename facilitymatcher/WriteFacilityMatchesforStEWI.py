"""
This script gets FRS data in the form of the FRS combined national files
It uses the bridges in the 'NATIONAL_ENVIRONMENTAL_INTEREST_FILE.CSV'
It writes facility matching file for StEWI
(github.com/usepa/standardizedinventories) inventories

Registry records that describe one physical site are folded together first,
using the facility attributes in 'NATIONAL_FACILITY_FILE.CSV' - see
facilitymatcher.colocation.
"""

import pandas as pd

import facilitymatcher.colocation as colo
import facilitymatcher.globals as fmg

def write_facility_matches():
    FRS_Bridges = fmg.download_and_read_frs_file('FRS_bridge_file')
    # Programs of interest
    stewi_programs = fmg.get_programs_for_inventory_list(fmg.stewi_inventories)

    # Limit to EPA programs of interest for StEWI
    stewi_bridges = fmg.filter_by_program_list(FRS_Bridges, stewi_programs)

    # One site can hold more than one FRS registry record, which leaves the
    # programs registered under each of them unmatched. Fold those onto one
    # registry record before anything reads FRS_ID as an identifier for a site.
    canonical = fmg.get_canonical_registry_map(stewi_bridges)
    stewi_bridges = colo.apply_canonical_map(stewi_bridges, canonical)
    FRS_Bridges = colo.apply_canonical_map(FRS_Bridges, canonical)

    # Separate out eGRID and EIA-860 matches to identify EIA matches to
    # add to eGRID set
    eia_bridges = fmg.filter_by_program_list(FRS_Bridges, ['EIA-860'])
    egrid_bridges = fmg.filter_by_program_list(FRS_Bridges, ['EGRID'])

    # get a list of all FRS in each
    eia_unique_frs = set(list(pd.unique(eia_bridges['REGISTRY_ID'])))
    egrid_unique_frs = set(list(pd.unique(egrid_bridges['REGISTRY_ID'])))

    eia_not_in_egrid = eia_unique_frs - egrid_unique_frs
    eia_to_add = eia_bridges[eia_bridges['REGISTRY_ID'].isin(
        eia_not_in_egrid)].reset_index(drop=True)

    # Rename to EGRID and add the subset back at the top so that EIA-860 is
    # preferred to eGRID data for improved matching
    eia_to_add['PGM_SYS_ACRNM'] = 'EGRID'
    stewi_bridges = pd.concat([eia_to_add, stewi_bridges], ignore_index=True)
    stewi_bridges = stewi_bridges.drop_duplicates()
    stewi_bridges.reset_index(drop=True)

    # Replace program acronymn with inventory acronymn
    program_to_inventory = fmg.invert_inventory_to_FRS()
    stewi_bridges['PGM_SYS_ACRNM'] = stewi_bridges['PGM_SYS_ACRNM'].replace(
        to_replace=program_to_inventory)

    stewi_bridges = stewi_bridges.rename(columns={'REGISTRY_ID': 'FRS_ID',
                                                  'PGM_SYS_ACRNM': 'Source',
                                                  'PGM_SYS_ID': 'FacilityID'})

    # Add in manual matches
    stewi_bridges = fmg.add_manual_matches(stewi_bridges)

    # Write matches to bridge
    sources = [fmg.FRS_config['FRS_bridge_file']]
    if canonical:
        sources.append(fmg.FRS_config['FRS_facility_file'])
        sources.append(fmg.FRS_config['FRS_program_file'])
    fmg.store_fm_file(stewi_bridges, 'FacilityMatchList_forStEWI',
                       sources=sources)


if __name__ == '__main__':
    write_facility_matches()
