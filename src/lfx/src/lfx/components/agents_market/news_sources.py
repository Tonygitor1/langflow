from lfx.components.agents_market._source import SourceNode
from lfx.custom.custom_component.component import Component


class AgentAppNewsSourcesComponent(SourceNode, Component):
    """News feeds an Agent App may read. Users pick any number; the platform
    merges them newest first, drops duplicates and names the publisher of each item.
    """

    display_name = "News Sources"
    description = "News sources users may pick (any number, merged). Connect it to Agent App Query."
    name = "AgentAppNewsSources"
    category = "news"
    cardinality = "many"
