"""Files and media: assets an environment carries, delivered to agents under its visibility rules.

See ``fg_env.guide('assets')``. The contract declares files beside it (:mod:`.spec`); loading reads and hashes
them (:mod:`.catalog`) into the run's :class:`AssetStore`; agents receive :class:`Attachment` objects through
views, records, briefs, tool results and inspect (:mod:`.delivery`), which LLM participants send as multimodal
parts (:mod:`.multimodal`); agents submit files through `file` parameters (:mod:`.intake`); hosts describe
files (:mod:`.describe`). Bytes are found by content hash (:mod:`.blobs`).
"""
from .blobs import BlobMissing, provide
from .delivery import Attachment
from .kinds import KINDS
from .spec import AssetSpec
from .store import Asset, AssetStore

__all__ = ["AssetSpec", "Asset", "AssetStore", "Attachment", "BlobMissing", "KINDS", "provide"]
