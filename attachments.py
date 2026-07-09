"""Attachments (files + URL links) on Focused Build objects — requirements, WPs, WIs.

Uses DROP_DOC_SRV the way the Fiori "Attachments" component does. The service is
RPC-over-OData: every call carries an ``Action`` discriminator of the form
``<consumer app id> + <operation suffix>`` (captured from the shipped UI5 code).

Verified live:
  upload   POST  DocumentSet   {CrmId, BranchId, Filename, Filecontent(b64), Action=_Document_Create}
  add URL  POST  DocumentSet   {..., weblink, Action=_Document_Create_Url}
  list     GET   CharmWP_WI_BRSet(CrmId,BranchId)/attachedDeltaDocuments
  download GET   DocumentContentCollection(DocId,CrmId,StructureId,BranchId)/$value
  delete   DELETE DocumentSet(BranchId,CrmId,DocId,StructureId)?Action=_Document_Delete
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import config
from client import SolmanError, client_for

# The attachments UI component's app id — the backend validates Action against
# the consumers configured for the service, so we present ourselves as that app.
_APP = "com.sap.solman.fb.attachments"
_MAX_UPLOAD = 20 * 1024 * 1024  # sanity ceiling; the gateway rejects huge JSON bodies anyway


def _ctx(guid: str, branch_id: str = "", solution: str = "") -> tuple[str, str]:
    """Resolve (CrmId, BranchId) for an object guid; CrmId is the plain 32-char guid."""
    g = guid.replace("-", "").upper()
    if solution and not branch_id:
        import solutions as _sol
        branch_id = _sol.resolve_context(solution)["branch_id"]
    return g, branch_id or config.DEFAULT_BRANCH_ID


def list_attachments(guid: str, branch_id: str = "", solution: str = "") -> list[dict]:
    """List the attachments (files and URL links) on a requirement/WP/WI."""
    g, br = _ctx(guid, branch_id, solution)
    c = client_for(config.SVC_DROP_DOC)
    rows = c.results(f"CharmWP_WI_BRSet(CrmId='{g}',BranchId='{br}')/attachedDeltaDocuments")
    return [{"doc_id": r.get("DocId"), "filename": r.get("Filename"),
             "size": r.get("Filesize"), "mime_type": r.get("MimeType"),
             "weblink": r.get("weblink") or "", "created_by": r.get("CreateAuthor"),
             "created_at": r.get("CreateDate"), "changed_at": r.get("LastChangeDate")}
            for r in rows]


def upload_attachment(guid: str, file_path: str = "", filename: str = "",
                      content_b64: str = "", mime_type: str = "",
                      branch_id: str = "", solution: str = "") -> dict:
    """Attach a file to a requirement/WP/WI, from a local path or raw base64.

    Pass EITHER file_path (read from disk; filename/mime auto-detected) OR
    filename + content_b64 (+ mime_type). Verified after upload via the list.
    """
    if file_path:
        p = Path(file_path)
        if not p.is_file():
            raise ValueError(f"file not found: {file_path}")
        data = p.read_bytes()
        filename = filename or p.name
        mime_type = mime_type or (mimetypes.guess_type(p.name)[0] or "application/octet-stream")
    elif filename and content_b64:
        data = base64.b64decode(content_b64)
        mime_type = mime_type or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
    else:
        raise ValueError("pass file_path, or filename + content_b64")
    if len(data) > _MAX_UPLOAD:
        raise ValueError(f"file too large ({len(data)} bytes; limit {_MAX_UPLOAD})")

    g, br = _ctx(guid, branch_id, solution)
    c = client_for(config.SVC_DROP_DOC)
    c.create("DocumentSet", {
        "DocId": "", "CrmId": g, "StructureId": "", "BranchId": br,
        "Filename": filename, "Description": filename,
        "DocType": "", "ElementType": "", "Element": "", "DocumentGroupKey": "",
        "Filecontent": base64.b64encode(data).decode(),
        "Filesize": len(data), "Filetype": mime_type, "MimeType": mime_type,
        "LastChangeDate": "", "Action": f"{_APP}_Document_Create",
    })
    match = [a for a in list_attachments(g, br) if a["filename"] == filename]
    if not match:
        raise SolmanError(f"upload of {filename!r} did not persist (not in the attachment list).")
    return {"guid": g, "uploaded": filename, "size": len(data),
            "doc_id": match[-1]["doc_id"], "verified": True}


def attach_url(guid: str, url: str, title: str = "",
               branch_id: str = "", solution: str = "") -> dict:
    """Attach a URL link (shows as <title>.URL in the attachment list)."""
    if not url:
        raise ValueError("url is required")
    title = title or url[:50]
    g, br = _ctx(guid, branch_id, solution)
    c = client_for(config.SVC_DROP_DOC)
    c.create("DocumentSet", {
        "DocId": "", "CrmId": g, "StructureId": "", "BranchId": br,
        "Filename": title, "Description": title, "weblink": url,
        "DocType": "", "ElementType": "", "Element": "", "DocumentGroupKey": "",
        "Filecontent": "", "Filesize": 0, "Filetype": "",
        "Action": f"{_APP}_Document_Create_Url",
    })
    match = [a for a in list_attachments(g, br) if a["weblink"] == url]
    if not match:
        raise SolmanError(f"URL attach {url!r} did not persist.")
    return {"guid": g, "url": url, "title": title,
            "doc_id": match[-1]["doc_id"], "verified": True}


def download_attachment(guid: str, doc_id: str, save_to: str = "",
                        branch_id: str = "", solution: str = "") -> dict:
    """Download one attachment's content. Saves to save_to, else returns base64."""
    g, br = _ctx(guid, branch_id, solution)
    c = client_for(config.SVC_DROP_DOC)
    key = (f"DocumentContentCollection(DocId='{doc_id}',CrmId='{g}',"
           f"StructureId='',BranchId='{br}')/$value")
    r = c._http.get(f"{c.service}/{key}")
    if r.status_code != 200:
        raise SolmanError(f"download failed: HTTP {r.status_code} {r.text[:150]}")
    if save_to:
        Path(save_to).write_bytes(r.content)
        return {"doc_id": doc_id, "saved_to": save_to, "size": len(r.content)}
    return {"doc_id": doc_id, "size": len(r.content),
            "content_b64": base64.b64encode(r.content).decode()}


def delete_attachment(guid: str, doc_id: str,
                      branch_id: str = "", solution: str = "") -> dict:
    """Delete an attachment (file or URL link) from an object, verified."""
    g, br = _ctx(guid, branch_id, solution)
    c = client_for(config.SVC_DROP_DOC)
    token = c._ensure_csrf()
    path = (f"{c.service}/DocumentSet(BranchId='{br}',CrmId='{g}',"
            f"DocId='{doc_id}',StructureId='')")
    r = c._http.request("DELETE", path, params={"Action": f"{_APP}_Document_Delete"},
                        headers={"X-CSRF-Token": token, "Accept": "application/json"})
    if r.status_code == 403 and "require" in r.headers.get("x-csrf-token", "").lower():
        c._csrf = None
        r = c._http.request("DELETE", path, params={"Action": f"{_APP}_Document_Delete"},
                            headers={"X-CSRF-Token": c._ensure_csrf(), "Accept": "application/json"})
    if r.status_code not in (200, 202, 204):
        raise SolmanError(f"delete failed: HTTP {r.status_code} {r.text[:200]}")
    still = any(a["doc_id"] == doc_id for a in list_attachments(g, br))
    return {"guid": g, "doc_id": doc_id, "deleted": not still}
