"""Reference resolution — converts UnresolvedReference records into Edge rows."""

from .resolver import ReferenceResolver, create_resolver
from .synthesizer import synthesize_interface_dispatch

__all__ = ["ReferenceResolver", "create_resolver", "synthesize_interface_dispatch"]
