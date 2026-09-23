"""Schema gate for stewi.globals.read_inventory."""

import pandas as pd
import pytest

from stewi.formats import StewiFormat
from stewi.globals import (
    WRITE_FORMAT,
    _evict_versioned_locals,
    _missing_required_names,
    paths,
    read_inventory,
    set_stewi_meta,
)


def _facility_shaped():
    """eGRID facility-like frame (no FlowName) — wrong under FLOWBYFACILITY."""
    return pd.DataFrame(
        {
            'FacilityID': ['1', '2'],
            'State': ['TX', 'CA'],
            'FacilityName': ['A', 'B'],
            'Plant primary fuel': ['NG', 'BIT'],
        }
    )


def _fbf_shaped():
    return pd.DataFrame(
        {
            'FacilityID': ['1'],
            'FlowName': ['Electricity'],
            'Compartment': ['air'],
            'FlowAmount': [1.0],
            'Unit': ['MJ'],
            'DataReliability': [1.0],
        }
    )


@pytest.fixture
def stewi_cache(tmp_path, monkeypatch):
    """Point stewi.globals.paths.local_path at an isolated temp dir."""
    local = tmp_path / 'stewi'
    local.mkdir()
    monkeypatch.setattr(paths, 'local_path', local)
    return local


def test_missing_required_names_detects_facility_under_fbf():
    missing = _missing_required_names(_facility_shaped(), StewiFormat.FLOWBYFACILITY)
    assert 'FlowName' in missing
    assert 'FlowAmount' in missing


def test_evict_removes_all_versioned_hashes(stewi_cache):
    cat = stewi_cache / 'flowbyfacility'
    cat.mkdir()
    (cat / f'eGRID_2024_v1.2.2_aaaaaaa.{WRITE_FORMAT}').write_bytes(b'bad')
    (cat / f'eGRID_2024_v1.2.2_bbbbbbb.{WRITE_FORMAT}').write_bytes(b'bad')
    (cat / f'eGRID_2023_v1.2.1_ccccccc.{WRITE_FORMAT}').write_bytes(b'keep')
    meta = set_stewi_meta('eGRID_2024', 'flowbyfacility')
    _evict_versioned_locals(meta)
    remaining = sorted(p.name for p in cat.iterdir())
    assert remaining == [f'eGRID_2023_v1.2.1_ccccccc.{WRITE_FORMAT}']


def test_local_poison_fbf_generates_when_download_false(stewi_cache, monkeypatch):
    cat = stewi_cache / 'flowbyfacility'
    cat.mkdir()
    poison = cat / f'eGRID_2024_v1.2.2_deadbeef.{WRITE_FORMAT}'
    _facility_shaped().to_parquet(poison)

    calls = {'generate': 0, 'gcs': 0}

    def fake_generate(acronym, year):
        calls['generate'] += 1
        assert acronym == 'eGRID' and str(year) == '2024'
        _fbf_shaped().to_parquet(cat / f'eGRID_2024_v1.2.2_goodhash.{WRITE_FORMAT}')

    def fake_gcs(*_a, **_k):
        calls['gcs'] += 1
        return False

    monkeypatch.setattr('stewi.globals.generate_inventory', fake_generate)
    monkeypatch.setattr('stewi.globals.download_prefer_gcs', fake_gcs)

    out = read_inventory('eGRID', 2024, StewiFormat.FLOWBYFACILITY,
                         download_if_missing=False)
    assert out is not None
    assert list(out['FlowName']) == ['Electricity']
    assert calls['generate'] == 1
    assert calls['gcs'] == 0
    assert not poison.exists()


def test_poison_after_gcs_does_not_reenter_gcs(stewi_cache, monkeypatch):
    cat = stewi_cache / 'flowbyfacility'
    cat.mkdir()
    calls = {'generate': 0, 'gcs': 0}

    def fake_gcs(meta, _paths):
        calls['gcs'] += 1
        # First (and only) GCS "success" drops facility-shaped poison.
        if meta.category == 'flowbyfacility':
            _facility_shaped().to_parquet(
                cat / f'eGRID_2024_v1.2.2_fromgcs.{WRITE_FORMAT}'
            )
        return True

    def fake_generate(acronym, year):
        calls['generate'] += 1
        _fbf_shaped().to_parquet(cat / f'eGRID_2024_v1.2.2_regen.{WRITE_FORMAT}')

    monkeypatch.setattr('stewi.globals.download_prefer_gcs', fake_gcs)
    monkeypatch.setattr('stewi.globals.generate_inventory', fake_generate)

    out = read_inventory('eGRID', 2024, StewiFormat.FLOWBYFACILITY,
                         download_if_missing=True)
    assert out is not None
    assert 'FlowName' in out.columns
    assert calls['generate'] == 1
    # inventory + metadata download only — no second inventory GCS after reject
    assert calls['gcs'] == 2


def test_pure_miss_with_download_true_returns_none(stewi_cache, monkeypatch):
    calls = {'generate': 0}

    monkeypatch.setattr(
        'stewi.globals.download_prefer_gcs', lambda *_a, **_k: False
    )

    def boom(*_a, **_k):
        calls['generate'] += 1
        raise AssertionError('must not generate on pure miss + True')

    monkeypatch.setattr('stewi.globals.generate_inventory', boom)

    out = read_inventory('eGRID', 2024, StewiFormat.FLOWBYFACILITY,
                         download_if_missing=True)
    assert out is None
    assert calls['generate'] == 0
