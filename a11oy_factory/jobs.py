"""Web-cited jobs versus SZL organs. Search our catalog, not a live scrape.

We searched the leaders, encoded the job, and refuse to rehost the code.
Signing stays UNSIGNED-honest (tamper-evident, not Sigstore/Cosign).
Energy stays UNAVAILABLE. Λ stays Conjecture 1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .cells import FRONTIERS, LYTE, Cell


@dataclass(frozen=True)
class Job:
    id: str
    title: str
    leader: str
    url: str
    organ: str
    cell: str
    honesty: str
    admitted: bool
    take: str
    refuse: str


JOBS: tuple[Job, ...] = (
    Job(
        id="lyte",
        title="Lyte design-partner cell",
        leader="SZL owner order",
        url="https://github.com/szl-holdings/a11oy-factory",
        organ="heart",
        cell="lyte",
        honesty="STRUCTURAL-ONLY",
        admitted=True,
        take="BIND_AS_A11OY_PACKAGE. Schema-checked bind into a11oy.",
        refuse="Not a second flagship. Formulas never grant authority.",
    ),
    Job(
        id="vllm",
        title="Production LLM serving",
        leader="vLLM",
        url="https://github.com/vllm-project/vllm",
        organ="brain",
        cell="N1",
        honesty="ROADMAP",
        admitted=False,
        take="Receipted fail-closed serving. Schema outside the weights. OpenAI-shaped /v1.",
        refuse="Do not rehost vLLM, PagedAttention, or their kernels.",
    ),
    Job(
        id="sglang",
        title="Agentic / structured serving",
        leader="SGLang",
        url="https://github.com/sgl-project/sglang",
        organ="brain",
        cell="N1",
        honesty="ROADMAP",
        admitted=False,
        take="Prefix-reuse and structured output as a serving job, under receipts.",
        refuse="Do not rehost RadixAttention or SGLang runtime.",
    ),
    Job(
        id="ollama",
        title="Local developer serving",
        leader="Ollama",
        url="https://github.com/ollama/ollama",
        organ="brain",
        cell="N1",
        honesty="ROADMAP",
        admitted=False,
        take="Honest local DX path wrapping llama.cpp. CPU-honest until GPU is MEASURED.",
        refuse="Do not rehost Ollama. Do not claim GPU serve LIVE.",
    ),
    Job(
        id="langgraph",
        title="Stateful agent graph",
        leader="LangGraph",
        url="https://www.langchain.com/langgraph",
        organ="nervous",
        cell="N2",
        honesty="ROADMAP",
        admitted=False,
        take="Durable graph, checkpoint, human-in-the-loop. SENTRA on every edge.",
        refuse="Do not rehost LangGraph StateGraph or interrupt().",
    ),
    Job(
        id="llamaguard",
        title="Prompt and response safeguard",
        leader="Llama Guard",
        url="https://ai.meta.com/research/publications/llama-guard-llm-based-input-output-safeguard-for-human-ai-conversations/",
        organ="immune",
        cell="N3",
        honesty="ROADMAP",
        admitted=False,
        take="Classify prompt and response. Fail closed on taxonomy hits.",
        refuse="Do not rehost Llama Guard weights or Purple Llama.",
    ),
    Job(
        id="mosaic",
        title="Own-data mosaic",
        leader="MosaicML / Databricks Mosaic AI",
        url="https://www.databricks.com/blog/databricks-mosaicml",
        organ="circulatory",
        cell="N4",
        honesty="ROADMAP",
        admitted=False,
        take="Receipted mosaic with UNSIGNED-honest lineage on own data.",
        refuse="Do not rehost MosaicML, Mosaic AI, or the lakehouse.",
    ),
    Job(
        id="lattice",
        title="Defense overlay",
        leader="immune-lattice",
        url="https://github.com/szl-holdings/immune-lattice",
        organ="immune",
        cell="N5",
        honesty="ROADMAP",
        admitted=False,
        take="SENTRA/YAWAR overlay on every cell. Defense only.",
        refuse="Hub vertical may be LIVE. This frontier bind is not. Never strike people.",
    ),
    Job(
        id="guidewire",
        title="P&C insurance core",
        leader="Guidewire",
        url="https://www.guidewire.com/",
        organ="heart",
        cell="N6",
        honesty="ROADMAP",
        admitted=False,
        take="Allodial/counsel bind for policy, billing, claims jobs.",
        refuse="Do not rehost Guidewire InsuranceSuite. Formulas never grant authority.",
    ),
    Job(
        id="quantconnect",
        title="Algorithmic research and backtest",
        leader="QuantConnect LEAN",
        url="https://www.quantconnect.com/",
        organ="brain",
        cell="N7",
        honesty="ROADMAP",
        admitted=False,
        take="Receipted backtest. Actuation SIMULATED.",
        refuse="Not a broker. Do not rehost LEAN. A price is not a fill.",
    ),
    Job(
        id="zillow",
        title="Property records",
        leader="Zillow",
        url="https://www.zillow.com/",
        organ="skeleton",
        cell="N8",
        honesty="ROADMAP",
        admitted=False,
        take="Receipted title/underwrite bind on public records.",
        refuse="Do not rehost Zillow. szl-real-estate Hub vertical may be LIVE; this bind is not.",
    ),
    Job(
        id="sigstore",
        title="Keyless artifact signing",
        leader="Sigstore / Cosign",
        url="https://www.sigstore.dev/",
        organ="skeleton",
        cell="",
        honesty="STRUCTURAL-ONLY",
        admitted=False,
        take="UNSIGNED-honest SHA-256 is tamper-EVIDENT.",
        refuse="Not a signature. Not Cosign. Not Fulcio. Signing stays STRUCTURAL-ONLY.",
    ),
    Job(
        id="energy",
        title="Grid carbon / joule accounting",
        leader="Electricity Maps",
        url="https://www.electricitymaps.com/",
        organ="circulatory",
        cell="",
        honesty="UNAVAILABLE",
        admitted=False,
        take="Energy remains UNAVAILABLE until NVML is MEASURED.",
        refuse="Do not fabricate joules. Do not clone Electricity Maps.",
    ),
    Job(
        id="unsloth",
        title="Receipted QLoRA",
        leader="Unsloth",
        url="https://github.com/unslothai/unsloth",
        organ="brain",
        cell="N1",
        honesty="ROADMAP",
        admitted=False,
        take="Fine-tunes only against LIVE weights with a receipt.",
        refuse="No unreceipted QLoRA. Not an Unsloth rehost.",
    ),
)


def _blob(job: Job) -> str:
    return " ".join(
        [
            job.id,
            job.title,
            job.leader,
            job.organ,
            job.cell,
            job.honesty,
            job.take,
            job.refuse,
        ]
    ).lower()


def _cell_blob(cell: Cell) -> str:
    return " ".join(
        [cell.id, cell.title, cell.job, cell.cite, cell.szl, cell.note, cell.organ]
    ).lower()


def search_jobs(q: str) -> dict:
    """Local catalog search. Empty query returns the full table. Unknown query is empty hits, not an error."""
    needle = (q or "").strip().lower()
    jobs = [asdict(j) for j in JOBS if not needle or needle in _blob(j)]
    cells = [c.__dict__ for c in (LYTE, *FRONTIERS) if not needle or needle in _cell_blob(c)]
    return {
        "query": q or "",
        "jobs": jobs,
        "cells": cells,
        "lambda_status": "Conjecture 1",
        "energy": None,
        "signer": "UNSIGNED-honest",
        "note": "Catalog of cited jobs. Not a live web crawl. We take the job, not the code.",
    }
