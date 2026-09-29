"""Reproduce one competitor-plan classification through OpenRouter.

Run from the project root. The OpenRouter key is read from the process
environment first, then from the project's .env file:

    ./.venv/bin/python test/openrouter_plan_classification.py

The default input is the first normalized plan from the bundled Ooredoo files.
Pass --prepaid, --postpaid, and --plan-index to reproduce a specific run input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from market_pulse.llm.plan_classifier import extract_json_object, get_classification_chain
from market_pulse.schemas.competitor import PlanEnrichment
from market_pulse.services.competitor_normalization_service import normalize_all_plans


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OOREDOO_DIR = PROJECT_ROOT / "data" / "competitors" / "ooredoo"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepaid",
        type=Path,
        default=OOREDOO_DIR / "prepaid_ooredoo_derived_features_updated.txt",
    )
    parser.add_argument(
        "--postpaid",
        type=Path,
        default=OOREDOO_DIR / "postpaid_ooredoo_derived_features_updated.txt",
    )
    parser.add_argument("--plan-index", type=int, default=0)
    parser.add_argument("--model", default="anthropic/claude-haiku-4.5")
    args = parser.parse_args()

    env_file = PROJECT_ROOT / ".env"
    file_values = dotenv_values(env_file) if env_file.is_file() else {}
    api_key = os.environ.get("OPENROUTER_API_KEY") or file_values.get("OPENROUTER_API_KEY")
    if not api_key:
        if os.environ.get("OPENAI_API_KEY") or file_values.get("OPENAI_API_KEY"):
            print(
                "OPENAI_API_KEY is present, but this test calls OpenRouter. "
                "Put an OpenRouter-issued key in OPENROUTER_API_KEY; the script "
                "will not send your OpenAI key to OpenRouter.",
                file=sys.stderr,
            )
        else:
            print(
                "OPENROUTER_API_KEY is missing from the process environment "
                f"and {env_file}.",
                file=sys.stderr,
            )
        return 2

    try:
        prepaid_raw = json.loads(args.prepaid.read_text(encoding="utf-8"))
        postpaid_raw = json.loads(args.postpaid.read_text(encoding="utf-8"))
        plans = normalize_all_plans(prepaid_raw, postpaid_raw)
    except (OSError, json.JSONDecodeError, IndexError, TypeError) as exc:
        print(f"Could not load or normalize plan files: {exc}", file=sys.stderr)
        return 2

    if not 0 <= args.plan_index < len(plans):
        print(
            f"--plan-index must be between 0 and {len(plans) - 1}.",
            file=sys.stderr,
        )
        return 2

    plan = plans[args.plan_index]
    # This is exactly the input shape and serialization used by enrich_one_plan.
    request = {"plan_json": json.dumps(plan, ensure_ascii=False)}

    llm = ChatOpenAI(
        model=args.model,
        temperature=0,
        max_retries=2,
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
        # OpenRouter must route to a provider that accepts response_format.
        extra_body={"provider": {"require_parameters": True}},
    )
    # Use the production prompt and strict JSON-schema request, while retaining
    # the raw response for diagnosis before applying the same extraction.
    chain = get_classification_chain(llm)

    print(f"Model: {args.model}")
    print(f"Plan: {plan.get('plan_name')} (index {args.plan_index} of {len(plans)})")
    print(f"Input plan JSON: {request['plan_json']}")

    try:
        raw = chain.invoke(request)
    except Exception as exc:
        print(f"Request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(raw, AIMessage) or not isinstance(raw.content, str):
        print("PARSE FAILED: Expected a text AIMessage", file=sys.stderr)
        return 1
    print("Raw response content:")
    print(raw.content)

    try:
        parsed = PlanEnrichment.model_validate(extract_json_object(raw.content))
    except Exception as exc:
        print(f"PARSE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PARSE SUCCEEDED: PlanEnrichment")
    print(parsed.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
