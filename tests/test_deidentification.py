"""DICOM de-identification — what must disappear, and what must not change.

Every test builds its own file. No real patient data is needed to prove the
rule, and none belongs in a repository: the fixture below is deliberately
dirtier than a real X-ray machine's output, with identity planted in the
places vendors actually use — patient and study fields, free text, nested
sequences, a private vendor block, and an overlay with a name drawn on it.
"""

from io import BytesIO

import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.filebase import DicomBytesIO
from pydicom.filewriter import write_dataset, write_file_meta_info
from pydicom.uid import (
    UID,
    DigitalXRayImageStorageForPresentation,
    ExplicitVRLittleEndian,
    JPEG2000Lossless,
)

from app.services.deidentify import (
    DEIDENTIFICATION_METHOD,
    EMPTIED_ATTRIBUTES,
    KEPT_ATTRIBUTES,
    REPLACED_UIDS,
    BurnedInTextError,
    IncompleteImageError,
    NotAnImageError,
    NotDicomError,
    deidentify_dicom,
    is_dicom,
)

ROWS = COLUMNS = 16
# Little-endian 16-bit ramp. No run of these bytes spells any of the identity
# strings below, so a byte search over the whole output stays meaningful.
PIXELS = b"".join(value.to_bytes(2, "little") for value in range(ROWS * COLUMNS))

ORIGINAL_UIDS = {
    "StudyInstanceUID": "1.2.826.0.1.3680043.2.1125.1.111",
    "SeriesInstanceUID": "1.2.826.0.1.3680043.2.1125.1.222",
    "SOPInstanceUID": "1.2.826.0.1.3680043.2.1125.1.333",
}

# Every value below identifies the patient, the visit, or the place. None of
# them may appear anywhere in the output, in any tag, at any depth.
IDENTIFYING = [
    "AMINAH", "SITI", "RM-778812", "9171010203710001", "19710203", "SENTANI",
    "081234567890", "TETANGGA", "20260921", "101530", "ACC-99121", "ST-4411",
    "SANTOSO", "RINA", "JAYAPURA", "RUANG-3", "SN-45A9", "REQ-5561", "ACME",
]

# How the image is shown and measured. Each must come through unchanged.
QUALITY = {
    "PhotometricInterpretation": "MONOCHROME1",
    "BitsAllocated": 16,
    "BitsStored": 12,
    "HighBit": 11,
    "PixelRepresentation": 0,
    "RescaleIntercept": "0",
    "RescaleSlope": "1",
    "RescaleType": "US",
    "WindowCenter": "2048",
    "WindowWidth": "4096",
    "VOILUTFunction": "LINEAR",
    "PixelSpacing": [0.139, 0.139],
    "ImagerPixelSpacing": [0.143, 0.143],
    "ViewPosition": "PA",
    "BodyPartExamined": "CHEST",
    "LossyImageCompression": "00",
}


def _item(**values: object) -> Dataset:
    item = Dataset()
    for keyword, value in values.items():
        setattr(item, keyword, value)
    return item


