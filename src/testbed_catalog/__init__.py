"""The tool catalog: honest metadata about what is integrated and what is not.

A catalog record is a *claim about integration status*, so the rules here are
deliberately strict: `certified` requires a pinned revision, a reviewed licence
and a smoke fixture; `stub` and `external` state plainly that the integration
does not exist in this repository.
"""

from testbed_catalog.model import CATALOG_CAPABILITIES, CatalogRecord, Lane
from testbed_catalog.registry import Catalog, CatalogError, load_catalog

__all__ = [
    "CATALOG_CAPABILITIES",
    "Catalog",
    "CatalogError",
    "CatalogRecord",
    "Lane",
    "load_catalog",
]
