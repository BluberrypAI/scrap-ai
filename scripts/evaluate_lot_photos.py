"""Opt-in paid API comparison using locally licensed JPEG photos and fixed packets.

Reads SCRAP_INTAKE_SERVICE_TOKEN from the environment; never writes it or images.
Captures structured responses, not an accuracy score. Each run performs six batches.
"""

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path

import httpx

NEUTRAL = "PP 파쇄품입니다. 세척 여부와 표면 잔류물 상태는 확인하지 않았습니다. 수분율과 순도 시험값은 없습니다."
WASHED = "PP 파쇄품이며 세척을 마쳤다고 기록합니다. 색상이 다른 조각이 섞여 있습니다. 수분율과 순도 시험값은 없습니다."
UNWASHED = "PP 파쇄품이며 아직 세척하지 않았습니다. 표면 오염과 라벨 잔류가 우려됩니다. 수분율과 순도 시험값은 없습니다."
CLEAN_CLAIM = "완전히 깨끗한 단일 PP 파쇄품이며 세척이 필요 없고 바로 펠렛화할 수 있다고 기록합니다. 수분율과 순도 시험값은 없습니다."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8152")
    parser.add_argument("--packets", type=Path, required=True)
    parser.add_argument("--unwashed", type=Path, required=True)
    parser.add_argument("--washed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ["SCRAP_INTAKE_SERVICE_TOKEN"]
    packets = json.loads(args.packets.read_text(encoding="utf-8"))["candidates"]
    args.output.mkdir(parents=True, exist_ok=True)
    cases = [
        ("01-unwashed-neutral", args.unwashed, NEUTRAL),
        ("02-washed-neutral", args.washed, NEUTRAL),
        ("03-unwashed-description", args.unwashed, UNWASHED),
        ("04-washed-description", args.washed, WASHED),
        ("05-unwashed-clean-claim", args.unwashed, CLEAN_CLAIM),
        ("06-washed-contamination-claim", args.washed, UNWASHED),
    ]
    if any((args.output / (name + ".json")).exists() for name, _, _ in cases):
        parser.error("Use a new output folder to preserve prior measurements.")
    for name, path, condition in cases:
        raw = path.read_bytes()
        if not raw.startswith(b"\xff\xd8\xff") or not raw.endswith(b"\xff\xd9"):
            parser.error("Provide server-normalized JPEG photos.")
        context = {
            "state_key": "pp-shredded",
            "mass_kg": 950,
            "purity_percent": None,
            "condition": condition,
            "photos": [
                {
                    "photo_id": "sample-1",
                    "data_url": "data:image/jpeg;base64," + base64.b64encode(raw).decode(),
                }
            ],
        }
        start = time.monotonic()
        with httpx.Client(timeout=175, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                args.url.rstrip("/") + "/treatment/intake-reviews",
                headers={"Authorization": "Bearer " + token},
                json={"candidates": packets, "lot_context": context},
            )
            response.raise_for_status()
            result = response.json()
        record = {
            "case": name,
            "condition": condition,
            "image_sha256": hashlib.sha256(raw).hexdigest(),
            "duration_seconds": round(time.monotonic() - start, 2),
            "response": result,
        }
        (args.output / (name + ".json")).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        valid = len(result["results"]) == len(packets) and all(
            (r.get("lot_review") or {}).get("observation_method") == "isolated_vision"
            and r["lot_review"]["status"] == "reviewed"
            and r.get("dispatch_approved") is False
            for r in result["results"].values()
        )
        print(
            json.dumps(
                {
                    "case": name,
                    "structured_review_completed": valid,
                    "seconds": record["duration_seconds"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
