"""Push a finished session (game summary plus heart rate) to a cloud store.

The backend is chosen from the environment, so nothing secret is hardcoded and
the public repository carries no credential:

  MONGODB_URI                          insert into MongoDB (needs pymongo)
  FIREBASE_DB_URL  (+ FIREBASE_SECRET) POST to a Firebase Realtime Database
  SESSION_ENDPOINT                     POST to any HTTPS endpoint (optional)
  none of the above                    append to a local JSONL file

MongoDB and Firebase are the intended targets; set one of the variables and the
game writes there. The local file keeps the pipeline complete when nothing is
configured or the network is down, which is what an offline grading session
gets. upload() never raises into the game: on any failure it falls back to the
local file and returns a short string saying where the record landed. Firebase
uses plain HTTPS through the standard library; MongoDB uses pymongo, imported
lazily so keyboard-only play never needs it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def upload(record: dict, logs_dir) -> str:
    """Store one session record. Returns a human readable location."""
    uri = os.environ.get("MONGODB_URI")
    fb_url = os.environ.get("FIREBASE_DB_URL")
    endpoint = os.environ.get("SESSION_ENDPOINT")
    try:
        if uri:
            return _to_mongo(record, uri)
        if fb_url:
            return _to_firebase(record, fb_url, os.environ.get("FIREBASE_SECRET"))
        if endpoint:
            return _to_endpoint(record, endpoint)
    except Exception as error:
        print(f"Cloud upload failed ({str(error)[:150]}); writing locally instead")
    return _to_local(record, logs_dir)


def _to_endpoint(record: dict, url: str) -> str:
    request = urllib.request.Request(
        url, data=json.dumps(record).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.loads(response.read().decode("utf-8"))
    host = url.split("/api/")[0]
    if body.get("stored") == "blob":
        return f"cloud endpoint ({host}, stored)"
    # Endpoint is live but no store is attached yet; still reached the cloud.
    return f"cloud endpoint ({host}, reached; attach a Blob store to persist)"


def _to_mongo(record: dict, uri: str) -> str:
    from pymongo import MongoClient          # lazy: only needed for this backend

    client = MongoClient(uri, serverSelectionTimeoutMS=4000)
    db = client.get_default_database()
    if db is None:                           # a URI without a path has no default db
        db = client["magicdodge"]
    db["sessions"].insert_one(dict(record))  # copy: pymongo adds an _id in place
    client.close()
    return f"MongoDB ({db.name}.sessions)"


def _to_firebase(record: dict, db_url: str, secret: str | None) -> str:
    # A POST to a Realtime Database path creates a new child under a push id.
    url = db_url.rstrip("/") + "/magicdodge_sessions.json"
    if secret:
        url += "?auth=" + secret
    request = urllib.request.Request(
        url, data=json.dumps(record).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        name = json.loads(response.read().decode("utf-8")).get("name", "?")
    return f"Firebase (magicdodge_sessions/{name})"


def _to_local(record: dict, logs_dir) -> str:
    path = Path(logs_dir) / "uploads.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")
    return f"local file ({path})"
