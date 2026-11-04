"""Generic Microsoft Graph publication. Credentials and tokens stay in memory.

The injected transport is responsible for HTTPS, HTTP errors, and redirect safety.
No company-specific configuration is bundled or persisted here.
"""
import hashlib
import json
import time
from dataclasses import dataclass, field
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

GRAPH = "https://graph.microsoft.com/v1.0"


class TransportError(Exception):
    def __init__(self, status, message="request failed", *, payload=None, location=None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}
        self.location = location


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def https_transport(method, url, headers=None, body=None):
    if urlsplit(url).scheme != "https":
        raise ValueError("HTTPS required")
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=60) as response:
            return response.read()
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (ValueError, UnicodeError):
            payload = {}
        raise TransportError(exc.code, payload=payload, location=exc.headers.get("Location")) from exc



def _trusted_url(url, host):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == host and
            parsed.port in (None, 443) and not parsed.username and not parsed.password)


def download_sha256(url, host):
    """Read a trusted download URL without credentials or redirect following."""
    if not _trusted_url(url, host):
        raise ValueError("untrusted download URL")
    digest = hashlib.sha256()
    try:
        with build_opener(_NoRedirect()).open(Request(url, method="GET"), timeout=60) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
    except HTTPError as exc:
        raise TransportError(exc.code) from exc
    return digest.digest()

def _json(raw):
    return json.loads(raw.decode("utf-8"))


@dataclass(frozen=True)
class SharePointProfile:
    site_url: str
    tenant: str
    client_id: str
    library: str
    folder: str = ""

    def __post_init__(self):
        parsed = urlsplit(self.site_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment):
            raise ValueError("site URL must be an HTTPS site URL")
        if not self.tenant or not self.client_id or not self.library:
            raise ValueError("tenant, approved public client and library required")
        if any(part in (".", "..") for part in self.folder.split("/")):
            raise ValueError("invalid folder")


@dataclass(frozen=True)
class PublicationResult:
    status: str
    detail: str
    remote_item: dict | None = None
    previous_remote: str = "unknown"
    consumer_refresh: str = "unknown"
    verification: str = "none"


