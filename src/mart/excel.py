"""SharePoint Power Query generation; no credentials are embedded."""

from urllib.parse import unquote, urlsplit


def _literal(value):
    value = str(value).replace("#(", "#(#)(").replace('"', '""')
    value = value.replace("\r", "#(cr)").replace("\n", "#(lf)").replace("\t", "#(tab)")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Unsupported control character")
    return '"' + value + '"'


def sharepoint_m_code(site_url, folder_url, filename, columns, numeric_columns):
    parsed = urlsplit(site_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError("A valid HTTPS site URL is required")
    if not filename or "/" in filename or "\\" in filename or not columns:
        raise ValueError("File name and output columns are required")
    numeric = set(numeric_columns)
    transforms = ", ".join(
        "{"
        + _literal(c)
        + ", "
        + ("type number" if c in numeric else "type text")
        + "}"
        for c in columns
    )
    folder = urlsplit(folder_url)
    folder_path = unquote(folder.path)
    site_path = unquote(parsed.path).rstrip("/")
    if (
        folder.scheme != "https"
        or folder.hostname != parsed.hostname
        or folder.port not in (None, 443)
        or folder.username
        or folder.password
        or folder.query
        or folder.fragment
        or any(part in (".", "..") for part in folder_path.split("/"))
        or not (folder_path == site_path or folder_path.startswith(site_path + "/"))
    ):
        raise ValueError("Resolved folder URL must belong to the site")
    # SharePoint.Files returns decoded folder paths, including a trailing slash.
    target = f"https://{folder.hostname}" + folder_path.rstrip("/") + "/"
    return f"""let
    Source = SharePoint.Files({_literal(site_url.rstrip("/"))}, [ApiVersion=15]),
    Selected = Table.SelectRows(Source, each [Folder Path] = {_literal(target)} and [Name] = {_literal(filename)}),
    File = if Table.RowCount(Selected) = 1 then Selected{{0}}[Content] else error "Expected exactly one file",
    CSV = Csv.Document(File, [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(CSV, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Headers, {{{transforms}}}, "en-US")
in
    Typed"""
