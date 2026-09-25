"""Remove patient identity from a DICOM file before anything keeps it.

The rule is an allowlist, not a denylist. Rather than hunting for every place a
vendor might have written a name, the output is built from nothing and only
the attributes listed in `KEPT_ATTRIBUTES` are copied into it. A tag nobody
thought of — a private vendor field, an overlay with the patient's name on it,
a free-text comment — never reaches the output, because nothing copies it.
This works the same for every X-ray machine without knowing which one sent
the file, and when it errs, it errs towards removing too much, never towards
leaking.

Image quality is the second rule, and the reason the kept list is as long as
it is. The pixels are never decoded or re-encoded here: the output carries the
exact PixelData bytes it arrived with, in the same transfer syntax, so a
compressed file stays compressed the same way and an uncompressed one stays
bit-for-bit identical. Every attribute that decides how those pixels are shown
or measured — bit depth, rescale, windowing, lookup tables, pixel spacing — is
kept, because an image that loses them is displayed with the wrong contrast or
measured at the wrong scale, which is a quality loss even when not one pixel
changed.

What this does not do yet: find text burned into the pixels themselves. A file
that declares such text (Burned In Annotation = YES) is refused rather than
stored. A file that declares nothing is accepted, and detecting undeclared
text is a separate step still to be built.

Follows the Basic Application Level Confidentiality Profile of DICOM PS3.15
Annex E, and records that it did so in the output.
"""

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO

from pydicom import dcmread
from pydicom.datadict import dictionary_VM, dictionary_VR
from pydicom.dataelem import DataElement
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import UID, generate_uid

# The longest value each kept text type may hold, per value, from PS3.5.
_TEXT_LIMITS = {"CS": 16, "DS": 16, "IS": 12, "LO": 64, "SH": 16, "UI": 64}

# Characters each constrained type may use, from PS3.5. CS is allowed lower
# case, which the standard forbids: some vendors write "Chest", and refusing a
# real X-ray over its capitalisation would help nobody.
_TEXT_ALPHABETS = {
    "CS": frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 _"),
    "DS": frozenset("0123456789+-.Ee "),
    "IS": frozenset("0123456789+- "),
    "UI": frozenset("0123456789."),
}

# What a file must state before its pixels can be checked or described.
_IMAGE_SIZE = ("Rows", "Columns", "BitsAllocated", "BitsStored")

# The DICOM part-10 marker: a 128-byte preamble, then these four bytes.
_DICOM_MARKER_OFFSET = 128
_DICOM_MARKER = b"DICM"

KEPT_ATTRIBUTES: frozenset[str] = frozenset(
    {
        # How the text in the kept attributes is encoded. Carries no identity,
        # and dropping it could garble the rest.
        "SpecificCharacterSet",
        # What the image is.
        "SOPClassUID",
        "Modality",
        "ImageType",
        # The pixels and how they are laid out.
        "SamplesPerPixel",
        "PhotometricInterpretation",
        "Rows",
        "Columns",
        "BitsAllocated",
        "BitsStored",
        "HighBit",
        "PixelRepresentation",
        "PlanarConfiguration",
        "NumberOfFrames",
        "SmallestImagePixelValue",
        "LargestImagePixelValue",
        "PixelPaddingValue",
        "PixelPaddingRangeLimit",
        "PixelData",
        # How stored values become what a reader sees. Without these the same
        # pixels are displayed with the wrong contrast.
        "RescaleIntercept",
        "RescaleSlope",
        "RescaleType",
        "WindowCenter",
        "WindowWidth",
        "WindowCenterWidthExplanation",
        "VOILUTFunction",
        "PresentationLUTShape",
        # Geometry. Without spacing a measurement on the image is meaningless,
        # and without orientation left and right are guesswork.
        "PixelSpacing",
        "ImagerPixelSpacing",
        "PixelAspectRatio",
        "PatientOrientation",
        "ViewPosition",
        "ImageLaterality",
        "Laterality",
        "BodyPartExamined",
        # Whether the image already lost information before it reached us. A
        # reader has to be able to tell, so the record survives as it came.
        "LossyImageCompression",
        "LossyImageCompressionRatio",
        "LossyImageCompressionMethod",
        # The sender's own declaration about text in the pixels. Kept only as
        # "NO": a "YES" never gets this far, and an absent value stays absent
        # rather than being turned into a claim nobody checked.
        "BurnedInAnnotation",
    }
)

# Sequences hold items, and an item can hold any tag at all, a patient name
# included. So the allowlist reaches inside them too: each kept sequence names
# the attributes its items may keep, and everything else in an item is left
# behind exactly as it is at the top level. The free-text LUTExplanation is
# left behind as well; it labels the curve and changes nothing about it.
KEPT_IN_SEQUENCES: dict[str, frozenset[str]] = {
    "VOILUTSequence": frozenset({"LUTDescriptor", "LUTData"}),
    "ModalityLUTSequence": frozenset({"LUTDescriptor", "ModalityLUTType", "LUTData"}),
}

