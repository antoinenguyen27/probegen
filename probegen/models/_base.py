from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProbegenModel(BaseModel):
    """Strict base model for JSON contracts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