def _dirty_xray(
    *,
    transfer_syntax: UID = ExplicitVRLittleEndian,
    pixel_data: bytes = PIXELS,
    burned_in: str | None = "NO",
    with_pixels: bool = True,
) -> bytes:
    """A chest X-ray with identity planted everywhere a vendor might put it."""
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.SOPClassUID = DigitalXRayImageStorageForPresentation
    for keyword, uid in ORIGINAL_UIDS.items():
        setattr(ds, keyword, uid)

    # The obvious places.
    ds.PatientName = "AMINAH^SITI"
    ds.PatientID = "RM-778812"
    ds.IssuerOfPatientID = "RSUD JAYAPURA"
    ds.PatientBirthDate = "19710203"
    ds.PatientSex = "F"
    ds.PatientAge = "055Y"
    ds.PatientAddress = "JL RAYA SENTANI 12"
    ds.PatientTelephoneNumbers = "081234567890"
    ds.StudyDate = ds.ContentDate = "20260921"
    ds.StudyTime = "101530"
    ds.AcquisitionDateTime = "20260921101530"
    ds.AccessionNumber = "ACC-99121"
    ds.StudyID = "ST-4411"
    ds.ReferringPhysicianName = "SANTOSO^BUDI^^DR."
    ds.OperatorsName = "RINA^OPERATOR"
    ds.InstitutionName = "RSUD DOK II JAYAPURA"
    ds.StationName = "XRAY-RUANG-3"
    ds.DeviceSerialNumber = "SN-45A9-7731"

    # Free text, where people write what they know.
    ds.StudyDescription = "THORAX PA SITI AMINAH"
    ds.AdditionalPatientHistory = "BATUK 3 MINGGU, TETANGGA TB"

    # Nested, where a shallow search never looks.
    ds.OtherPatientIDsSequence = [_item(PatientID="9171010203710001")]
    ds.RequestAttributesSequence = [_item(RequestedProcedureID="REQ-5561")]

    # A private vendor block, which no dictionary can describe.
    ds.private_block(0x0029, "ACME XRAY", create=True).add_new(
        0x10, "LO", "AMINAH SITI RM-778812"
    )

    # An overlay: a separate bitmap drawn on top of the image, labelled here
    # with the patient's name.
    ds.add_new(0x60000010, "US", ROWS)
    ds.add_new(0x60000011, "US", COLUMNS)
    ds.add_new(0x60000022, "LO", "SITI AMINAH")
    ds.add_new(0x60000040, "CS", "G")
    ds.add_new(0x60000050, "SS", [1, 1])
    ds.add_new(0x60000100, "US", 1)
    ds.add_new(0x60000102, "US", 0)
    ds.add_new(0x60003000, "OW", bytes(ROWS * COLUMNS // 8))

    # The image itself, and everything that decides how it looks.
    ds.Modality = "DX"
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.SamplesPerPixel = 1
    ds.Rows = ROWS
    ds.Columns = COLUMNS
    for keyword, value in QUALITY.items():
        setattr(ds, keyword, value)
    if burned_in is not None:
        ds.BurnedInAnnotation = burned_in
    if with_pixels:
        ds.PixelData = pixel_data

    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = transfer_syntax
    ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    buffer = BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def _read(data: bytes) -> Dataset:
    return dcmread(BytesIO(data))


class TestIdentityIsGone:
    def test_no_identifying_value_survives_anywhere_in_the_file(self) -> None:
        # Searched in the raw bytes, not through the parsed tags, so a value
        # hiding in a private block, an overlay or a nested sequence is
        # caught just the same as one in PatientName.
        output = deidentify_dicom(_dirty_xray()).data
        for value in IDENTIFYING:
            assert value.encode() not in output, value

    def test_every_attribute_in_the_output_is_one_we_chose(self) -> None:
        # The allowlist's promise, checked from the other side: whatever the
        # source contained, the output holds only what the module names.
        written_by_us = {
            "PatientIdentityRemoved",
            "DeidentificationMethod",
            "DeidentificationMethodCodeSequence",
            "LongitudinalTemporalInformationModified",
        }
        allowed = KEPT_ATTRIBUTES | set(EMPTIED_ATTRIBUTES) | set(REPLACED_UIDS) | written_by_us

        output = _read(deidentify_dicom(_dirty_xray()).data)
        for element in output:
            assert not element.tag.is_private, element
            assert element.tag.group & 0xFF00 != 0x6000, element
            assert element.keyword in allowed, element

    def test_the_original_uids_are_replaced_consistently(self) -> None:
        # An original UID can be looked up in the hospital archive and leads
        # straight back to the patient.
        data = deidentify_dicom(_dirty_xray()).data
        output = _read(data)
        for keyword, original in ORIGINAL_UIDS.items():
            assert original.encode() not in data
            assert UID(getattr(output, keyword)).is_valid
        assert output.file_meta.MediaStorageSOPInstanceUID == output.SOPInstanceUID

    def test_the_same_file_uploaded_twice_cannot_be_linked_by_uid(self) -> None:
        raw = _dirty_xray()
        first, second = _read(deidentify_dicom(raw).data), _read(deidentify_dicom(raw).data)
        for keyword in REPLACED_UIDS:
            assert getattr(first, keyword) != getattr(second, keyword)

    def test_required_patient_fields_stay_present_but_empty(self) -> None:
        # The image definition requires them. Present and empty is a valid
        # file; missing would make some viewers refuse to open it.
        output = _read(deidentify_dicom(_dirty_xray()).data)
        for keyword in EMPTIED_ATTRIBUTES:
            assert keyword in output
            assert not output[keyword].value

    def test_the_file_records_what_was_done_to_it(self) -> None:
        output = _read(deidentify_dicom(_dirty_xray()).data)
        assert output.PatientIdentityRemoved == "YES"
        assert output.DeidentificationMethod == DEIDENTIFICATION_METHOD
        assert output.DeidentificationMethodCodeSequence[0].CodeValue == "113100"
        assert output.LongitudinalTemporalInformationModified == "REMOVED"


class TestImageQualityIsKept:
    def test_the_pixels_are_untouched(self) -> None:
        output = _read(deidentify_dicom(_dirty_xray()).data)
        assert output.PixelData == PIXELS

    def test_every_display_and_geometry_attribute_comes_through_exactly(self) -> None:
        # The same pixels with a different window, rescale or spacing are a
        # different picture: wrong contrast, or measured at the wrong scale.
        output = _read(deidentify_dicom(_dirty_xray()).data)
        for keyword, expected in QUALITY.items():
            actual = getattr(output, keyword)
            if isinstance(expected, list):
                assert [float(v) for v in actual] == expected, keyword
            else:
                assert actual == expected, keyword
        assert (output.Rows, output.Columns) == (ROWS, COLUMNS)
        assert output.Modality == "DX"

    def test_a_compressed_file_stays_compressed_exactly_as_it_came(self) -> None:
        # Never decoded, never re-encoded: the compressed stream passes
        # through byte for byte, in the same transfer syntax. Re-encoding is
        # where quality is lost, so it does not happen here at all.
        frame = b"\xff\x4f\xff\x51" + bytes(range(60))
        stream = encapsulate([frame])
        raw = _dirty_xray(transfer_syntax=JPEG2000Lossless, pixel_data=stream)

        result = deidentify_dicom(raw)
        output = _read(result.data)
        assert result.transfer_syntax_uid == JPEG2000Lossless
        assert output.file_meta.TransferSyntaxUID == JPEG2000Lossless
        assert output.PixelData == stream

    def test_the_result_reports_the_image_it_holds(self) -> None:
        result = deidentify_dicom(_dirty_xray())
        assert result.sop_class_uid == DigitalXRayImageStorageForPresentation
        assert result.transfer_syntax_uid == ExplicitVRLittleEndian
        assert (result.modality, result.rows, result.columns, result.bits_stored) == (
            "DX", ROWS, COLUMNS, 12,
        )

    def test_an_absent_burned_in_declaration_is_not_invented(self) -> None:
        # Nobody checked the pixels for text, so the output must not claim
        # that someone did.
        output = _read(deidentify_dicom(_dirty_xray(burned_in=None)).data)
        assert "BurnedInAnnotation" not in output


class TestUnsafeFilesAreRefused:
    def test_declared_burned_in_text_is_refused(self) -> None:
        # The text is usually the patient's name, and no tag removal touches
        # it. Refused rather than stored.
        with pytest.raises(BurnedInTextError):
            deidentify_dicom(_dirty_xray(burned_in="YES"))

    @pytest.mark.parametrize(
        "raw",
        [b"", b"\x89PNG\r\n\x1a\n" + bytes(200), bytes(128) + b"DIC"],
        ids=["empty", "png", "cut-off-marker"],
    )
    def test_something_that_is_not_dicom_is_refused(self, raw: bytes) -> None:
        assert not is_dicom(raw)
        with pytest.raises(NotDicomError):
            deidentify_dicom(raw)

    def test_a_damaged_file_is_refused_not_half_read(self) -> None:
        # The marker is right, the rest is cut off mid-element.
        damaged = _dirty_xray()[:200]
        assert is_dicom(damaged)
        with pytest.raises(NotDicomError):
            deidentify_dicom(damaged)

    def test_a_file_without_pixels_is_refused(self) -> None:
        with pytest.raises(NotAnImageError):
            deidentify_dicom(_dirty_xray(with_pixels=False))

    def test_a_file_that_hides_its_pixel_encoding_is_refused(self) -> None:
        # pydicom will guess how such a file is encoded and read it anyway,
        # but without a stated transfer syntax the output cannot say how its
        # pixels are stored, and a guess is not good enough for a clinical
        # image. Refused cleanly instead of failing halfway through.
        image = Dataset()
        image.SOPClassUID = DigitalXRayImageStorageForPresentation
        image.SOPInstanceUID = ORIGINAL_UIDS["SOPInstanceUID"]
        image.Rows, image.Columns = ROWS, COLUMNS
        image.SamplesPerPixel, image.PhotometricInterpretation = 1, "MONOCHROME2"
        image.BitsAllocated, image.BitsStored, image.HighBit = 16, 12, 11
        image.PixelRepresentation = 0
        image.PixelData = PIXELS
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = image.SOPClassUID
        meta.MediaStorageSOPInstanceUID = image.SOPInstanceUID

        stream = DicomBytesIO()
        stream.is_little_endian, stream.is_implicit_VR = True, False
        stream.write(bytes(128) + b"DICM")
        write_file_meta_info(stream, meta, enforce_standard=False)
        write_dataset(stream, image)

        with pytest.raises(NotDicomError):
            deidentify_dicom(stream.getvalue())

    @pytest.mark.parametrize("missing", [1, 2, 100])
    def test_an_uncompressed_image_cut_off_is_refused(self, missing: int) -> None:
        # An upload interrupted part-way through the pixels. pydicom reads
        # what arrived without complaint, so without this check a damaged
        # image would be stored looking exactly like a whole one.
        cut_off = _dirty_xray()[:-missing]
        with pytest.raises(IncompleteImageError):
            deidentify_dicom(cut_off)

    def test_a_compressed_image_cut_off_is_refused(self) -> None:
        stream = encapsulate([b"\xff\x4f\xff\x51" + bytes(range(200))])
        whole = _dirty_xray(transfer_syntax=JPEG2000Lossless, pixel_data=stream)
        with pytest.raises(NotAnImageError):
            deidentify_dicom(whole[:-50])

    def test_an_image_that_does_not_state_its_size_is_refused(self) -> None:
        image = _read(_dirty_xray())
        del image.Rows
        buffer = BytesIO()
        image.save_as(buffer, enforce_file_format=True)
        with pytest.raises(NotAnImageError):
            deidentify_dicom(buffer.getvalue())


class TestDamagedFilesNeverLeak:
    """A seeded sample of the fuzzing that shaped _require_plausible.

    Damaged files arrive from the field as a matter of course: an upload cut
    off, a byte flipped on a bad line. Each one here is the dirty X-ray with
    its header damaged at random. Whatever happens, one of two things must be
    true: the file is refused with a DeidentificationError, or what comes out
    carries none of the identity planted in it. A fresh random UID can contain
    a planted digit string by pure chance, so the generated UIDs are the one
    place not searched.
    """

    CASES = 1500

    # pydicom warns about every damaged tag it has to guess at, which here is
    # most of them. Expected, and hundreds of lines of it would bury the
    # warnings that matter in everyone's test output.
    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_damage_never_lets_identity_through(self) -> None:
        import random

        from app.services.deidentify import DeidentificationError

        seed = _dirty_xray()
        header_end = seed.find(b"\xe0\x7f\x10\x00")
        rng = random.Random(20260925)
        generated = {*REPLACED_UIDS, "MediaStorageSOPInstanceUID"}
        accepted = 0
        for _ in range(self.CASES):
            damaged = bytearray(seed)
            at = rng.randrange(132, header_end)
            kind = rng.randrange(4)
            if kind == 0:
                damaged[at] = rng.randrange(256)
            elif kind == 1:
                damaged[at : at + 4] = rng.randrange(2**32).to_bytes(4, "little")
            elif kind == 2:
                del damaged[at : at + rng.randrange(1, 9)]
            else:
                damaged[at:at] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 9)))
            try:
                output = deidentify_dicom(bytes(damaged)).data
            except DeidentificationError:
                continue
            accepted += 1
            written = _read(output)
            for element in [*written.iterall(), *written.file_meta]:
                if element.keyword in generated:
                    continue
                for value in IDENTIFYING:
                    assert value not in str(element.value), (element.keyword, value)
        # Guard against a sample that proves nothing because every case was
        # refused before reaching the copy.
        assert accepted > self.CASES // 10


