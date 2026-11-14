"""Conservative, source-bound W2RKG → KEITI LCI plastic carbon estimates.

No result in this module is permission to dispatch waste, a facility-specific
measurement, or an ISO-conformant comparative assertion.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from resource_ai.calculators.lci.ilcd import LCIDataset, decimal_value

# These are verified reference *process* UUIDs from the supplied 2023 archives.
# The flake LCI is a SINGLE aggregated boundary, never four separate factors.
PET_FLAKE_UUID = "f77f5793-82c4-4731-a011-ecd8e3b593ce"
PET_INCINERATION_UUID = "922879a8-8826-41ab-9fdb-9db61742a149"

# Process UUIDs independently verified against each source ZIP (not emission factors).
# Route matching only accepts explicit whole-process incineration for the exact resin.
INCINERATION_PROCESS_UUIDS = {
    "pet": PET_INCINERATION_UUID,
    "hdpe": "cdb9c4a6-87f6-4673-b48f-54715a347dc3",
    "ldpe": "a7ca2ffa-e6db-44bc-99bd-27fe64cbd380",
    "pp": "f863dfc3-1af5-4815-9798-15ce74225b7a",
    "ps": "da5a226c-3799-42cf-9a34-fbb0b065cb2c",
    "pvc": "b7975da6-c4bc-48fd-b3a8-68b421c4082a",
}
MATERIAL_ALIASES = {
    "pet": "pet",
    "폐pet": "pet",
    "폐페트": "pet",
    "waste pet": "pet",
    "waste pet bottles": "pet",
    "hdpe": "hdpe",
    "폐hdpe": "hdpe",
    "high density polyethylene": "hdpe",
    "ldpe": "ldpe",
    "폐ldpe": "ldpe",
    "low density polyethylene": "ldpe",
    "pp": "pp",
    "폐pp": "pp",
    "polypropylene": "pp",
    "ps": "ps",
    "폐ps": "ps",
    "polystyrene": "ps",
    "pvc": "pvc",
    "폐pvc": "pvc",
    "polyvinyl chloride": "pvc",
}
FLAKE_STEPS = ("sorting", "shredding", "washing", "drying")
KG_FLAKE_TRANSITIONS = ("pet-prepare", "pet-wash", "pet-finish")
ALIASES = {
    "선별": "sorting",
    "sorting": "sorting",
    "selection": "sorting",
    "파쇄": "shredding",
    "분쇄": "shredding",
    "shredding": "shredding",
    "crushing": "shredding",
    "세척": "washing",
    "washing": "washing",
    "건조": "drying",
    "drying": "drying",
    "소각": "incineration",
    "incineration": "incineration",
}


def _status(candidate, status, reason, **details):
    return {
        "candidate_id": candidate.get("candidate_id"),
        "calculation_status": status,
        "reason": reason,
        "carbon_kgco2e": None,
        "comparable": False,
        "recommended": False,
        "dispatch_approved": False,
        **details,
    }


def _route_steps(value):
    if not isinstance(value, list) or not value or len(value) > 10:
        raise ValueError("process_steps must have 1–10 strings")
    if any(not isinstance(step, str) or not step.strip() for step in value):
        raise ValueError("process_steps contains empty or invalid values")
    return [ALIASES.get(step.strip().lower(), step.strip().lower()) for step in value]


def evaluate_candidate(candidate: dict[str, Any], datasets: list[LCIDataset]):
    """Compute only for a verified whole-process plastic treatment route."""
    if not isinstance(candidate, dict):
        raise TypeError("candidate must be an object")
    if not isinstance(candidate.get("candidate_id"), str) or not candidate["candidate_id"]:
        raise ValueError("candidate_id is required")
    mass = decimal_value(candidate.get("input_mass_kg"), "input_mass_kg")
    if mass <= 0 or mass > Decimal("1e12"):
        raise ValueError("input_mass_kg must be positive and bounded")
    steps = _route_steps(candidate.get("process_steps"))
    if len(set(steps)) != len(steps):
        return _status(candidate, "REVIEW_REQUIRED", "Repeated step: double-count risk")
    material = MATERIAL_ALIASES.get(str(candidate.get("material", "")).strip().lower())
    if material is None:
        return _status(candidate, "UNMATCHED", "No validated LCI material mapping")
    if tuple(steps) in (FLAKE_STEPS, KG_FLAKE_TRANSITIONS):
        if material != "pet":
            return _status(candidate, "UNMATCHED", "PET flake LCI cannot represent other resins")
        uuid, kind = PET_FLAKE_UUID, "AGGREGATED"
        transport_boundary = "INCLUDES_COLLECTION_AND_INTERNAL_TRANSPORT"
    elif steps == ["incineration"]:
        uuid, kind = INCINERATION_PROCESS_UUIDS[material], "EXACT"
        transport_boundary = "EXCLUDES_EXTERNAL_COLLECTION_TRANSPORT"
    else:
        return _status(
            candidate, "UNMATCHED", "No validated whole-route LCI mapping", normalized_steps=steps
        )
    matching = [d for d in datasets if d.process_uuid == uuid]
    if len(matching) != 1:
        return _status(candidate, "REVIEW_REQUIRED", "Expected exactly one matching dataset")
    d = matching[0]
    if d.process_uuid != uuid:
        return _status(candidate, "REVIEW_REQUIRED", "Process UUID does not match material")
    base = {
        "lci_mapping_type": kind,
        "covered_steps": steps,
        "dataset_id": d.dataset_key,
        "process_uuid": d.process_uuid,
        "dataset_year": d.year,
        "source_file": d.filename,
        "source_sha256": d.source_sha256,
        "reference_flow": d.reference_flow_name,
        "reference_direction": d.reference_direction,
        "reference_unit": d.reference_unit,
        "source_lcia_result": (
            str(d.climate_change_amount) if d.climate_change_amount is not None else None
        ),
        "climate_method_uuid": d.climate_method_uuid,
        "climate_unit_status": d.climate_unit_status,
        "transport_boundary": transport_boundary,
        "input_mass_kg": str(mass),
        "facility_specific": False,
        "assumptions": [],
        "warnings": [],
    }
    if d.reference_unit != "kg":
        return _status(candidate, "REVIEW_REQUIRED", "Unverified reference mass unit", **base)
    if d.climate_change_amount is None:
        return _status(candidate, "UNAVAILABLE", "No Climate change LCIA in source", **base)
    if d.climate_change_amount < 0:
        return _status(candidate, "REVIEW_REQUIRED", "Negative score needs interpretation", **base)
    if kind == "AGGREGATED":
        if str(candidate.get("waste_form", "")).upper() not in {"PET_BOTTLES", "WASTE_PET_BOTTLES"}:
            return _status(
                candidate, "REVIEW_REQUIRED", "PET bottle feedstock not confirmed", **base
            )
        if d.reference_direction != "Output" or not d.waste_pet_input_per_ref:
            return _status(candidate, "REVIEW_REQUIRED", "Product yield not verifiable", **base)
        if d.waste_pet_input_per_ref <= 0:
            return _status(candidate, "REVIEW_REQUIRED", "Invalid source mass balance", **base)
        if candidate.get("transport") is not None:
            return _status(
                candidate, "REVIEW_REQUIRED", "LCI already contains collection transport", **base
            )
        if "recycled_product_yield" in candidate:
            y = decimal_value(candidate["recycled_product_yield"], "recycled_product_yield")
            if not Decimal(0) < y <= Decimal(1):
                raise ValueError("recycled_product_yield must be within (0,1]")
            refs = mass * y / d.reference_quantity
            base["assumptions"].append(
                "User-supplied yield replaces the national-average LCI mass relationship"
            )
            base["yield_source"] = "USER_SUPPLIED_NOT_VERIFIED"
        else:
            refs = mass / d.waste_pet_input_per_ref
            base["yield_source"] = "SOURCE_INPUT_EXCHANGE"
        base["reference_flow_quantity_kg"] = str(refs * d.reference_quantity)
        base["waste_input_per_reference_kg"] = str(d.waste_pet_input_per_ref)
    else:
        if d.reference_direction != "Input":
            return _status(
                candidate, "REVIEW_REQUIRED", "Incineration reference should be input", **base
            )
        refs = mass / d.reference_quantity
        base["reference_flow_quantity_kg"] = str(mass)
    total = d.climate_change_amount * refs
    if candidate.get("transport") is not None:
        t = candidate["transport"]
        if not isinstance(t, dict) or not isinstance(t.get("source"), str) or not t["source"]:
            return _status(
                candidate, "REVIEW_REQUIRED", "Transport evidence source required", **base
            )
        extra = decimal_value(t.get("emissions_kgco2e"), "transport.emissions_kgco2e")
        if extra < 0:
            raise ValueError("Transport emissions cannot be negative")
        total += extra
        base["transport_emissions_kgco2e"] = str(extra)
        base["transport_source"] = t["source"]
        base["transport_boundary"] = "EXTERNAL_TRANSPORT_ADDED_FROM_SUBMITTED_EVIDENCE"
    elif kind == "EXACT":
        base["warnings"].append("External collection transport is absent")
    base["warnings"].extend(
        [
            "National-average screening estimate; not a facility measurement",
            "Climate LCIA method unit is not independently resolved by the 2023 ZIP",
            "Different functional outputs and transport boundaries are not directly comparable",
        ]
    )
    if d.valid_until is not None and d.valid_until < 2026:
        base["warnings"].append("Source validity metadata predates 2026")
    return _status(
        candidate,
        "ESTIMATED_REVIEW_REQUIRED",
        "Preliminary source-bound estimate",
        carbon_kgco2e=str(total),
        **base,
    )


def candidate_from_plan(
    plan: dict[str, Any], *, material: str, input_mass_kg: str | int, waste_form: str | None = None
):
    """Adapt a real KG ProcessPlan packet; do not infer a facility's capability."""
    if not isinstance(plan, dict) or not isinstance(plan.get("plan_id"), str):
        raise TypeError("plan_id is required")
    packet = plan.get("research_packet")
    if not isinstance(packet, dict) or not isinstance(packet.get("stages"), list):
        return {
            "candidate_id": plan["plan_id"],
            "material": material,
            "input_mass_kg": input_mass_kg,
            "process_steps": ["UNKNOWN_KG_ROUTE"],
            "waste_form": waste_form,
        }
    keys = []
    for stage in packet["stages"]:
        transition = stage.get("transition") if isinstance(stage, dict) else None
        key = transition.get("key") if isinstance(transition, dict) else None
        keys.append(key if isinstance(key, str) and key else "UNKNOWN_KG_STAGE")
    return {
        "candidate_id": plan["plan_id"],
        "material": material,
        "input_mass_kg": input_mass_kg,
        "waste_form": waste_form,
        "process_steps": keys,
    }


def compare_candidates(results: list[dict[str, Any]]):
    """Never claim a Pareto ordering based on non-harmonized LCI scopes."""
    if len(results) < 2:
        return {"status": "INSUFFICIENT_CANDIDATES", "ranked": []}
    if any(row["calculation_status"] != "ESTIMATED_REVIEW_REQUIRED" for row in results):
        return {"status": "BLOCKED_MISSING_OR_UNVERIFIED_CANDIDATE", "ranked": []}
    if (
        len({row["transport_boundary"] for row in results}) > 1
        or len({row["climate_method_uuid"] for row in results}) > 1
    ):
        return {"status": "BLOCKED_BOUNDARY_OR_METHOD_MISMATCH", "ranked": []}
    return {"status": "REVIEW_REQUIRED_BEFORE_COMPARISON", "ranked": []}
