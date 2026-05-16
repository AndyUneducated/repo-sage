"""GraphRAG: Leiden community detection + LLM-generated community summaries.

Communities are formed on the *call topology* of the symbol graph, not the
file-system layout, so logical modules emerge even when files are scattered.
"""

from reposage.indexer.graphrag.community import Community, CommunityDetector
from reposage.indexer.graphrag.summarizer import CommunitySummarizer

__all__ = ["Community", "CommunityDetector", "CommunitySummarizer"]
