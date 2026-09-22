"""
This script gets FRS data in the form of the FRS combined national files
It uses the bridges in the 'NATIONAL_NAICS_FILE.CSV'
It writes NAICS by facility for StEWI
"""
import facilitymatcher.colocation as colo
import facilitymatcher.globals as fmg


def write_NAICS_matches():
    FRS_NAICS = fmg.download_and_read_frs_file('FRS_NAICS_file')

    # Filter this list for stewi
    # Programs of interest
    stewi_programs = fmg.get_programs_for_inventory_list(fmg.stewi_inventories)

    # Limit to EPA programs of interest for StEWI
    stewi_NAICS = fmg.filter_by_program_list(FRS_NAICS, stewi_programs)

    # Fold registry records that are one site onto the record the facility
    # match list keeps, so a NAICS lookup by FRS_ID still finds them
    stewi_NAICS = colo.apply_canonical_map(stewi_NAICS,
                                           fmg.get_canonical_registry_map())

    # Drop duplicates
    stewi_NAICS = stewi_NAICS.drop_duplicates()

    # Replace program acronymn with inventory acronymn
    program_to_inventory = fmg.invert_inventory_to_FRS()
    stewi_NAICS['PGM_SYS_ACRNM'] = stewi_NAICS['PGM_SYS_ACRNM'].replace(to_replace=program_to_inventory)

    # Rename columns to be consistent with standards
    stewi_NAICS = stewi_NAICS.rename(columns={'REGISTRY_ID': 'FRS_ID',
                                              'PGM_SYS_ACRNM': 'Source',
                                              'NAICS_CODE': 'NAICS'})
    sources = [fmg.FRS_config['FRS_NAICS_file']]
    if fmg.get_canonical_registry_map():
        sources.append(fmg.FRS_config['FRS_facility_file'])
        sources.append(fmg.FRS_config['FRS_program_file'])
    fmg.store_fm_file(stewi_NAICS, 'FRS_NAICSforStEWI', sources=sources)


if __name__ == '__main__':
    write_NAICS_matches()