# Required by the image's DICOM definition, so they stay present — but empty.
# An empty patient name is a valid file; a missing one is not.
EMPTIED_ATTRIBUTES: tuple[str, ...] = (
    "PatientName",
    "PatientID",
    "PatientBirthDate",
    "PatientSex",
    "StudyDate",
    "StudyTime",
    "ReferringPhysicianName",
    "StudyID",
    "AccessionNumber",
    "SeriesNumber",
    "InstanceNumber",
)

# Replaced, never copied. An original UID can be looked up in the hospital's
# own archive and leads straight back to the patient.
REPLACED_UIDS: tuple[str, ...] = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
)

# At most 64 characters: the attribute is a LO, and a longer value is invalid.
DEIDENTIFICATION_METHOD = "TBScreen.AI allowlist, PS3.15 Annex E basic profile"


class DeidentificationError(ValueError):
    """The file cannot be made safe to keep, so it must not be kept."""


class NotDicomError(DeidentificationError):
    """The bytes are not a DICOM part-10 file, or are too damaged to read."""


class NotAnImageError(DeidentificationError):
    """No pixel data, or no description of it: a report, not an X-ray.

    Also what a compressed file cut off part-way looks like, because pydicom
    drops a compressed pixel element whose end never arrived.
    """


class IncompleteImageError(DeidentificationError):
    """The pixel data is shorter than the image it describes.

    The usual cause is an upload cut off part-way, which on a field connection
    is ordinary. Keeping such a file would store a damaged image that still
    looks like a valid one.
    """


class BurnedInTextError(DeidentificationError):
    """The sender says text is burned into the pixels.

    That text is usually the patient's name or number, and removing tags does
    nothing about it. Refused rather than stored, until pixel cleaning exists.
    """


@dataclass(frozen=True)
class DeidentifiedImage:
    """The file to keep, plus the facts about it a caller needs to record."""

    data: bytes
    sop_class_uid: str
    transfer_syntax_uid: str
    modality: str
    rows: int
    columns: int
    bits_stored: int


def is_dicom(raw: bytes) -> bool:
    """Whether the bytes carry the DICOM part-10 marker."""
    end = _DICOM_MARKER_OFFSET + len(_DICOM_MARKER)
    return raw[_DICOM_MARKER_OFFSET:end] == _DICOM_MARKER


def deidentify_dicom(raw: bytes) -> DeidentifiedImage:
    """Return a copy of the file that keeps the image and loses the patient.

    Raises a DeidentificationError subclass when the file cannot be made safe:
    not DICOM, not an image, or declaring text burned into its pixels.
    """
    source = _read(raw)
    try:
        _require_safe_image(source)
        return _build(source)
    except DeidentificationError:
        raise
    except Exception as exc:
        # pydicom converts values lazily, so a damaged file can fail at almost
        # any point of the copy, in almost any exception type. Whatever the
        # failure, the answer is the same: the file cannot be made safe, so it
        # is not kept. Deliberately not logged with its message, which can
        # quote the file's own bytes, identity included.
        raise NotDicomError("DICOM file is damaged and cannot be read") from exc


def _build(source: Dataset) -> DeidentifiedImage:
    output = Dataset()
    for element in source:
        if element.keyword in KEPT_ATTRIBUTES:
            _require_plausible(element)
            output.add(deepcopy(element))
        elif element.keyword in KEPT_IN_SEQUENCES and element.VR == "SQ":
            allowed = KEPT_IN_SEQUENCES[element.keyword]
            setattr(output, element.keyword, Sequence(
                [_filtered_item(item, allowed) for item in element.value]
            ))

    for keyword in EMPTIED_ATTRIBUTES:
        setattr(output, keyword, None)
    for keyword in REPLACED_UIDS:
        setattr(output, keyword, generate_uid(prefix=None))

    output.PatientIdentityRemoved = "YES"
    output.DeidentificationMethod = DEIDENTIFICATION_METHOD
    output.DeidentificationMethodCodeSequence = [_basic_profile_code()]
    # Dates are gone, so intervals between this patient's studies cannot be
    # reconstructed from these files. The standard asks that this be said.
    output.LongitudinalTemporalInformationModified = "REMOVED"

    transfer_syntax = source.file_meta.TransferSyntaxUID
    output.file_meta = FileMetaDataset()
    output.file_meta.TransferSyntaxUID = transfer_syntax
    output.file_meta.MediaStorageSOPClassUID = output.SOPClassUID
    output.file_meta.MediaStorageSOPInstanceUID = output.SOPInstanceUID

    buffer = BytesIO()
    output.save_as(buffer, enforce_file_format=True)
    return DeidentifiedImage(
        data=buffer.getvalue(),
        sop_class_uid=str(output.SOPClassUID),
        transfer_syntax_uid=str(transfer_syntax),
        modality=str(output.get("Modality", "")),
        rows=int(output.Rows),
        columns=int(output.Columns),
        bits_stored=int(output.BitsStored),
    )


