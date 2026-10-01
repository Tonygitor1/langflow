from lfx.components.agents_market._source import SourceNode
from lfx.custom.custom_component.component import Component


class AgentAppResearchDocumentsComponent(SourceNode, Component):
    """Users' own research files (Google Drive, OneDrive). Each user connects their
    account in the app; only they see their files, and every read is audited.
    """

    display_name = "Research Documents"
    description = "Document accounts users may connect (any number). Connect it to Agent App Query."
    name = "AgentAppResearchDocuments"
    category = "research.documents"
    cardinality = "many"
