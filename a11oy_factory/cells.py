"""Admitted and ROADMAP decision cells. Fail closed on anything else.

N1–N8 are named category-capture theatres. We cite the leader of the job
and take the job, not the code. Compiler refuses admission until doctrine
names a cell LIVE. Lyte is the only admitted cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Honesty = Literal[
    "STRUCTURAL-ONLY",
    "ROADMAP",
    "CONJECTURE",
    "UNAVAILABLE",
]


@dataclass(frozen=True)
class Cell:
    id: str
    title: str
    organ: str
    honesty: Honesty
    admitted: bool
    bind: str
    note: str
    job: str = ""
    cite: str = ""
    szl: str = ""


LYTE = Cell(
    id="lyte",
    title="Lyte",
    organ="heart",
    honesty="STRUCTURAL-ONLY",
    admitted=True,
    bind="BIND_AS_A11OY_PACKAGE",
    job="design-partner cell",
    cite="Owner-admitted. Not a flagship.",
    szl="Schema-checked bind into a11oy. Formulas never grant authority.",
    note="The one admitted cell. Schema-checked bind into a11oy. Not a flagship.",
)


def _frontier(
    n: int,
    *,
    title: str,
    organ: str,
    job: str,
    cite: str,
    szl: str,
) -> Cell:
    return Cell(
        id=f"N{n}",
        title=title,
        organ=organ,
        honesty="ROADMAP",
        admitted=False,
        bind="BIND_AS_A11OY_PACKAGE",
        job=job,
        cite=cite,
        szl=szl,
        note=(
            f"{title}. Cite {cite}. SZL takes the job: {szl} "
            "Compiler refuses admission until doctrine names it LIVE."
        ),
    )


FRONTIERS: tuple[Cell, ...] = (
    _frontier(
        1,
        title="Serve",
        organ="brain",
        job="inference serving",
        cite=(
            "vLLM (production default, PagedAttention, continuous batching, OpenAI /v1); "
            "SGLang (RadixAttention prefix reuse, agentic/structured); "
            "Ollama (local DX wrapping llama.cpp)"
        ),
        szl="receipted fail-closed serving with schema outside the weights. Not a vLLM/SGLang/Ollama rehost.",
    ),
    _frontier(
        2,
        title="Graph",
        organ="nervous",
        job="agent orchestration",
        cite=(
            "LangGraph (stateful cyclic multi-agent, durable execution, "
            "checkpointing, human-in-the-loop interrupt())"
        ),
        szl="doctrine-bound graph with SENTRA on every edge. Not a LangGraph rehost.",
    ),
    _frontier(
        3,
        title="Guard",
        organ="immune",
        job="input/output safeguard",
        cite=(
            "Llama Guard (prompt and response classification, risk taxonomy; "
            "Llama-Guard-4 multimodal)"
        ),
        szl="SENTRA tripwires plus WILLAY conscience. Not a Llama Guard rehost.",
    ),
    _frontier(
        4,
        title="Mosaic",
        organ="circulatory",
        job="data mosaic",
        cite="MosaicML / Databricks Mosaic AI (train, customize, and deploy on own data, lakehouse)",
        szl="receipted mosaic with UNSIGNED-honest lineage. Not a Databricks rehost.",
    ),
    _frontier(
        5,
        title="Lattice",
        organ="immune",
        job="defense overlay",
        cite="immune-lattice COP (SENTRA/YAWAR). Hub vertical may be LIVE; this frontier bind is not.",
        szl="defense overlay on every cell. Hunt, isolate, deceive. Never strike people. Bind stays ROADMAP.",
    ),
    _frontier(
        6,
        title="Cover",
        organ="heart",
        job="P&C insurance core",
        cite="Guidewire (policy, billing, claims; 570+ insurers)",
        szl="allodial/counsel bind. Formulas never grant authority. Not a Guidewire rehost.",
    ),
    _frontier(
        7,
        title="Quant",
        organ="brain",
        job="algorithmic research and backtest",
        cite="QuantConnect LEAN (research, backtest, live trade on many venues)",
        szl="receipted backtest. Actuation SIMULATED. Not a broker. Not a LEAN rehost.",
    ),
    _frontier(
        8,
        title="Title",
        organ="skeleton",
        job="property records",
        cite="Zillow (residential listings and records). szl-real-estate is a LIVE Hub vertical; this frontier bind is not.",
        szl="receipted title/underwrite bind. Not a Zillow rehost. Bind stays ROADMAP.",
    ),
)

CELLS: dict[str, Cell] = {LYTE.id: LYTE, **{c.id: c for c in FRONTIERS}}
ADMITTED = frozenset(c.id for c in CELLS.values() if c.admitted)
