"""Project the next reachable transition from the existing KG process paths."""

from resource_ai.api.schemas.kg_next_step import NextStepRequest, NextStepResponse
from resource_ai.knowledge_graph import graph_store as store
from resource_ai.knowledge_graph.facility_codes import load_codebook
from resource_ai.knowledge_graph.facility_search import straight_line_km
from resource_ai.knowledge_graph.planning import enumerate_paths, load_reference, normalize_form
from resource_ai.knowledge_graph.planning_reference import reference
from resource_ai.knowledge_graph.query import resolve_material

# Curated MVP entry cases. A code alone cannot resolve a resin or a lot's state.
ENTRY_FORMS = {
    "51-03-01": {
        "pp": {"scrap", "rigid", "film", "shredded", "flake", "pellet"},
        "pe": {"scrap", "rigid", "film", "shredded", "flake", "pellet"},
        "pet": {"bottle", "bale", "flake"},
    },
    "51-03-07": {
        "pp": {"scrap", "rigid", "film"},
        "pe": {"scrap", "rigid", "film"},
        "pet": {"bottle", "bale"},
    },
    "51-29-03": {"aluminium": {"can"}},
}

# Public map points tied to the exact facility/address. Used for approximate
# distances only. This does not modify or extend the audited Neo4j graph.
MAP_POINTS = {
    "samyang-sihwa": (37.3342268, 126.7039098, "https://juso.app/b/iq03tvk7uma42oyr"),
    "jungang-gongju": (36.3190188, 127.0303035, "https://www.jungangenb.com/about/location"),
    "bgf-yesan": (
        36.7990626,
        126.695566,
        "https://findby.co.kr/details/32401-448103353161-st-686c1167f99e2ce9c5b60275",
    ),
    "novelis-yeongju": (
        36.793474,
        128.623877,
        "https://novelis.com/ko/locations/yeongju-south-korea/",
    ),
}


def next_step(request: NextStepRequest, *, config=None, demo=False) -> NextStepResponse:
    code = request.waste_code
    row = load_codebook()["records"].get(code)
    base = {
        "backend": "reference_demo" if demo else "neo4j",
        "waste_code": code,
        "code_label": row["label_excerpt"] if row else None,
    }
    materials = ENTRY_FORMS.get(code)
    if not row or not materials:
        return NextStepResponse(
            **base, status="unsupported", message="이 분류번호의 검수된 공정 경로가 아직 없습니다."
        )
    material = resolve_material(request.material)
    form = normalize_form(request.input_form)
    if not request.material or not form:
        return NextStepResponse(
            **base, status="needs_input", message="문진의 재질과 현재 형태를 확인해 주세요."
        )
    if form not in materials.get(material, set()) or material not in row["query_materials"]:
        return NextStepResponse(
            **base,
            status="unsupported",
            message="이 분류번호·재질·형태 조합의 공정 근거가 없습니다.",
        )

    def project(value):
        result, _ = enumerate_paths(value, material, form, None, 8)
        if result.budget_exhausted or result.depth_limited:
            raise store.GraphStoreError("next-step-limit", "공정 탐색 한도를 초과했습니다.")
        return project_next_step(
            value, result.paths, request.completed_transitions, base, request.origin
        )

    if demo:
        return project(reference())
    config = config or store.settings()

    def lookup(tx):
        generation, manifest = store._head(tx, config)
        value, _ = load_reference(tx, config, generation, manifest)
        return project(value)

    try:
        return store._read(config, lookup)
    except (KeyError, TypeError, ValueError):
        raise store.integrity() from None


def project_next_step(value, paths, completed, base, origin):
    states = {state.key: state for state in value.states}
    facilities = {facility.key: facility for facility in value.facilities}
    sources = {source.key: source for source in value.sources}

    def state_label(key):
        return {"key": key, "label": states[key].label}

    def links(keys):
        return [
            {
                "publisher": sources[key].publisher,
                "url": sources[key].url,
                "locator": sources[key].locator,
            }
            for key in sorted(set(keys))
        ]

    options = {}
    can_finish = False
    current = set()
    index = len(completed)
    for path in paths:
        if [transition.key for transition, _ in path[:index]] != completed:
            continue
        current.add(path[index - 1][0].output_state if index else path[0][0].input_state)
        if len(path) == index:
            can_finish = True
            continue
        transition, capability = path[index]
        option = options.setdefault(
            transition.key,
            {
                "transition_key": transition.key,
                "label": transition.label,
                "input_state": state_label(transition.input_state),
                "output_state": state_label(transition.output_state),
                "operation_steps": transition.operation_steps,
                "checks": list(dict.fromkeys(transition.conditions + transition.risk_checks)),
                "sources": links(transition.source_ids),
                "facilities": {},
            },
        )
        if capability and capability.key not in option["facilities"]:
            facility = facilities[capability.facility]
            location, location_url = facility_location(facility)
            option["facilities"][capability.key] = {
                "capability_key": capability.key,
                "facility_key": facility.key,
                "label": facility.label,
                "region": facility.region,
                "evidence_basis": capability.basis,
                "checks": capability.conditions,
                "sources": links(capability.source_ids),
                "location": location,
                "location_source_url": location_url,
                "straight_line_distance_km": straight_line_km(
                    (origin.latitude, origin.longitude) if origin else None,
                    tuple(location.values()) if location else None,
                ),
            }
    if not current and completed:
        raise store.GraphStoreError(
            "invalid-progress", "완료한 공정이 현재 KG 경로와 일치하지 않습니다."
        )
    items = [
        {**option, "facilities": list(option["facilities"].values())} for option in options.values()
    ]
    rank_facilities(items)
    if items:
        status, message = "candidates", "현재 상태에서 바로 이어지는 공정의 조사 후보입니다."
    elif can_finish:
        status, message = "end_of_route", "현재 그래프에 등록된 마지막 산출 상태입니다."
    else:
        status, message = "no_route", "입력 상태에서 이어지는 검수된 경로를 찾지 못했습니다."
    return NextStepResponse(
        **base,
        status=status,
        current_state=state_label(next(iter(current))) if len(current) == 1 else None,
        can_finish=can_finish,
        options=items,
        message=message,
    )


def facility_location(facility):
    if facility.latitude is not None and facility.longitude is not None:
        return {"latitude": facility.latitude, "longitude": facility.longitude}, None
    if facility.key in MAP_POINTS:
        lat, lon, url = MAP_POINTS[facility.key]
        return {"latitude": lat, "longitude": lon}, url
    return None, None


def rank_facilities(options):
    """Rank distinct physical sites across all immediate transitions."""
    distances = {
        facility["facility_key"]: facility["straight_line_distance_km"]
        for option in options
        for facility in option["facilities"]
        if facility["straight_line_distance_km"] is not None
    }
    keys = sorted(distances, key=lambda key: (distances[key], key))[:5]
    ranks = {key: i for i, key in enumerate(keys, 1)}
    for option in options:
        for facility in option["facilities"]:
            facility["distance_rank"] = ranks.get(facility["facility_key"])
        option["facilities"].sort(
            key=lambda facility: (facility["distance_rank"] or 6, facility["capability_key"])
        )
