"""Upload validation for chest X-rays.

`content_type` is a claim the client makes about its own bytes, and nothing
verifies it. Multipart headers are attacker-controlled, so labelling a payload
`image/png` costs nothing. The file's own signature is the thing worth
checking, and the size has to be bounded before anything reads it.

Deliberately hand-rolled rather than pulling in python-magic (which the
architecture review suggested): that needs a libmagic binary, which is a real
setup burden on the Windows dev machines this project is built on, and three
signatures is not enough logic to justify it. If format support grows past a
handful, revisit.
"""

import os

from fastapi import HTTPException, UploadFile, status

# 128-byte preamble then the literal "DICM" — the DICOM part-10 header.
_DICOM_MAGIC_OFFSET = 128
_DICOM_MAGIC = b"DICM"

# (signature, offset, human-readable format)
_SIGNATURES: tuple[tuple[bytes, int, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", 0, "PNG"),
    (b"\xff\xd8\xff", 0, "JPEG"),
    (_DICOM_MAGIC, _DICOM_MAGIC_OFFSET, "DICOM"),
)

# Enough to cover the DICOM preamble plus its marker.
_HEADER_BYTES = _DICOM_MAGIC_OFFSET + len(_DICOM_MAGIC)

SUPPORTED_FORMATS = tuple(name for _, _, name in _SIGNATURES)


def _detect_format(header: bytes) -> str | None:
    for signature, offset, name in _SIGNATURES:
        if header[offset : offset + len(signature)] == signature:
            return name
    return None


def read_validated_image(upload: UploadFile, max_bytes: int) -> bytes:
    """Return the upload's bytes, or raise the right HTTP error.

    Size is measured by seeking rather than reading, so an oversized file is
    rejected without ever being pulled into memory.
    """
    stream = upload.file

    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)

    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
        )
    if size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Image is {size // 1_048_576} MB; the limit is "
                f"{max_bytes // 1_048_576} MB"
            ),
        )

    header = stream.read(_HEADER_BYTES)
    stream.seek(0)

    detected = _detect_format(header)
    if detected is None:
        # 415 regardless of what the client called it. Saying which formats are
        # accepted is fine; echoing back the claimed content_type is not, since
        # that reflects attacker-supplied text straight into the response.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "File is not a recognised medical image. Supported formats: "
                + ", ".join(SUPPORTED_FORMATS)
            ),
        )

    return stream.read()