@dataclass
class SharePointPublisher:
    profile: SharePointProfile
    transport: object = https_transport
    token: str | None = field(default=None, repr=False)
    download_digest: object = download_sha256

    def _request(self, method, url, body=None, headers=None, authenticated=True):
        h = dict(headers or {})
        if authenticated:
            if not self.token:
                raise ValueError("sign-in required")
            h["Authorization"] = "Bearer " + self.token
        return self.transport(method, url, h, body)

    def begin_device_flow(self):
        url = "https://login.microsoftonline.com/" + quote(self.profile.tenant, safe="") + "/oauth2/v2.0/devicecode"
        body = urlencode({"client_id": self.profile.client_id,
                          "scope": "https://graph.microsoft.com/Files.ReadWrite.All https://graph.microsoft.com/Sites.Read.All"}).encode()
        return _json(self._request("POST", url, body, {"Content-Type": "application/x-www-form-urlencoded"}, False))

    def poll_device_flow(self, device_code):
        """Perform one poll; caller observes service interval and expiry."""
        url = "https://login.microsoftonline.com/" + quote(self.profile.tenant, safe="") + "/oauth2/v2.0/token"
        body = urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                          "client_id": self.profile.client_id, "device_code": device_code}).encode()
        result = _json(self._request("POST", url, body, {"Content-Type": "application/x-www-form-urlencoded"}, False))
        self.token = result["access_token"]
        return True

    def login_device(self, on_user_code, *, cancel=None, clock=time.monotonic, wait=None):
        """Bounded device login. Callback receives display-only verification details."""
        info = self.begin_device_flow()
        interval = max(1, int(info.get("interval", 5)))
        expires = min(900, max(0, int(info["expires_in"])))
        deadline = clock() + expires
        on_user_code({key: info[key] for key in ("user_code", "verification_uri", "message") if key in info})
        if wait is None:
            wait = cancel.wait if cancel is not None else time.sleep
        while clock() < deadline:
            if cancel is not None and cancel.is_set():
                return "cancelled"
            remaining = deadline - clock()
            wait(min(interval, max(0, remaining)))
            if cancel is not None and cancel.is_set():
                return "cancelled"
            if clock() >= deadline:
                break
            try:
                self.poll_device_flow(info["device_code"])
                return "authenticated"
            except TransportError as exc:
                error = exc.payload.get("error")
                if error == "authorization_pending":
                    continue
                if error == "slow_down":
                    interval += 5
                    continue
                if error in ("expired_token", "authorization_declined", "access_denied"):
                    return error
                return "failed"
            except (OSError, ValueError, KeyError):
                return "failed"
        return "expired"

    def resolve(self):
        site = urlsplit(self.profile.site_url)
        endpoint = GRAPH + "/sites/" + site.hostname + ":" + quote(site.path.rstrip("/"), safe="/")
        site_id = _json(self._request("GET", endpoint))["id"]
        next_url = GRAPH + "/sites/" + quote(site_id, safe="") + "/drives"
        matches = []
        visited = set()
        while next_url:
            if not _trusted_url(next_url, "graph.microsoft.com") or next_url in visited:
                raise ValueError("unsafe or repeated Graph page")
            visited.add(next_url)
            if len(visited) > 1000:
                raise ValueError("too many Graph pages")
            drives = _json(self._request("GET", next_url))
            matches.extend(d for d in drives["value"] if d.get("name") == self.profile.library)
            next_url = drives.get("@odata.nextLink")
        if len(matches) != 1:
            raise ValueError("library must identify exactly one drive")
        drive = matches[0]["id"]
        root = GRAPH + "/drives/" + quote(drive, safe="")
        folder = self.profile.folder.strip("/")
        item = _json(self._request("GET", root + ("/root:/" + quote(folder, safe="/") if folder else "/root")))
        if "folder" not in item:
            raise ValueError("target is not a folder")
        web_url = item.get('webUrl', '')
        parsed = urlsplit(web_url)
        path = unquote(parsed.path)
        site_path = unquote(site.path).rstrip('/')
        if (not _trusted_url(web_url, site.hostname) or parsed.query or parsed.fragment or
                any(part in ('.', '..') for part in path.split('/')) or
                not (path == site_path or path.startswith(site_path + '/'))):
            raise ValueError('folder URL is missing or outside the site')
        return {"site_id": site_id, "drive_id": drive, "folder_id": item["id"], "folder_url": web_url}

    def observe_target(self, target, filename):
        if not filename or filename in ('.', '..') or '/' in filename or '\\' in filename:
            raise ValueError('invalid filename')
        url = GRAPH + '/drives/' + quote(target['drive_id'], safe='') + '/items/' + quote(target['folder_id'], safe='') + ':/' + quote(filename, safe='') + ':'
        try:
            item = _json(self._request('GET', url))
        except TransportError as exc:
            if exc.status == 404:
                return {'absent': True}
            raise
        if not item.get('eTag'):
            raise ValueError('remote ETag unavailable')
        return {'etag': item['eTag'], 'absent': False}

    def publish(self, target, filename, data, *, expected_etag=None, expected_absent=False):
        """Publish small files conditionally and verify by authenticated readback.

        Existing files require their observed ETag; new files require explicit absence.
        Large uploads are held back until session conflict semantics are validated.
        """
        if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
            raise ValueError("invalid filename")
        if bool(expected_etag) == bool(expected_absent):
            raise ValueError("provide an ETag or explicitly expect absence")
        if len(data) > 250 * 1024 * 1024:
            return PublicationResult("failed", "upload session unsupported; no upload attempted")
        base = GRAPH + "/drives/" + quote(target["drive_id"], safe="") + "/items/" + quote(target["folder_id"], safe="") + ":/" + quote(filename, safe="") + ":"
        headers = {"Content-Type": "application/octet-stream"}
        headers["If-Match" if expected_etag else "If-None-Match"] = expected_etag or "*"
        try:
            response_item = _json(self._request("PUT", base + "/content", data, headers))
            item = {key: response_item[key] for key in ("id", "name", "size", "eTag") if key in response_item}
        except (TransportError, OSError) as exc:
            return PublicationResult("failed", "upload failed or conflict; remote state unknown (" + str(getattr(exc, "status", "network")) + ")")
        try:
            if not item.get("id") or item.get("size") != len(data):
                return PublicationResult("unverified", "upload response incomplete or size mismatch", item)
            content_url = GRAPH + "/drives/" + quote(target["drive_id"], safe="") + "/items/" + quote(item["id"], safe="") + "/content"
            try:
                remote = self._request("GET", content_url)
                remote_digest = hashlib.sha256(remote).digest()
            except TransportError as exc:
                if exc.status not in (301, 302, 303, 307, 308) or not exc.location:
                    raise
                host = urlsplit(self.profile.site_url).hostname
                if not _trusted_url(exc.location, host):
                    raise ValueError("untrusted download redirect")
                remote_digest = self.download_digest(exc.location, host)
            if remote_digest != hashlib.sha256(data).digest():
                return PublicationResult("unverified", "readback differs; remote may already have changed", item)
            # Check that readback still corresponds to the uploaded version.
            current = _json(self._request("GET", GRAPH + "/drives/" + quote(target["drive_id"], safe="") + "/items/" + quote(item["id"], safe="")))
            if not item.get("eTag") or current.get("eTag") != item["eTag"]:
                return PublicationResult("unverified", "remote version changed or is unknown", item)
            return PublicationResult("verified", "SHA-256 readback matches", item, verification="sha256_readback")
        except (TransportError, OSError, ValueError, KeyError):
            return PublicationResult("unverified", "uploaded but readback verification failed", item)


def power_query_guidance(site_url, folder, filename):
    """Return connection guidance; no native Excel workbook is created."""
    return {"template_status": "guidance_only", "site_url": site_url,
            "folder": folder, "filename": filename,
            "instructions": "Excel 데이터 가져오기에서 SharePoint 폴더를 선택하고 사이트 주소와 회사 계정을 사용하세요. 폴더 및 파일을 필터링한 후 CSV 구분자, 문화권 및 열 형식을 명시하세요."}
