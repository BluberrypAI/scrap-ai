"""Parse one ILCD process dataset directly from a user-installed KEITI ZIP archive.

Licensed raw datasets are never checked in. No LCIA score is fabricated when
the source process does not provide one.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

P = "{http://lca.jrc.it/ILCD/Process}"
C = "{http://lca.jrc.it/ILCD/Common}"
F = "{http://lca.jrc.it/ILCD/Flow}"
FP = "{http://lca.jrc.it/ILCD/FlowProperty}"
UG = "{http://lca.jrc.it/ILCD/UnitGroup}"


def _text(node, path, default=None):
    child = node.find(path) if node is not None else None
    return (child.text or "").strip() if child is not None and child.text else default


def decimal_value(value, field):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("non-finite")
        return number
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc


def _entry_by_uuid(archive, folder, uuid):
    """ILCD export filenames usually contain UUID plus version suffix."""
    if not uuid:
        return None
    prefix = f"ILCD/{folder}/{uuid}"
    matches = [
        name
        for name in archive.namelist()
        if name.startswith(prefix) and name.lower().endswith(".xml")
    ]
    if len(matches) != 1:
        return None
    return ET.fromstring(archive.read(matches[0]))


def _reference_mass_unit(archive, flow_id):
    flow = _entry_by_uuid(archive, "flows", flow_id)
    if flow is None:
        return None
    refid = _text(flow, f".//{F}referenceToReferenceFlowProperty")
    prop = next(
        (p for p in flow.findall(f".//{F}flowProperty") if p.get("dataSetInternalID") == refid),
        None,
    )
    ref = prop.find(f"{F}referenceToFlowPropertyDataSet") if prop is not None else None
    propdoc = (
        _entry_by_uuid(archive, "flowproperties", ref.get("refObjectId"))
        if ref is not None
        else None
    )
    refgroup = (
        propdoc.find(f".//{FP}referenceToReferenceUnitGroup") if propdoc is not None else None
    )
    group = (
        _entry_by_uuid(archive, "unitgroups", refgroup.get("refObjectId"))
        if refgroup is not None
        else None
    )
    if group is None:
        return None
    unitid = _text(group, f".//{UG}referenceToReferenceUnit")
    unit = next(
        (u for u in group.findall(f".//{UG}unit") if u.get("dataSetInternalID") == unitid),
        None,
    )
    return _text(unit, f"{UG}name")


@dataclass(frozen=True)
class LCIDataset:
    dataset_key: str
    filename: str
    source_sha256: str
    process_uuid: str
    name: str
    year: int | None
    country: str | None
    valid_until: int | None
    reference_flow_id: str
    reference_flow_name: str
    reference_direction: str
    reference_quantity: Decimal
    reference_unit: str | None
    climate_change_amount: Decimal | None
    climate_method_uuid: str | None
    climate_unit_status: str
    waste_pet_input_per_ref: Decimal | None

    def to_dict(self):
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


def parse_archive(filename: str | Path) -> LCIDataset:
    """Inspect a single-process KEITI export, resolving reference flow mass units."""
    src = Path(filename)
    with ZipFile(src) as archive:
        paths = [
            name
            for name in archive.namelist()
            if name.startswith("ILCD/processes/") and name.lower().endswith(".xml")
        ]
        if len(paths) != 1:
            raise ValueError(f"Expected one process XML, got {len(paths)} in {src.name}")
        root = ET.fromstring(archive.read(paths[0]))
        info = root.find(f".//{P}dataSetInformation")
        if info is None:
            raise ValueError("Missing dataSetInformation")
        name = _text(info, f".//{P}baseName")
        if not name:
            raise ValueError("Missing process name")
        refid = _text(root, f".//{P}referenceToReferenceFlow")
        refex = next(
            (ex for ex in root.findall(f".//{P}exchange") if ex.get("dataSetInternalID") == refid),
            None,
        )
        if refex is None:
            raise ValueError("Missing reference flow exchange")
        flow = refex.find(f"{P}referenceToFlowDataSet")
        if flow is None or not flow.get("refObjectId"):
            raise ValueError("Missing reference flow UUID")
        flow_uuid = flow.get("refObjectId")
        reference_flow_name = _text(flow, f"{C}shortDescription") or ""
        ref_amount = decimal_value(_text(refex, f"{P}meanAmount"), "reference quantity")
        if ref_amount <= 0:
            raise ValueError("Reference quantity must be positive")
        unit = _reference_mass_unit(archive, flow_uuid)
        if unit is not None and unit != "kg":
            raise ValueError(f"Unsupported reference mass unit: {unit}")

        climate = None
        method_uuid = None
        for result in root.findall(f".//{P}LCIAResult"):
            method = result.find(f"{P}referenceToLCIAMethodDataSet")
            if method is not None and _text(method, f"{C}shortDescription") == "Climate change":
                if climate is not None:
                    raise ValueError("Ambiguous total Climate change results")
                climate = decimal_value(_text(result, f"{P}meanAmount"), "LCIA climate")
                method_uuid = method.get("refObjectId")
        method_doc = _entry_by_uuid(archive, "lciamethods", method_uuid) if method_uuid else None
        unit_status = (
            "NO_LCIA_RESULT"
            if climate is None
            else "METHOD_METADATA_PRESENT_VERIFY_MANUALLY"
            if method_doc is not None
            else "LCIA_METHOD_UNIT_NOT_IN_ARCHIVE"
        )
        waste_in = None
        for ex in root.findall(f".//{P}exchange"):
            flow = ex.find(f"{P}referenceToFlowDataSet")
            desc = _text(flow, f"{C}shortDescription", "") if flow is not None else ""
            if _text(ex, f"{P}exchangeDirection") == "Input" and re.search(
                r"waste\s+polyethylene terephthalate\s*\(?pet\)?\s+bottles",
                desc,
                re.IGNORECASE,
            ):
                if waste_in is not None:
                    raise ValueError("Ambiguous waste PET bottle input")
                if _reference_mass_unit(archive, flow.get("refObjectId")) != "kg":
                    raise ValueError("Waste PET bottle input kg unit is unverified")
                waste_in = decimal_value(_text(ex, f"{P}meanAmount"), "waste PET input")
        year = _text(root, f".//{P}referenceYear")
        until = _text(root, f".//{P}dataSetValidUntil")
        return LCIDataset(
            dataset_key=src.stem.replace(" (1)", "").replace("(1)", "").strip().lower(),
            filename=src.name,
            source_sha256=hashlib.sha256(src.read_bytes()).hexdigest(),
            process_uuid=_text(info, f"{C}UUID") or "",
            name=name,
            year=int(year) if year and year.isdigit() else None,
            country=_text(root, f".//{P}locationOfOperationSupplyOrProduction"),
            valid_until=int(until) if until and until.isdigit() else None,
            reference_flow_id=flow_uuid,
            reference_flow_name=reference_flow_name,
            reference_direction=_text(refex, f"{P}exchangeDirection") or "",
            reference_quantity=ref_amount,
            reference_unit=unit,
            climate_change_amount=climate,
            climate_method_uuid=method_uuid,
            climate_unit_status=unit_status,
            waste_pet_input_per_ref=waste_in,
        )


def load_local_datasets(data_dir: str | Path) -> list[LCIDataset]:
    """Only explicitly recognized local KEITI archives; no untrusted uploads."""
    folder = Path(data_dir)
    datasets = []
    source_names = {
        "LCI-2023-168": ("LCI-2023-168.zip",),
        "LCI-2023-186": ("LCI-2023-186.zip", "LCI-2023-186 (1).zip"),
        "LCI-2023-187": ("LCI-2023-187.zip",),
        "LCI-2023-188": ("LCI-2023-188.zip",),
        "LCI-2023-189": ("LCI-2023-189.zip",),
        "LCI-2023-190": ("LCI-2023-190.zip",),
        "LCI-2023-191": ("LCI-2023-191.zip",),
        "LCI-2024-411": ("LCI-2024-411(1).zip", "LCI-2024-411.zip"),
    }
    for dataset_key, filenames in source_names.items():
        present = [folder / name for name in filenames if (folder / name).is_file()]
        if len(present) > 1:
            raise ValueError(f"Multiple archives for {dataset_key}; keep just one local copy")
        if present:
            datasets.append(parse_archive(present[0]))
    return datasets
