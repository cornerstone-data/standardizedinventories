"""Test the activity by process output built from NEI calculation parameters."""

import pandas as pd

from stewi.NEI import generate_process_activity


def _nei_frame():
    """Three pollutant rows of one process, plus a process with no parameter."""
    return pd.DataFrame({
        'FacilityID': ['1', '1', '1', '2'],
        'UnitID': ['10', '10', '10', '20'],
        'ProcessID': ['100', '100', '100', '200'],
        'Process': ['10200601'] * 3 + ['30100101'],
        'FlowName': ['Carbon Dioxide', 'Nitrogen Oxides', 'Carbon Monoxide', 'Methane'],
        'FlowAmount': [1.0, 2.0, 3.0, 4.0],
        'ActivityType': ['I', 'I', 'I', None],
        'ActivityAmount': ['12.5', '12.5', '12.5', None],
        'ActivityUnit': ['E6FT3', 'E6FT3', 'E6FT3', None],
        'ActivityMaterial': ['209', '209', '209', None],
    })


def test_process_is_the_grain():
    """The parameter repeated across pollutant rows collapses to one row."""
    activity = generate_process_activity(_nei_frame())
    assert len(activity) == 1
    row = activity.iloc[0]
    assert (row.FacilityID, row.UnitID, row.ProcessID) == ('1', '10', '100')
    assert row.ActivityAmount == 12.5
    assert row.ActivityUnit == 'E6FT3'


def test_amount_is_numeric_and_positive():
    """String amounts are coerced; missing, zero and unparseable ones are dropped."""
    df = _nei_frame()
    df.loc[3, ['ActivityAmount', 'ActivityUnit']] = ['0', 'TON']
    df.loc[2, 'ActivityAmount'] = 'not a number'
    activity = generate_process_activity(df)
    assert activity.ActivityAmount.dtype.kind == 'f'
    assert list(activity.ProcessID) == ['100']


def test_two_parameters_on_one_process_both_survive():
    """A source that does report two parameters for a process is not collapsed."""
    df = _nei_frame()
    df.loc[1, ['ActivityAmount', 'ActivityUnit', 'ActivityMaterial']] = ['4', 'TON', '58']
    activity = generate_process_activity(df)
    assert len(activity) == 2
    assert set(activity.ActivityUnit) == {'E6FT3', 'TON'}


def test_codes_read_the_same_across_source_years():
    """Earlier exports type codes as numbers; they still come out as strings."""
    df = _nei_frame()
    df['ActivityMaterial'] = [209.0, 209.0, 209.0, None]
    df['ProcessID'] = [100, 100, 100, 200]
    activity = generate_process_activity(df)
    assert list(activity.ActivityMaterial) == ['209']
    assert list(activity.ProcessID) == ['100']


def test_year_without_the_fields_returns_none():
    """2011 and 2014 exports carry no calculation parameters."""
    df = _nei_frame().drop(columns=['ActivityType', 'ActivityAmount',
                                    'ActivityUnit', 'ActivityMaterial'])
    assert generate_process_activity(df) is None
