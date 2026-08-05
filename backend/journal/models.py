from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class JournalEntryDraft:
  symbol: str
  strategy_id: str | None
  result: str
  evidence_ids: list[str] = field(default_factory=list)
  narrative: str = ""
  tags: list[str] = field(default_factory=list)
