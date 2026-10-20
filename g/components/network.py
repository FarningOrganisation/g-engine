from __future__ import annotations

from typing import TYPE_CHECKING

from ..inspector import inspector_field
from .base import Component

if TYPE_CHECKING:
    from ..network import ClientInfo


class NetworkedGameObject(Component):
    """Configure network synchronization and ownership for a game object.

    Attributes:
        synchronization: Automatic, transform-only, or custom synchronization.
        sync_scale: Whether runtime snapshots include scale.
        remote_smoothing: Interpolation or snapping for remote transforms.
        owner_client_number: Development player slot; zero is server-owned.
        owner_disconnect: Whether to keep or despawn when its owner disconnects.
        network_id: Runtime network identity, if registered.
        owner_id: Network identity of the owning client, if assigned.
    """
    synchronization: str = inspector_field(
        "automatic",
        choices=("automatic", "transform_only", "custom"),
        tooltip="Choose how this object's network-safe state is synchronized.",
    )
    sync_scale: bool = inspector_field(
        False,
        tooltip="Include scale in runtime snapshots.",
    )
    remote_smoothing: str = inspector_field(
        "interpolate",
        choices=("interpolate", "snap"),
        tooltip="How remote authoritative transforms are presented.",
    )
    owner_client_number: int = inspector_field(
        0,
        minimum=0,
        tooltip="Development player slot; zero is server-owned.",
        group="Ownership",
        group_expanded=False,
    )
    owner_disconnect: str = inspector_field(
        "keep",
        choices=("keep", "despawn"),
        tooltip="What the server does when this object's owner disconnects.",
        group="Ownership",
        group_expanded=False,
    )

    def __init__(
        self,
        *,
        synchronization: str = "automatic",
        sync_scale: bool = False,
        remote_smoothing: str = "interpolate",
        owner_client_number: int = 0,
        owner_disconnect: str = "keep",
        enabled: bool = True,
    ) -> None:
        """Configure synchronization policy and client ownership.

        Raises:
            ValueError: A mode, smoothing option, or disconnect policy is
                unknown, or the owner number is negative.
        """
        super().__init__(enabled=enabled)
        if synchronization not in {"automatic", "transform_only", "custom"}:
            raise ValueError("unknown network synchronization mode")
        if remote_smoothing not in {"interpolate", "snap"}:
            raise ValueError("unknown network smoothing mode")
        if owner_client_number < 0:
            raise ValueError("owner client number must not be negative")
        if owner_disconnect not in {"keep", "despawn"}:
            raise ValueError("unknown owner disconnect policy")
        self.synchronization = synchronization
        self.sync_scale = sync_scale
        self.remote_smoothing = remote_smoothing
        self.owner_client_number = owner_client_number
        self.owner_disconnect = owner_disconnect
        self.network_id: str | None = None
        self.owner_id: str | None = None

    @property
    def is_local_owner(self) -> bool:
        """Whether the local network client owns this object."""
        session = self._session
        local = None if session is None else session.local_client
        return local is not None and self.owner_id == local.id

    @property
    def owner_client(self) -> ClientInfo | None:
        """Client that owns the object, if known to the current session."""
        session = self._session
        return None if session is None else session.client(self.owner_id)

    @property
    def _session(self):
        owner = self.game_object if self._owner is not None else None
        scene = None if owner is None else owner.scene
        return None if scene is None else scene.game.network

    def on_ready(self) -> None:
        """Register this component with the active network session."""
        if self.enabled:
            session = self._session
            if session is not None:
                session.register(self)

    def on_detach(self) -> None:
        """Unregister this component from the active network session."""
        session = self._session
        if session is not None:
            session.unregister(self)

    def on_scene_detach(self) -> None:
        """Unregister while the owner leaves its scene for possible reuse."""
        session = self._session
        if session is not None:
            session.unregister(self)


class NetworkClient(Component):
    """Configure connection to a WebSocket network server.

    Attributes:
        endpoint: WebSocket server address.
        auto_connect: Connect automatically in a client build when ready.
        connection_timeout: Maximum opening and handshake time in seconds.
    """
    endpoint: str = inspector_field(
        "ws://127.0.0.1:7777",
        tooltip="WebSocket server address.",
    )
    auto_connect: bool = inspector_field(
        True,
        tooltip="Connect when this component becomes ready in a client build.",
    )
    connection_timeout: float = inspector_field(
        10.0,
        minimum=0.1,
        tooltip="Seconds allowed for opening and handshaking.",
    )

    def __init__(
        self,
        *,
        endpoint: str = "ws://127.0.0.1:7777",
        auto_connect: bool = True,
        connection_timeout: float = 10,
        enabled: bool = True,
    ) -> None:
        """Create a client connection component.

        Raises:
            ValueError: The endpoint is empty or timeout is not positive.
        """
        super().__init__(enabled=enabled)
        if not endpoint:
            raise ValueError("network endpoint must not be empty")
        if connection_timeout <= 0:
            raise ValueError("connection timeout must be positive")
        self.endpoint = endpoint
        self.auto_connect = auto_connect
        self.connection_timeout = connection_timeout

    @property
    def state(self) -> str:
        """Current network-session state string."""
        return self._network.state

    @property
    def is_connected(self) -> bool:
        """Whether the network session is connected."""
        return self.state == "connected"

    @property
    def client_id(self) -> str | None:
        """Identifier of the local client, if connected and assigned."""
        local = self._network.local_client
        return None if local is None else local.id

    @property
    def client_number(self) -> int | None:
        """Player number of the local client, if assigned."""
        local = self._network.local_client
        return None if local is None else local.number

    @property
    def _network(self):
        scene = self.game_object.scene
        if scene is None:
            raise RuntimeError("NetworkClient object is not in a scene")
        return scene.game.network

    def on_ready(self) -> None:
        """Connect automatically when configured for a client session."""
        if self.enabled and self.auto_connect:
            session = self._network
            if session.role == "client" and session.state == "disconnected":
                session.connect(self.endpoint, timeout=self.connection_timeout)
