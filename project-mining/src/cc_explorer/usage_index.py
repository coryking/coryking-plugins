"""Incremental request-holder lookup; caches IDs/counters/locators, never prose.

A query scans bytes for new request IDs and changed files, parsing only matches.
The SQLite cache is local derived evidence, not a shared store or lifetime ledger.
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
from .usage_models import Observation

_LOCK = threading.Lock()


def holders(identities, refs, provider):
    identities = {identity for identity in identities if identity.startswith("claude:request:")}
    if not identities:
        return [], []
    cache_path = Path(os.environ.get("CC_EXPLORER_USAGE_INDEX", str(Path(tempfile.gettempdir()) / "cc-explorer/usage-holders-v1.sqlite3")))
    paths = {}
    for ref in refs:
        if ref.metadata.get("nested_only_agent_id"):
            continue
        for path in ref.paths:
            paths.setdefault(path, ref)
    inventory = {}
    reasons = []
    for path in paths:
        try:
            stat = path.stat()
            inventory[str(path)] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            reasons.append("request_holder_source_unreadable")
    # Namespace query completeness by corpus roots, so an unrelated corpus/test
    # cannot certify a query completed against another inventory.
    namespace = json.dumps({"parser_version": version("cc-explorer"), "roots": sorted({str(root) for ref in refs for root in ref.source_roots})}, sort_keys=True)
    with _LOCK, _connect(cache_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS files (scope TEXT, path TEXT, size INTEGER, mtime INTEGER, PRIMARY KEY(scope,path))")
        db.execute("CREATE TABLE IF NOT EXISTS queries (scope TEXT, identity TEXT, PRIMARY KEY(scope,identity))")
        db.execute("CREATE TABLE IF NOT EXISTS observations (scope TEXT, path TEXT, identity TEXT, line INTEGER, data TEXT, PRIMARY KEY(scope,path,identity,line))")
        db.execute("CREATE TABLE IF NOT EXISTS issues (scope TEXT, path TEXT, reason TEXT, PRIMARY KEY(scope,path,reason))")
        known = {row[0] for row in db.execute("SELECT identity FROM queries WHERE scope=?", (namespace,))}
        old = {path: (size, mtime) for path, size, mtime in db.execute("SELECT path,size,mtime FROM files WHERE scope=?", (namespace,))}
        changed = [Path(path) for path, version in inventory.items() if old.get(path) != version]
        deleted = old.keys() - inventory.keys()
        for path in deleted:
            db.execute("DELETE FROM observations WHERE scope=? AND path=?", (namespace, path))
            db.execute("DELETE FROM files WHERE scope=? AND path=?", (namespace, path))
            db.execute("DELETE FROM issues WHERE scope=? AND path=?", (namespace, path))
        new = identities - known
        searches = [(list(paths), new), (changed, known | identities)]
        candidates = set()
        fresh_candidates = set()
        uncertain_new = set()
        for search_number, (files, requested) in enumerate(searches):
            if not files or not requested:
                continue
            found = set()
            ids = [i.split("claude:request:", 1)[1] for i in requested]
            if any(not raw.isascii() or '\\' in raw or '"' in raw for raw in ids):
                found.update(files)
                candidates.update(found)
                if search_number == 0:
                    fresh_candidates.update(found)
                continue
            try:
                for chunk in range(0, len(ids), 1000):
                    found.update(make_scanner().files_with_match([re.escape(raw) for raw in ids[chunk:chunk+1000]], files))
            except ScannerError:
                found.update(files)
                reasons.append("request_holder_prefilter_fallback")
            candidates.update(found)
            if search_number == 0:
                fresh_candidates.update(found)
        for path in changed:
            db.execute("DELETE FROM observations WHERE scope=? AND path=?", (namespace, str(path)))
            db.execute("DELETE FROM issues WHERE scope=? AND path=?", (namespace, str(path)))
        for path in sorted(candidates):
            usage = provider.load_usage(replace(paths[path], paths=(path,)))
            if any(source.status != "observed" for source in usage.sources):
                db.execute("INSERT OR IGNORE INTO issues VALUES (?,?,?)", (namespace, str(path), "request_holder_snapshot_partial"))
                if path in fresh_candidates:
                    reasons.append("request_holder_snapshot_partial")
                    uncertain_new.update(new)
            for observation in usage.observations:
                if observation.identity not in known | identities:
                    continue
                for loc in observation.sources:
                    db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?)", (namespace, str(path), observation.identity, loc.line, observation.model_dump_json()))
        for path, (size, mtime) in inventory.items():
            db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (namespace, path, size, mtime))
        for identity in identities - uncertain_new:
            db.execute("INSERT OR IGNORE INTO queries VALUES (?,?)", (namespace, identity))
        observations = []
        for identity in sorted(identities):
            reasons.extend(row[0] for row in db.execute("SELECT DISTINCT i.reason FROM issues i JOIN observations o ON i.scope=o.scope AND i.path=o.path WHERE o.scope=? AND o.identity=?", (namespace, identity)))
            observations.extend(Observation.model_validate_json(row[0]) for row in db.execute("SELECT data FROM observations WHERE scope=? AND identity=? ORDER BY path,line", (namespace, identity)))
    return observations, sorted(set(reasons))


def _connect(path):
    # A derived cache failure must not prevent a correct report. In-memory
    # fallback runs the same evidence lookup, with no cross-call acceleration.
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
