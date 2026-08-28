"""API-only request models.

Response bodies reuse the domain models in ``schemas.runs`` directly (``Run``,
``CompetitorRun``, ``StageResult``) -- they're already clean enough for the
HTTP layer, so no parallel/duplicate response-schema layer is introduced
here. Only the competitor-submission request body is API-specific.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, model_validator


class CompetitorSubmitRequest(BaseModel):
    """Body for ``POST /runs/{run_id}/competitors``.

    Exactly one input source is required (``data`` inline JSON or
    ``data_path`` file paths) -- ``docs/architecture.md`` section 6.2: "At
    least one supported input source must be provided. Do not require both."
    If both are given, ``data`` takes precedence.

    Within the chosen input source, at least one of ``prepaid``/``postpaid``
    must be present -- a competitor may legitimately be prepaid-only or
    postpaid-only. A category that is not supplied is treated as an empty
    envelope for that category (see ``routes.submit_competitor``).
    """

    competitor: str
    data: Optional[dict] = None
    data_path: Optional[dict] = None

    @model_validator(mode="after")
    def _require_one_input_source(self) -> "CompetitorSubmitRequest":
        has_data = bool(self.data)
        has_path = bool(self.data_path)

        if not has_data and not has_path:
            raise ValueError(
                "Either 'data' (inline JSON) or 'data_path' (file paths) must be provided."
            )

        if has_path:
            if not any(self.data_path.get(k) for k in ("prepaid", "postpaid")):
                raise ValueError(
                    "'data_path' must include at least one of 'prepaid'/'postpaid'."
                )

        if has_data:
            if not any(k in self.data for k in ("prepaid", "postpaid")):
                raise ValueError(
                    "'data' must include at least one of 'prepaid'/'postpaid'."
                )

        return self
