"""KG-to-LCI PET carbon checks using synthetic metadata; raw licensed ZIP is optional."""

from decimal import Decimal
from pathlib import Path

import pytest

from resource_ai.calculators.lci import (
    LCIDataset,
    candidate_from_plan,
    compare_candidates,
    evaluate_candidate,
    load_local_datasets,
)


def source(uuid, *, name, direction, climate, pet_input=None):
    return LCIDataset(
        dataset_key="fixture",
        filename="fixture.zip",
        source_sha256="a" * 64,
        process_uuid=uuid,
        name=name,
        year=2022,
        country="KR",
        valid_until=2025,
        reference_flow_id="flow-id",
        reference_flow_name="reference",
        reference_direction=direction,
        reference_quantity=Decimal(1),
        reference_unit="kg",
        climate_change_amount=Decimal(climate) if climate is not None else None,
        climate_method_uuid="climate-method",
        climate_unit_status="LCIA_METHOD_UNIT_NOT_IN_ARCHIVE",
        waste_pet_input_per_ref=Decimal(pet_input) if pet_input is not None else None,
    )


@pytest.fixture
def datasets():
    from resource_ai.calculators.lci.evaluate import (
        PET_FLAKE_UUID,
        PET_INCINERATION_UUID,
    )

    return [
        source(
            PET_FLAKE_UUID,
            name="Recycled PET flake",
            direction="Output",
            climate="0.57164725210341",
            pet_input="1.27949",
        ),
        source(
            PET_INCINERATION_UUID,
            name="PET incineration",
            direction="Input",
            climate="2.45408129128249",
        ),
    ]


def route(steps, **kwargs):
    return {
        "candidate_id": "kg-example",
        "material": "PET",
        "waste_form": "PET_BOTTLES",
        "input_mass_kg": 1000,
        "process_steps": steps,
        **kwargs,
    }


def test_recycled_flake_aggregate_counts_only_once(datasets):
    row = evaluate_candidate(route(["선별", "파쇄", "세척", "건조"]), datasets)
    assert row["calculation_status"] == "ESTIMATED_REVIEW_REQUIRED"
    assert row["lci_mapping_type"] == "AGGREGATED"
    expected = Decimal(1000) / Decimal("1.27949") * Decimal("0.57164725210341")
    assert Decimal(row["carbon_kgco2e"]) == expected
    assert row["comparable"] is False


def test_real_kg_transition_keys_map_only_exact_route(datasets):
    plan = {
        "plan_id": "plan:pet",
        "research_packet": {
            "stages": [
                {"transition": {"key": k}} for k in ("pet-prepare", "pet-wash", "pet-finish")
            ]
        },
    }
    cand = candidate_from_plan(plan, material="PET", input_mass_kg="1000", waste_form="PET_BOTTLES")
    assert cand["process_steps"] == ["pet-prepare", "pet-wash", "pet-finish"]
    assert evaluate_candidate(cand, datasets)["lci_mapping_type"] == "AGGREGATED"
    plan["research_packet"]["stages"].append({"transition": {"key": "pet-pelletise"}})
    cand = candidate_from_plan(plan, material="PET", input_mass_kg="1000", waste_form="PET_BOTTLES")
    assert evaluate_candidate(cand, datasets)["calculation_status"] == "UNMATCHED"


def test_incineration_per_input_mass(datasets):
    row = evaluate_candidate(route(["소각"]), datasets)
    assert Decimal(row["carbon_kgco2e"]) == Decimal("2454.08129128249000")
    assert "EXCLUDES_EXTERNAL" in row["transport_boundary"]


def test_missing_feedstock_and_transport_double_count_are_blocked(datasets):
    cand = route(["sorting", "shredding", "washing", "drying"])
    cand.pop("waste_form")
    assert evaluate_candidate(cand, datasets)["carbon_kgco2e"] is None
    cand["waste_form"] = "PET_BOTTLES"
    cand["transport"] = {"source": "quote", "emissions_kgco2e": "10"}
    assert evaluate_candidate(cand, datasets)["calculation_status"] == "REVIEW_REQUIRED"