class TestDisplayCurvesSurvive:
    def test_a_display_lut_is_kept_exactly_and_only_its_label_goes(self) -> None:
        # Some machines ship the contrast curve as a lookup table instead of
        # a window. Losing it changes how the X-ray looks on screen, so the
        # curve comes through untouched, while its free-text label, and
        # anything planted in the item beside it, is left behind.
        source = _read(_dirty_xray())
        curve = Dataset()
        # Both tags allow two value types; a real machine states which one it
        # wrote, and so does this file.
        curve.add_new(0x00283002, "US", [4, 0, 16])
        curve.add_new(0x00283006, "US", [0, 1200, 2400, 4095])
        curve.LUTExplanation = "AMINAH"
        curve.PatientName = "SITI"
        source.VOILUTSequence = [curve]
        buffer = BytesIO()
        source.save_as(buffer, enforce_file_format=True)

        [kept] = _read(deidentify_dicom(buffer.getvalue()).data).VOILUTSequence

        assert list(kept.LUTDescriptor) == [4, 0, 16]
        assert list(kept.LUTData) == [0, 1200, 2400, 4095]
        assert "LUTExplanation" not in kept
        assert "PatientName" not in kept


@pytest.mark.parametrize(
    ("rule", "count", "allowed"),
    [
        ("1", 0, True),
        ("1", 1, True),
        ("1", 2, False),
        ("1-n", 5, True),
        ("1-3", 2, True),
        ("1-3", 4, False),
        ("2-2n", 4, True),
        ("2-2n", 3, False),
    ],
)
def test_value_multiplicity_rules(rule: str, count: int, allowed: bool) -> None:
    from app.services.deidentify import _multiplicity_allows

    assert _multiplicity_allows(rule, count) is allowed
