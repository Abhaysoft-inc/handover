"""Document sources. LocalDrive serves the bundled demo Drive; GoogleDrive talks to
the real Google Drive API through OAuth (read-only for ingestion, drive.file for
filing documents Handover creates)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .. import db
from ..config import DATA_DIR, DEMO_DRIVE, get_settings

FOLDER_MIME = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"]
READABLE = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "image/png", "image/jpeg", "image/webp", "text/plain", GOOGLE_DOC_MIME}


@dataclass
class DriveFile:
    ref: str                  # local: relative posix path · google: file id
    name: str
    path: str                 # display folder path
    mime_type: str
    is_folder: bool
    size: int | None = None
    modified_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def doc_id(source: str, ref: str) -> str:
    return "F-" + hashlib.sha1(f"{source}:{ref}".encode()).hexdigest()[:10]


class DriveConnector(Protocol):
    source: str
    label: str

    async def list_folder(self, folder_ref: str) -> list[DriveFile]: ...
    async def search(self, text: str) -> list[DriveFile]: ...
    async def get(self, ref: str) -> DriveFile: ...
    async def download(self, ref: str) -> tuple[bytes, str]: ...
    async def upload(self, folder_path: str, name: str, data: bytes, mime: str) -> dict: ...


# ── local demo drive ──────────────────────────────────────────────────────


class LocalDrive:
    source = "local"
    label = "Demo Drive (bundled sample, local)"

    def __init__(self, root: Path = DEMO_DRIVE, filings: Path = DATA_DIR / "drive_filings") -> None:
        self.root = root
        self.filings = filings

    def _safe(self, ref: str) -> Path:
        p = (self.root / ref).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes the drive root")
        return p

    def _file(self, p: Path) -> DriveFile:
        rel = p.relative_to(self.root).as_posix()
        st = p.stat()
        mime = FOLDER_MIME if p.is_dir() else (mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        if p.suffix.lower() == ".docx":
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        parent = Path(rel).parent.as_posix()
        return DriveFile(ref=rel, name=p.name, path="/" if parent == "." else parent,
                         mime_type=mime, is_folder=p.is_dir(), size=None if p.is_dir() else st.st_size,
                         modified_at=datetime.fromtimestamp(st.st_mtime, timezone.utc).date().isoformat())

    async def list_folder(self, folder_ref: str) -> list[DriveFile]:
        ref = "" if folder_ref in ("", "/", "root") else folder_ref
        base = self._safe(ref)
        if not base.is_dir():
            raise ValueError(f"not a folder: {folder_ref}")
        items = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        return [self._file(p) for p in items if not p.name.startswith(".")]

    async def search(self, text: str) -> list[DriveFile]:
        from ..documents import extract_text

        terms = [t.lower() for t in text.split() if t.strip()]
        hits = []
        for p in sorted(self.root.rglob("*")):
            if not p.is_file():
                continue
            hay = p.relative_to(self.root).as_posix().lower()
            cached = db.one("SELECT text_content FROM documents WHERE id=?", (doc_id(self.source, p.relative_to(self.root).as_posix()),))
            body = (cached or {}).get("text_content")
            if body is None:
                try:
                    body = extract_text(p.read_bytes(), self._file(p).mime_type)[0]
                except Exception:
                    body = ""
            hay += " " + (body or "").lower()
            if all(t in hay for t in terms):
                hits.append(self._file(p))
        return hits

    async def get(self, ref: str) -> DriveFile:
        return self._file(self._safe(ref))

    async def download(self, ref: str) -> tuple[bytes, str]:
        p = self._safe(ref)
        return p.read_bytes(), self._file(p).mime_type

    async def upload(self, folder_path: str, name: str, data: bytes, mime: str) -> dict:
        folder = (self.filings / folder_path.strip("/")).resolve()
        if not folder.is_relative_to(self.filings.resolve()):
            raise ValueError("folder escapes the filings root")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_bytes(data)
        rel = target.relative_to(self.filings).as_posix()
        return {"ref": rel, "web_link": None, "location": f"Demo Drive / {rel}"}


# ── Google Drive ──────────────────────────────────────────────────────────

TOKEN_KEY = "google_token"


def google_configured() -> bool:
    return get_settings().google_client_file.exists()


def google_connected() -> bool:
    return bool(db.kv_get(TOKEN_KEY))


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info = db.kv_get(TOKEN_KEY)
    if not info:
        raise RuntimeError("Google Drive is not connected. Use Settings → Connect Google Drive.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        db.kv_set(TOKEN_KEY, json.loads(creds.to_json()))
    return creds


_pending_flows: dict[str, object] = {}


def google_auth_url(redirect_uri: str) -> str:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")   # localhost callback
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(str(get_settings().google_client_file), scopes=SCOPES,
                                         redirect_uri=redirect_uri)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    _pending_flows[state] = flow
    return url


def google_finish_auth(state: str, authorization_response: str) -> None:
    flow = _pending_flows.pop(state, None)
    if flow is None:
        raise RuntimeError("OAuth session expired; start the connection again.")
    flow.fetch_token(authorization_response=authorization_response)
    db.kv_set(TOKEN_KEY, json.loads(flow.credentials.to_json()))


class GoogleDrive:
    source = "google"
    label = "Google Drive"

    def __init__(self) -> None:
        self._svc = None
        self._paths: dict[str, str] = {}
        self.root_id = get_settings().google_root_folder or "root"

    def _service(self):
        if self._svc is None:
            from googleapiclient.discovery import build

            self._svc = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
        return self._svc

    def _to_file(self, f: dict, parent_path: str) -> DriveFile:
        path = parent_path or "/"
        self._paths[f["id"]] = f"{path.rstrip('/')}/{f['name']}"
        return DriveFile(ref=f["id"], name=f["name"], path=path, mime_type=f["mimeType"],
                         is_folder=f["mimeType"] == FOLDER_MIME, size=int(f["size"]) if f.get("size") else None,
                         modified_at=(f.get("modifiedTime") or "")[:10] or None)

    async def list_folder(self, folder_ref: str) -> list[DriveFile]:
        folder = self.root_id if folder_ref in ("", "/", "root") else folder_ref
        parent_path = "/" if folder == self.root_id else self._paths.get(folder, f"/{folder}")

        def run():
            out, token = [], None
            while True:
                resp = self._service().files().list(
                    q=f"'{folder}' in parents and trashed = false", pageSize=200, pageToken=token,
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                    supportsAllDrives=True, includeItemsFromAllDrives=True, orderBy="folder,name").execute()
                out += resp.get("files", [])
                token = resp.get("nextPageToken")
                if not token:
                    return out

        files = await asyncio.to_thread(run)
        return [self._to_file(f, parent_path) for f in files]

    async def search(self, text: str) -> list[DriveFile]:
        safe = text.replace("\\", "\\\\").replace("'", "\\'")

        def run():
            return self._service().files().list(
                q=f"(fullText contains '{safe}' or name contains '{safe}') and trashed = false "
                  f"and mimeType != '{FOLDER_MIME}'",
                pageSize=50, fields="files(id, name, mimeType, size, modifiedTime)",
                supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])

        return [self._to_file(f, "(search)") for f in await asyncio.to_thread(run)]

    async def get(self, ref: str) -> DriveFile:
        def run():
            return self._service().files().get(fileId=ref, fields="id, name, mimeType, size, modifiedTime",
                                               supportsAllDrives=True).execute()

        f = await asyncio.to_thread(run)
        return self._to_file(f, self._paths.get(ref, "/").rsplit("/", 1)[0] or "/")

    async def download(self, ref: str) -> tuple[bytes, str]:
        from googleapiclient.http import MediaIoBaseDownload

        meta = await self.get(ref)

        def run():
            files = self._service().files()
            if meta.mime_type == GOOGLE_DOC_MIME:
                request, mime = files.export_media(fileId=ref, mimeType="application/pdf"), "application/pdf"
            else:
                request, mime = files.get_media(fileId=ref, supportsAllDrives=True), meta.mime_type
            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return buf.getvalue(), mime

        return await asyncio.to_thread(run)

    async def upload(self, folder_path: str, name: str, data: bytes, mime: str) -> dict:
        from googleapiclient.http import MediaInMemoryUpload

        def ensure_folder(parent: str, folder_name: str) -> str:
            safe = folder_name.replace("'", "\\'")
            found = self._service().files().list(
                q=f"'{parent}' in parents and name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false",
                fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
            if found:
                return found[0]["id"]
            return self._service().files().create(
                body={"name": folder_name, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id", supportsAllDrives=True).execute()["id"]

        def run():
            parent = self.root_id
            for part in [p for p in folder_path.split("/") if p]:
                parent = ensure_folder(parent, part)
            created = self._service().files().create(
                body={"name": name, "parents": [parent]}, media_body=MediaInMemoryUpload(data, mimetype=mime),
                fields="id, webViewLink", supportsAllDrives=True).execute()
            return {"ref": created["id"], "web_link": created.get("webViewLink"),
                    "location": f"Google Drive / {folder_path.strip('/')}/{name}"}

        return await asyncio.to_thread(run)


def requested_source() -> str:
    return db.kv_get("drive_source") or get_settings().drive_source


def get_drive() -> DriveConnector:
    if requested_source() == "google" and google_connected():
        return GoogleDrive()
    return LocalDrive()


def drive_status() -> dict:
    active = get_drive()
    return {"source": active.source, "label": active.label, "requested": requested_source(),
            "google_configured": google_configured(), "google_connected": google_connected()}