def test_unknown_and_duplicate_routes_are_not_estimable(datasets):
    assert (
        evaluate_candidate(route(["sorting", "pelletizing"]), datasets)["calculation_status"]
        == "UNMATCHED"
    )
    assert (
        evaluate_candidate(route(["sorting", "sorting"]), datasets)["calculation_status"]
        == "REVIEW_REQUIRED"
    )


@pytest.mark.parametrize("mass", ["NaN", "-1", "0", "Infinity", "1e20"])
def test_bad_masses_rejected(datasets, mass):
    with pytest.raises(ValueError):
        evaluate_candidate(route(["소각"], input_mass_kg=mass), datasets)


def test_external_incineration_transport_requires_source(datasets):
    cand = route(["소각"])
    cand["transport"] = {"emissions_kgco2e": "25"}
    assert evaluate_candidate(cand, datasets)["calculation_status"] == "REVIEW_REQUIRED"
    cand["transport"]["source"] = "verified-carrier-input"
    row = evaluate_candidate(cand, datasets)
    assert Decimal(row["carbon_kgco2e"]) == Decimal("2479.08129128249000")


def test_comparison_blocked_for_different_transport_boundaries(datasets):
    rows = [
        evaluate_candidate(route(["선별", "파쇄", "세척", "건조"]), datasets),
        evaluate_candidate(route(["소각"]), datasets),
    ]
    assert compare_candidates(rows)["ranked"] == []
    assert compare_candidates(rows)["status"] == "BLOCKED_BOUNDARY_OR_METHOD_MISMATCH"


def test_missing_lcia_does_not_default_to_zero(datasets):
    from resource_ai.calculators.lci.evaluate import PET_INCINERATION_UUID

    datasets[-1] = source(
        PET_INCINERATION_UUID, name="PET incineration", direction="Input", climate=None
    )
    row = evaluate_candidate(route(["소각"]), datasets)
    assert row["calculation_status"] == "UNAVAILABLE"
    assert row["carbon_kgco2e"] is None


def test_source_missing_rejects_estimate(datasets):
    row = evaluate_candidate(route(["소각"]), datasets[:1])
    assert row["calculation_status"] == "REVIEW_REQUIRED"


def test_kg_packet_absent_does_not_guess_path(datasets):
    cand = candidate_from_plan(
        {"plan_id": "p", "research_packet": None},
        material="PET",
        input_mass_kg=1000,
        waste_form="PET_BOTTLES",
    )
    assert evaluate_candidate(cand, datasets)["calculation_status"] == "UNMATCHED"


def test_real_keiti_archives_when_installed():
    folder = Path("data/local/lci")
    if not (folder / "LCI-2023-168.zip").exists():
        pytest.skip("licensed KEITI ZIPs not present in CI")
    ds = load_local_datasets(folder)
    assert any(d.climate_change_amount == Decimal("0.57164725210341") for d in ds)


