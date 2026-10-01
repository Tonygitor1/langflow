from lfx.components.agents_market._source import SourceNode
from lfx.custom.custom_component.component import Component


class AgentAppPriceSourceComponent(SourceNode, Component):
    """Where an Agent App's prices come from. Each user reads exactly one of the
    sources offered here (ds.tradingadvisor, Yahoo Finance, sample data) and can
    switch in the app; screens show which one every figure came from.
    """

    display_name = "Price Source"
    description = "Price sources users may pick (exactly one each). Connect it to Agent App Query."
    name = "AgentAppPriceSource"
    category = "market.prices"
    cardinality = "one"
