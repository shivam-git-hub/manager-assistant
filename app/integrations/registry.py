"""source string -> connector instance. Used by app.outbound (dispatching a
send) and by each channel's own webhook/poll entrypoint (dispatching an
ingest). Mirrors the pattern app/agent/registry.py already uses for tools."""
from typing import Dict, Optional
from app.integrations.base import ChannelConnector

_connectors: Dict[str, ChannelConnector] = {}


def register_connector(connector: ChannelConnector) -> None:
    _connectors[connector.source] = connector


def get_connector(source: str) -> Optional[ChannelConnector]:
    return _connectors.get(source)


def _register_defaults() -> None:
    from app.integrations.slack import SlackConnector
    from app.integrations.outlook import OutlookConnector

    register_connector(SlackConnector())
    register_connector(OutlookConnector())


_register_defaults()
