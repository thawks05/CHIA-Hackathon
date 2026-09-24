"""Optional real LLM proposals through CHIA's Vertex API adapter.

No tool server, shell, file reader, or code-execution tool is exposed. The
response is parsed as JSON data and still must pass the trusted core validator.
"""
from __future__ import annotations

import json


SYSTEM = """Propose one Pivot memory scheduling candidate as a JSON object.
This is hardware design search, not model fine-tuning. You have no tools.
Return only JSON: {"params":{"lane_width":64|128|256|512,
"prefetch_depth":1|2|4|8|16|32,"layout":"vector_major"|"bit_interleaved"|"xor_swizzle",
"issue_policy":"fifo"|"row_hit_first"},
"rationale":"brief explanation"}. You may additionally propose a "program"
object with four address expressions named channel, bank, row, column.
The names permitted in address expressions are v, b, linear, channels, banks,
row_bursts, bursts_per_vector. linear = v * bursts_per_vector + b.
An optional priority expression may also use channel, bank, row, column,
hit, ordinal. Lower priority values issue first.
Use only integer arithmetic, integer floor division, modulo, and bitwise
operations; no calls, imports, attributes, containers, or mutable state.
The core requires physical addresses to be in bounds and injective for all
accessed bursts. An invalid program is rejected and consumes its budget slot.
Optimize model operations per cycle using only the supplied aggregate history.
Do not infer physical timing/area, edit the evaluator, or claim measured PPA.
"""


def parse_proposal(text: str, index: int) -> dict:
    if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
        raise ValueError("Agent output must be text of at most 64 KiB")
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or lines[-1].strip() != "```":
            raise ValueError("Agent output has an incomplete JSON fence")
        value = "\n".join(lines[1:-1])
    proposal = json.loads(value)
    if not isinstance(proposal, dict):
        raise ValueError("Agent output must be one JSON object")
    unknown = set(proposal) - {"params", "program", "rationale", "parent_id"}
    if unknown:
        raise ValueError("Unexpected proposal fields: " + ", ".join(sorted(unknown)))
    params = proposal.get("params")
    if not isinstance(params, dict) or set(params) != {"lane_width", "prefetch_depth", "layout", "issue_policy"}:
        raise ValueError("Proposal must specify lane_width, prefetch_depth, layout and issue_policy")
    if type(params["lane_width"]) is not int or params["lane_width"] not in (64, 128, 256, 512):
        raise ValueError("Invalid lane_width")
    if type(params["prefetch_depth"]) is not int or params["prefetch_depth"] not in (1, 2, 4, 8, 16, 32):
        raise ValueError("Invalid prefetch_depth")
    if params["layout"] not in ("vector_major", "bit_interleaved", "xor_swizzle"):
        raise ValueError("Invalid layout")
    if params["issue_policy"] not in ("fifo", "row_hit_first"):
        raise ValueError("Invalid issue_policy")
    if "program" in proposal:
        program = proposal["program"]
        address_fields = {"channel", "bank", "row", "column"}
        if (not isinstance(program, dict)
                or (set(program) & address_fields and not address_fields <= set(program))
                or set(program) - (address_fields | {"priority"})):
            raise ValueError("An address program needs all four coordinates; priority alone is permitted")
        if any(not isinstance(expr, str) or len(expr) > 512 for expr in program.values()):
            raise ValueError("Address expressions must be short strings")
    proposal["id"] = f"agent-{index:04d}"
    proposal["arm"] = "agent"
    return proposal


class VertexProposer:
    """Explicit opt-in API calls, with no access to the evaluator filesystem."""

    def __init__(self, model: str, project: str | None, location: str, timeout: int):
        from chia.models.vertex import VertexGeminiLLM

        self.llm = VertexGeminiLLM(
            model=model, project=project, location=location,
            system_message=SYSTEM, retries=1, timeout_seconds=timeout,
            max_tokens=2048, max_tool_iterations=1,
        )

    def propose(self, history: list[dict], index: int) -> tuple[dict, dict]:
        prompt = json.dumps({
            "proposal_number": index,
            "feedback": history[-16:],
            "instruction": "Return one candidate JSON object.",
        }, ensure_ascii=True)
        response = self.llm.prompt(prompt, tools=[])
        if not response.success:
            raise RuntimeError("CHIA provider returned an unsuccessful response")
        proposal = parse_proposal(response.result, index)
        metadata = {"provider": "VertexGeminiLLM", "actual_llm_call": True}
        return proposal, metadata
