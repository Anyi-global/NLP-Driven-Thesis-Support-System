import io
import zipfile
from pathlib import Path

ALLOWED_EXTENSIONS = {"docx", "pdf"}
MAX_DOCX_UNCOMPRESSED = 200 * 1024 * 1024  # guards against zip bombs


def allowed_file(filename):
    return bool(filename and "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS)


def safe_extension(filename):
    return Path(filename).suffix.lower().lstrip(".")


def inspect_upload(data, ext):
    """Verify the bytes really are a PDF/DOCX (the extension alone proves nothing).
    Returns (ok, message). This is a format check, not malware scanning."""
    if not data:
        return False, "The uploaded file is empty."
    if ext == "pdf":
        return (True, "") if data[:1024].lstrip().startswith(b"%PDF-") else (False, "The file is not a valid PDF.")
    if ext == "docx":
        if not zipfile.is_zipfile(io.BytesIO(data)):
            return False, "The file is not a valid DOCX document."
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                    return False, "The file is not a valid DOCX document."
                if sum(i.file_size for i in z.infolist()) > MAX_DOCX_UNCOMPRESSED:
                    return False, "The document expands to an unreasonable size and was rejected."
        except zipfile.BadZipFile:
            return False, "The file is not a valid DOCX document."
        return True, ""
    return False, "Unsupported file type."


def validate_password(password):
    """Thesis Section 4.7 (security): at least 8 characters, including letters and numbers.
    Capped at 72 bytes because bcrypt only uses the first 72 (and bcrypt>=5 raises beyond that)."""
    if not isinstance(password, str) or len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must include both letters and numbers."
    if len(password.encode("utf-8")) > 72:
        return "Password must be at most 72 bytes (about 72 characters)."
    return None


def generate_temp_password(length=12):
    """Random temporary password that satisfies validate_password (letters and numbers)."""
    import secrets
    import string
    while True:
        p = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(length))
        if validate_password(p) is None:
            return p
