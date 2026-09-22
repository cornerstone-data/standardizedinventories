"""Download StEWI artifacts from Cornerstone GCS before EPA Data Commons.

Layout matches the local stewi store and the existing GCS prefix::

    gs://cornerstone-default/stewi/<category>/<file>
    gs://cornerstone-default/stewi/<metadata>.json

On ``download_if_missing``, try GCS first (ADC / workload identity), then fall
back to ``esupy`` Data Commons. Failures to reach GCS are logged and treated as
a miss so Commons can still serve older years.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger('stewi.gcs_remote')

GCS_BUCKET = os.environ.get('STEWI_GCS_BUCKET', 'cornerstone-default')
GCS_PREFIX = os.environ.get('STEWI_GCS_PREFIX', 'stewi').strip('/')

_VERSION_HASH = re.compile(
    r'^(?P<name>.+)_(?P<version>v[\d.]+)_(?P<hash>[0-9a-f]+)(?P<rest>.*)$',
    re.IGNORECASE,
)


def _storage_client():
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ImportError(
            'google-cloud-storage is required for GCS downloads. '
            'Install with: pip install google-cloud-storage'
        ) from exc
    return storage.Client()


def _blob_prefix(category: str) -> str:
    parts = [GCS_PREFIX]
    if category:
        parts.append(category.strip('/'))
    return '/'.join(parts) + '/'


def _local_dir(paths, category: str) -> Path:
    folder = Path(paths.local_path)
    if category:
        folder = folder / category
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _parse_name(file_name: str) -> dict:
    """Split ``NEI_2022_v1.2.0_abc123.parquet``-style names for ranking."""
    stem, ext = os.path.splitext(file_name)
    m = _VERSION_HASH.match(stem)
    if m:
        return {
            'file_name': file_name,
            'name': m.group('name'),
            'version': m.group('version'),
            'hash': m.group('hash'),
            'ext': ext.lstrip('.'),
        }
    return {
        'file_name': file_name,
        'name': stem,
        'version': '',
        'hash': '',
        'ext': ext.lstrip('.'),
    }


def _list_matching_blobs(client, file_meta) -> list:
    """Return GCS blob names (basename) matching ``file_meta.name_data``."""
    category = file_meta.category or ''
    prefix = _blob_prefix(category)
    bucket = client.bucket(GCS_BUCKET)
    matches = []
    for blob in bucket.list_blobs(prefix=prefix):
        # stewi/flowbyprocess/NEI_2022_....parquet -> basename
        rel = blob.name[len(prefix):]
        if not rel or '/' in rel:
            continue
        parsed = _parse_name(rel)
        if not parsed['file_name'].startswith(file_meta.name_data):
            continue
        if file_meta.ext and parsed['ext'] != file_meta.ext:
            # metadata json often requested with ext='json' and empty category
            if not (
                file_meta.ext == 'json'
                and parsed['ext'] == 'json'
            ):
                if parsed['ext'] != file_meta.ext:
                    continue
        matches.append((parsed, blob))
    return matches


def _pick_recent(matches: list):
    """Prefer highest version string, then newest blob updated time."""
    if not matches:
        return None

    def sort_key(item):
        parsed, blob = item
        return (parsed['version'], blob.updated or blob.time_created)

    matches = sorted(matches, key=sort_key, reverse=True)
    best_parsed, _ = matches[0]
    vh = ''
    if best_parsed['version']:
        vh = f"{best_parsed['version']}_{best_parsed['hash']}"
    selected = []
    for parsed, blob in matches:
        if vh:
            if vh in parsed['file_name']:
                selected.append(blob)
        elif parsed['file_name'] == best_parsed['file_name']:
            selected.append(blob)
    return selected or [matches[0][1]]


def try_download_from_gcs(file_meta, paths) -> bool:
    """Download matching objects from GCS into ``paths.local_path``.

    :return: True if at least one file was downloaded
    """
    try:
        client = _storage_client()
    except Exception as exc:  # noqa: BLE001 — fall through to Commons
        log.info('GCS client unavailable (%s); will try Data Commons', exc)
        return False

    try:
        matches = _list_matching_blobs(client, file_meta)
    except Exception as exc:  # noqa: BLE001
        log.info('GCS list failed for %s (%s); will try Data Commons',
                 file_meta.name_data, exc)
        return False

    if not matches:
        log.info(
            '%s not found under gs://%s/%s',
            file_meta.name_data,
            GCS_BUCKET,
            _blob_prefix(file_meta.category or ''),
        )
        return False

    blobs = _pick_recent(matches)
    dest_dir = _local_dir(paths, file_meta.category or '')
    ok = False
    for blob in blobs:
        name = Path(blob.name).name
        dest = dest_dir / name
        try:
            log.info(
                'downloading gs://%s/%s -> %s',
                GCS_BUCKET,
                blob.name,
                dest,
            )
            blob.download_to_filename(str(dest))
            ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning('GCS download failed for %s: %s', blob.name, exc)
    return ok


def download_prefer_gcs(file_meta, paths) -> bool:
    """Try GCS, then ``esupy.processed_data_mgmt.download_from_remote``.

    :return: True if either remote produced files
    """
    if try_download_from_gcs(file_meta, paths):
        return True
    from esupy.processed_data_mgmt import download_from_remote
    return bool(download_from_remote(file_meta, paths))
