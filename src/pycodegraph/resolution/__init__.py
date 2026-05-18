"""Reference resolution — converts UnresolvedReference records into Edge rows."""

from .resolver import ReferenceResolver, create_resolver

__all__ = ["ReferenceResolver", "create_resolver"]