def _require_safe_image(source: Dataset) -> None:
    if "PixelData" not in source:
        raise NotAnImageError("DICOM file carries no complete pixel data")
    if any(keyword not in source for keyword in _IMAGE_SIZE):
        raise NotAnImageError("DICOM file does not describe the size of its image")
    if str(source.get("BurnedInAnnotation", "")).upper() == "YES":
        raise BurnedInTextError("DICOM file declares text burned into its pixels")
    _require_complete_pixels(source)


def _filtered_item(item: Dataset, allowed: frozenset[str]) -> Dataset:
    kept = Dataset()
    for element in item:
        if element.keyword in allowed:
            _require_plausible(element)
            kept.add(deepcopy(element))
    return kept


def _require_plausible(element: DataElement) -> None:
    """Refuse a kept element whose value is not the kind its tag holds.

    Found by fuzzing, and the reason this exists: when the length field of a
    kept element is damaged, pydicom reads on past its real end and the value
    swallows the elements that follow — the patient's name among them. The
    element's tag is still on the allowlist, so without this check the name
    would be copied out inside it. A swallowed span always takes part of the
    next element's binary header with it, so it breaks at least one of these
    rules: how many values the tag allows, how long each may be, or which
    characters its type may use.

    Fuzzing also found the second way in: a damaged tag number that turns an
    identifying element into a kept one, StudyID arriving as ImageLaterality.
    The type the file states for the element then disagrees with the one the
    dictionary gives that tag, and the value usually breaks the alphabet of
    the type it is pretending to be.
    """
    if element.VR not in dictionary_VR(element.tag).split(" or "):
        raise NotDicomError("DICOM file is damaged and cannot be read")
    if not _multiplicity_allows(dictionary_VM(element.tag), element.VM):
        raise NotDicomError("DICOM file is damaged and cannot be read")
    limit = _TEXT_LIMITS.get(element.VR)
    if limit is None or element.VM == 0:
        return
    alphabet = _TEXT_ALPHABETS.get(element.VR)
    values = element.value if element.VM > 1 else [element.value]
    for value in values:
        text = str(value)
        if len(text) > limit:
            raise NotDicomError("DICOM file is damaged and cannot be read")
        # ESC is legitimate in free text: it switches character sets.
        if alphabet is not None and not set(text) <= alphabet:
            raise NotDicomError("DICOM file is damaged and cannot be read")
        if any(ch < " " and ch != "\x1b" for ch in text):
            raise NotDicomError("DICOM file is damaged and cannot be read")


def _multiplicity_allows(rule: str, count: int) -> bool:
    """Whether `count` values satisfy a dictionary VM such as 1, 2, 1-n, 2-2n."""
    if count == 0:
        return True
    if "-" not in rule:
        return count == int(rule)
    low, high = rule.split("-")
    if high.endswith("n"):
        step = int(high[:-1] or 1)
        return count >= int(low) and count % step == 0
    return int(low) <= count <= int(high)


def _require_complete_pixels(dataset: Dataset) -> None:
    """Refuse uncompressed pixel data shorter than the image it describes.

    Only uncompressed data can be measured this way; its size follows from
    the image's dimensions. Compressed data that was cut off never reaches
    this point, because pydicom drops the unfinished element and the file is
    refused as having no pixels.
    """
    if dataset.file_meta.TransferSyntaxUID.is_encapsulated:
        return
    bits = (
        int(dataset.Rows)
        * int(dataset.Columns)
        * int(dataset.get("SamplesPerPixel", 1) or 1)
        * int(dataset.get("NumberOfFrames", 1) or 1)
        * int(dataset.BitsAllocated)
    )
    if len(dataset.PixelData) < (bits + 7) // 8:
        raise IncompleteImageError("DICOM pixel data is shorter than its image")


def _read(raw: bytes) -> Dataset:
    """Parse the upload, turning every way it can be malformed into one error.

    The bytes come from a client, so a damaged or hostile file is expected
    input, not an exceptional one.
    """
    if not is_dicom(raw):
        raise NotDicomError("Not a DICOM part-10 file")
    try:
        dataset = dcmread(BytesIO(raw))
        # dcmread defers some parsing; touching every element now surfaces a
        # damaged file here instead of halfway through building the output.
        for _element in dataset.iterall():
            pass
        transfer_syntax = dataset.file_meta.get("TransferSyntaxUID")
        stated = transfer_syntax is not None and UID(transfer_syntax).is_transfer_syntax
    except Exception as exc:
        # Parsing hostile bytes fails in more ways than pydicom documents;
        # fuzzing turned up six exception types. All of them mean the same.
        raise NotDicomError("DICOM file is damaged and cannot be read") from exc
    if not stated:
        raise NotDicomError("DICOM file does not say how its pixels are encoded")
    return dataset


def _basic_profile_code() -> Dataset:
    code = Dataset()
    code.CodeValue = "113100"
    code.CodingSchemeDesignator = "DCM"
    code.CodeMeaning = "Basic Application Confidentiality Profile"
    return code
