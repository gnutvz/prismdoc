"""HTTP API package for the prismdoc microservice."""

from prismdoc.api.app import app, get_runtime

__all__ = ["app", "get_runtime"]
