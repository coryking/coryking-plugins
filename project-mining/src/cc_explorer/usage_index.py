"""Incremental native request/child-link holders; caches metadata, never prose.

Current queries refresh only their own stale source generations. Historical IDs
are invalidated lazily, so a growing corpus cannot grow each call's pattern set.
The cache is derived local evidence, not a shared store or lifetime ledger.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import threading
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from .corpus import ScannerError, make_scanner
from .usage_models import ChildLink, Observation

_LOCK = threading.Lock()


def holders(identities, refs, provider):
    return _lookup({i for i in identities if i.startswith("claude:request:")}, refs, provider, links=False)


def child_link_holders(identities, refs, provider):
    return _lookup({i for i in identities if i and i.startswith("claude:childlink:")}, refs, provider, links=True)


def _lookup(identities, refs, provider, *, links):
    if not identities:
        return [], []
    cache_path = Path(os.environ.get("CC_EXPLORER_USAGE_INDEX", str(Path(tempfile.gettempdir()) / "cc-explorer/usage-holders-v2.sqlite3")))
    paths = {}
    for ref in refs:
        if ref.metadata.get("nested_only_agent_id"):
            continue
        for path in ref.paths:
            paths.setdefault(path, ref)
    inventory, reasons = {}, []
    for path in paths:
        try:
            stat = path.stat()
            inventory[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            reasons.append("request_holder_source_unreadable")
    namespace = json.dumps({"parser_version": version("cc-explorer"), "roots": sorted({str(root) for ref in refs for root in ref.source_roots})}, sort_keys=True)
    with _LOCK, _connect(cache_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS files (scope TEXT, path TEXT, size INTEGER, mtime INTEGER, PRIMARY KEY(scope,path))")
        db.execute("CREATE TABLE IF NOT EXISTS query_generations (scope TEXT, identity TEXT, generation INTEGER, PRIMARY KEY(scope,identity))")
        db.execute("CREATE TABLE IF NOT EXISTS source_generations (scope TEXT, path TEXT, generation INTEGER, PRIMARY KEY(scope,path))")
        db.execute("CREATE INDEX IF NOT EXISTS source_generation_index ON source_generations(scope,generation)")
        db.execute("CREATE TABLE IF NOT EXISTS inventories (scope TEXT PRIMARY KEY, generation INTEGER)")
        db.execute("CREATE TABLE IF NOT EXISTS observations (scope TEXT, path TEXT, identity TEXT, line INTEGER, data TEXT, PRIMARY KEY(scope,path,identity,line))")
        db.execute("CREATE TABLE IF NOT EXISTS issues (scope TEXT, path TEXT, reason TEXT, PRIMARY KEY(scope,path,reason))")
        state = db.execute("SELECT generation FROM inventories WHERE scope=?", (namespace,)).fetchone()
        generation = state[0] if state else 0
        old = {path: (size, mtime) for path, size, mtime in db.execute("SELECT path,size,mtime FROM files WHERE scope=?", (namespace,))}
        changed = {path for path, stamp in inventory.items() if old.get(path) != stamp}
        deleted = old.keys() - inventory.keys()
        if changed or deleted:
            generation += 1
            for path in changed | deleted:
                db.execute("DELETE FROM observations WHERE scope=? AND path=?", (namespace, path))
                db.execute("DELETE FROM issues WHERE scope=? AND path=?", (namespace, path))
                db.execute("INSERT OR REPLACE INTO source_generations VALUES (?,?,?)", (namespace, path, generation))
            for path in deleted:
                db.execute("DELETE FROM files WHERE scope=? AND path=?", (namespace, path))
        db.execute("INSERT OR REPLACE INTO inventories VALUES (?,?)", (namespace, generation))
        # Query versions retain their old generation until those IDs are asked
        # for again. A later query therefore sees every source changed since its
        # own last search, including sources changed during unrelated queries.
        searches = {}
        for identity in identities:
            prior = db.execute("SELECT generation FROM query_generations WHERE scope=? AND identity=?", (namespace, identity)).fetchone()
            searches.setdefault(prior[0] if prior else None, set()).add(identity)
        candidates, searched_ids = set(), {}
        for previous, requested in searches.items():
            files = list(paths) if previous is None else [Path(row[0]) for row in db.execute(
                "SELECT path FROM source_generations WHERE scope=? AND generation>?", (namespace, previous)) if row[0] in inventory]
            if not files:
                continue
            ids = [i.split(":", 2)[2] for i in requested]
            found = set()
            if any(not raw.isascii() or '\\' in raw or '"' in raw for raw in ids):
                found.update(files)
            else:
                try:
                    for chunk in range(0, len(ids), 1000):
                        found.update(make_scanner().files_with_match([re.escape(raw) for raw in ids[chunk:chunk+1000]], files))
                except ScannerError:
                    found.update(files)
                    reasons.append("request_holder_prefilter_fallback")
            candidates.update(found)
            for path in found:
                searched_ids.setdefault(path, set()).update(requested)
        uncertain = set()
        for path in sorted(candidates):
            usage = provider.load_usage(replace(paths[path], paths=(path,)))
            if any(source.status != "observed" for source in usage.sources):
                db.execute("INSERT OR IGNORE INTO issues VALUES (?,?,?)", (namespace, str(path), "request_holder_snapshot_partial"))
                reasons.append("request_holder_snapshot_partial")
                uncertain.update(searched_ids[path])
            records = usage.child_links if links else usage.observations
            for record in records:
                if record.identity not in identities:
                    continue
                locators = [record.source] if links else record.sources
                for loc in locators:
                    db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?)", (namespace, str(path), record.identity, loc.line, record.model_dump_json()))
        for path, (size, mtime) in inventory.items():
            db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (namespace, path, size, mtime))
        for identity in identities - uncertain:
            db.execute("INSERT OR REPLACE INTO query_generations VALUES (?,?,?)", (namespace, identity, generation))
        records = []
        record_type = ChildLink if links else Observation
        for identity in sorted(identities):
            reasons.extend(row[0] for row in db.execute("SELECT DISTINCT i.reason FROM issues i JOIN observations o ON i.scope=o.scope AND i.path=o.path WHERE o.scope=? AND o.identity=?", (namespace, identity)))
            records.extend(record_type.model_validate_json(row[0]) for row in db.execute("SELECT data FROM observations WHERE scope=? AND identity=? ORDER BY path,line", (namespace, identity)))
    return records, sorted(set(reasons))


def _connect(path):
    # A derived cache failure must not prevent a correct report. In-memory
    # fallback runs the same evidence lookup without cross-call acceleration.
    db = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(path, timeout=30)
        db.execute("CREATE TABLE IF NOT EXISTS cache_health (version INTEGER)")
        path.chmod(0o600)
        return db
    except (OSError, sqlite3.Error):
        if db is not None:
            db.close()
        return sqlite3.connect(":memory:")
