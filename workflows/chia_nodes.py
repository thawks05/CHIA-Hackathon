"""Real CHIA nodes. Imported only when --backend chia is selected."""
from chia.base.ChiaFunction import ChiaFunction

from workflows import stages


@ChiaFunction(num_cpus=1, resources={"pivot_tier0": 1}, max_retries=0)
def tier0(config: dict) -> dict:
    return stages.tier0(config)


@ChiaFunction(num_cpus=1, resources={"pivot_tier1": 1}, max_retries=0)
def tier1(config: dict, candidate: dict, smoke: dict) -> dict:
    # Passing T0's ObjectRef resolves to its envelope and preserves a graph edge.
    return stages.tier1(config, candidate, smoke.get("result", smoke))


@ChiaFunction(num_cpus=1, resources={"pivot_synth": 1}, max_retries=0)
def tier2(config: dict) -> dict:
    return stages.tier2(config)
