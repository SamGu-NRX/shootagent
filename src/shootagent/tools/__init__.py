"""The agent's tools. Importing this package registers every tool in `base.REGISTRY`."""

from . import edit, perceive, shoot  # noqa: F401  (registration side effects)
from .base import REGISTRY, ToolContext, ToolResult, ToolSpec, run_tool

__all__ = ["REGISTRY", "ToolContext", "ToolResult", "ToolSpec", "run_tool"]
