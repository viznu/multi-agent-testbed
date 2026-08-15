"""Catalog record schema."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from testbed_contracts.enums import Maturity, Runtime

#: Suggested capability tags. This is an *open* vocabulary: a new kind of tool is
#: a new tag plus an adapter bound to an existing port, never a new kernel
#: contract. Unknown tags are allowed and reported, not rejected.
CATALOG_CAPABILITIES: tuple[str, ...] = (
    "runner", "agent", "world", "topology", "communication", "memory", "payoff", "fault",
    "benchmark", "verifier", "scorer", "aggregator", "judge", "preference_protocol",
    "scanner", "discovery_campaign", "control_campaign", "interpretability_probe",
    "optimizer", "telemetry_sink", "sandbox", "assurance_profile", "report_exporter",
)


class Lane(BaseModel):
    """One of the fifteen market lanes the plan requires coverage for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1, le=15)
    title: str


LANES: tuple[Lane, ...] = (
    Lane(number=1, title="Frameworks and harnesses"),
    Lane(number=2, title="Product eval / LLMOps"),
    Lane(number=3, title="Static knowledge and reasoning"),
    Lane(number=4, title="Agentic capability"),
    Lane(number=5, title="Agent safety and dangerous capability"),
    Lane(number=6, title="Long-horizon and economic agency"),
    Lane(number=7, title="Automated auditing and discovery"),
    Lane(number=8, title="Transcript analysis and eval science"),
    Lane(number=9, title="Jailbreak and adversarial robustness"),
    Lane(number=10, title="Preference and arena evaluation"),
    Lane(number=11, title="Interpretability-based evaluation"),
    Lane(number=12, title="AI control"),
    Lane(number=13, title="Multi-agent environments and safety"),
    Lane(number=14, title="Multi-agent orchestration frameworks"),
    Lane(number=15, title="Governance and assurance"),
)

LANES_BY_NUMBER = {lane.number: lane for lane in LANES}


class CatalogRecord(BaseModel):
    """One tool, benchmark, protocol or profile.

    `record_id` is publisher-qualified (`publisher/name`) because several
    projects in this space share a name; a bare name is ambiguous enough to
    point an experiment at the wrong repository.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    title: str
    lane: int = Field(ge=1, le=15)
    capabilities: tuple[str, ...] = ()
    runtime: Runtime = Runtime.IN_PROCESS
    maturity: Maturity = Maturity.STUB
    source_url: str | None = None
    revision: str | None = None
    license: str | None = None
    data_terms: str | None = None
    image_digest: str | None = None
    expected_cost_usd: float | None = None
    contamination_notes: str = ""
    supported_drivers: tuple[str, ...] = ()
    entry_point: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _publisher_qualified(self) -> CatalogRecord:
        if "/" not in self.record_id:
            raise ValueError(
                f"record_id {self.record_id!r} must be publisher-qualified, "
                "e.g. 'ukaisi/inspect-ai'"
            )
        return self

    @property
    def is_runnable_here(self) -> bool:
        """True only when this repository can actually execute the record."""
        return self.maturity in (Maturity.CERTIFIED, Maturity.EXPERIMENTAL)

    def certification_gaps(self) -> list[str]:
        """What this record would still need to become `certified`.

        The gate list mirrors the plan: pinned revision, licence review, source
        URL, digest where a container is involved, and a declared entry point.
        """
        gaps: list[str] = []
        if not self.source_url:
            gaps.append("source_url")
        if not self.revision or self.revision in ("main", "master", "latest"):
            gaps.append("pinned revision (a moving ref is not a pin)")
        if not self.license:
            gaps.append("license review")
        if self.runtime is Runtime.OCI and not self.image_digest:
            gaps.append("image digest")
        if not self.entry_point:
            gaps.append("entry point")
        if not self.capabilities:
            gaps.append("capability tags")
        return gaps
