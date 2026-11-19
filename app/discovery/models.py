"""The common internal scheme format for discovery (integration §2-§3). Pure data; no AI, no network."""

from dataclasses import dataclass, field
from datetime import datetime

SOURCE_MYSCHEME = "myscheme"
SOURCE_DEMO = "demo"


@dataclass(frozen=True)
class NormalizedScheme:
    """One scheme as discovery shows it. Missing values stay None or empty and are shown as unavailable."""

    key: str  # stable id used in URLs: the YAML id for demo and matched schemes, else the provider's id
    name: str
    source: str  # SOURCE_MYSCHEME or SOURCE_DEMO. Never shown as "live" unless it came from the live API.
    department: str | None = None
    description: str | None = None
    benefits: str | None = None
    eligibility_summary: str | None = None
    documents: list[str] = field(default_factory=list)
    official_url: str | None = None
    retrieved_at: datetime | None = None  # None for demo data: it was never retrieved
    internal_id: str | None = None  # the matching scheme in the rulebook, if any
    aliases: list[str] = field(default_factory=list)

    @property
    def live(self) -> bool:
        return self.source == SOURCE_MYSCHEME and self.retrieved_at is not None

    def source_label(self, lang: str = "en") -> str:
        if self.live:
            return "Source: myScheme (live)" if lang == "en" else "स्रोत: myScheme (लाइव)"
        return "Demo data: not live from myScheme" if lang == "en" else "डेमो डेटा: myScheme से लाइव नहीं"
