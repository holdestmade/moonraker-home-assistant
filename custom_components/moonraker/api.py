"""moonraker Client."""

import logging

from moonraker_api import MoonrakerClient, MoonrakerListener

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 7125


def _coerce_port(port) -> int:
    """Return a sane int port (config-flow may store str/empty/None)."""
    if port is None or port == "":
        return DEFAULT_PORT
    try:
        return int(port)
    except (TypeError, ValueError):
        _LOGGER.debug("Invalid Moonraker port %r; falling back to %d", port, DEFAULT_PORT)
        return DEFAULT_PORT


class MoonrakerApiClient(MoonrakerListener):
    """Moonraker communication API."""

    def __init__(
        self, url, session, port: int = DEFAULT_PORT, api_key: str = None, tls: bool = False
    ):
        """Init."""
        self.running = False
        if api_key == "":
            api_key = None
        self.client = MoonrakerClient(
            listener=self,
            host=url,
            port=_coerce_port(port),
            session=session,
            api_key=api_key,
            ssl=tls,
        )

    async def start(self) -> None:
        """Start the websocket connection."""
        self.running = True
        return await self.client.connect()

    async def stop(self) -> None:
        """Stop the websocket connection."""
        self.running = False
        await self.client.disconnect()