# These values are asserted from the five independently inspected, user-installed
# KEITI 2023 ILCD source archives. They are fixtures, not production coefficients.
@pytest.mark.parametrize(
    ("material", "uuid", "climate"),
    [
        ("HDPE", "cdb9c4a6-87f6-4673-b48f-54715a347dc3", "3.29577066046423"),
        ("LDPE", "a7ca2ffa-e6db-44bc-99bd-27fe64cbd380", "3.29577066046423"),
        ("PP", "f863dfc3-1af5-4815-9798-15ce74225b7a", "3.29681341159016"),
        ("PS", "da5a226c-3799-42cf-9a34-fbb0b065cb2c", "3.51665341158856"),
        ("PVC", "b7975da6-c4bc-48fd-b3a8-68b421c4082a", "1.53644249098189"),
    ],
)
def test_other_plastic_incineration_whole_route_only(material, uuid, climate):
    dataset = source(uuid, name=f"{material} incineration", direction="Input", climate=climate)
    result = evaluate_candidate(
        {
            "candidate_id": "example",
            "material": material,
            "process_steps": ["소각"],
            "input_mass_kg": "1000",
        },
        [dataset],
    )
    assert result["calculation_status"] == "ESTIMATED_REVIEW_REQUIRED"
    assert Decimal(result["carbon_kgco2e"]) == Decimal(climate) * Decimal(1000)
    assert result["process_uuid"] == uuid
    assert result["reference_unit"] == "kg"
    assert result["transport_boundary"] == "EXCLUDES_EXTERNAL_COLLECTION_TRANSPORT"
    assert result["facility_specific"] is False
    assert result["comparable"] is False
    assert result["dispatch_approved"] is False

    # No inferred recycling, omitted incineration steps, or wrong resin factors.
    other = {
        "candidate_id": "x",
        "material": material,
        "process_steps": ["sorting", "washing"],
        "input_mass_kg": 1000,
    }
    assert evaluate_candidate(other, [dataset])["calculation_status"] == "UNMATCHED"
    other["process_steps"] = ["incineration"]
    other["material"] = "PET"
    assert evaluate_candidate(other, [dataset])["carbon_kgco2e"] is None


def test_incineration_does_not_allow_mixed_plastic_to_assume_one_resin():
    from resource_ai.calculators.lci.evaluate import INCINERATION_PROCESS_UUIDS

    assert len(set(INCINERATION_PROCESS_UUIDS.values())) == 6
    test_data = source(
        INCINERATION_PROCESS_UUIDS["hdpe"],
        name="HDPE",
        direction="Input",
        climate="3.29577066046423",
    )
    result = evaluate_candidate(
        {
            "candidate_id": "mixture",
            "material": "HDPE/LDPE",
            "process_steps": ["incineration"],
            "input_mass_kg": 1000,
        },
        [test_data],
    )
    assert result["calculation_status"] == "UNMATCHED"
    assert result["carbon_kgco2e"] is None


def test_incineration_transport_requires_evidence_for_non_pet_material():
    from resource_ai.calculators.lci.evaluate import INCINERATION_PROCESS_UUIDS

    dataset = source(
        INCINERATION_PROCESS_UUIDS["pp"], name="PP", direction="Input", climate="3.29681341159016"
    )
    cand = {
        "candidate_id": "pp",
        "material": "PP",
        "process_steps": ["incineration"],
        "input_mass_kg": 1000,
        "transport": {"emissions_kgco2e": "40"},
    }
    assert evaluate_candidate(cand, [dataset])["calculation_status"] == "REVIEW_REQUIRED"
    cand["transport"]["source"] = "user-confirmed-transport"
    result = evaluate_candidate(cand, [dataset])
    assert Decimal(result["carbon_kgco2e"]) == Decimal("3336.81341159016")
    assert result["transport_source"] == "user-confirmed-transport"


def test_new_incineration_sources_parse_when_locally_installed():
    folder = Path("data/local/lci")
    names = (
        "LCI-2023-186.zip",
        "LCI-2023-186 (1).zip",
        "LCI-2023-187.zip",
        "LCI-2023-189.zip",
        "LCI-2023-190.zip",
        "LCI-2023-191.zip",
    )
    if not any((folder / n).is_file() for n in names):
        pytest.skip("Licensed incineration ILCD archives are not installed in CI")
    from resource_ai.calculators.lci.evaluate import INCINERATION_PROCESS_UUIDS

    known = {d.process_uuid: d for d in load_local_datasets(folder)}
    expectations = {
        "hdpe": "3.29577066046423",
        "ldpe": "3.29577066046423",
        "pp": "3.29681341159016",
        "ps": "3.51665341158856",
        "pvc": "1.53644249098189",
    }
    for resin, expected in expectations.items():
        dataset = known.get(INCINERATION_PROCESS_UUIDS[resin])
        if dataset is None:
            continue  # Supported archives can be installed separately.
        assert dataset.reference_direction == "Input"
        assert dataset.reference_quantity == Decimal(1)
        assert dataset.reference_unit == "kg"
        assert dataset.climate_change_amount == Decimal(expected)
