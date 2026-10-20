from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from math import isfinite
from queue import Empty, Full, Queue
from threading import Lock, Thread
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect as websocket_connect
from websockets.sync.server import serve as websocket_serve

from .inspector import inspector_fields
from .math import Vec2

if TYPE_CHECKING:
    from .components import NetworkedGameObject
    from .game import Game


PROTOCOL_VERSION = 1
MAX_MESSAGE_SIZE = 64 * 1024
LOCAL_POSITION_TOLERANCE = 12.0
LOCAL_ROTATION_TOLERANCE = 4.0
LOCAL_HARD_POSITION_ERROR = 192.0
LOCAL_HARD_ROTATION_ERROR = 45.0
REMOTE_HARD_POSITION_ERROR = 256.0
REMOTE_HARD_ROTATION_ERROR = 90.0
LOCAL_CORRECTION_RATE = 2.0
REMOTE_CORRECTION_RATE = 6.0
MAX_LOCAL_CORRECTION_SPEED = 120.0


class NetworkError(RuntimeError):
    """Base exception for network-session failures."""

    pass


class NetworkAuthorityError(NetworkError):
    """An operation was requested by a session without the required role."""

    pass


@dataclass(frozen=True)
class ClientInfo:
    """Identity assigned to a connected network client.

    Attributes:
        id: Unique client identifier.
        number: Monotonically assigned client number.
    """

    id: str
    number: int


@dataclass
class _Peer:
    info: ClientInfo
    connection: Any
    send_lock: Lock


def _encode_value(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise NetworkError("network values must be finite")
        return value
    if isinstance(value, Vec2):
        return {"$vec2": [value.x, value.y]}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    raise NetworkError(
        f"unsupported network value type: {type(value).__name__}"
    )


def _decode_value(value: object, current: object) -> object:
    if isinstance(current, Vec2):
        if (
            not isinstance(value, dict)
            or set(value) != {"$vec2"}
            or not isinstance(value["$vec2"], list)
            or len(value["$vec2"]) != 2
        ):
            raise NetworkError("invalid network Vec2")
        return Vec2(float(value["$vec2"][0]), float(value["$vec2"][1]))
    if isinstance(current, Enum):
        if not isinstance(value, str):
            raise NetworkError("invalid network enum")
        return type(current)[value]
    if current is None or isinstance(current, (bool, int, float, str, tuple)):
        return value
    raise NetworkError(
        f"unsupported network destination type: {type(current).__name__}"
    )


class NetworkSession:
    """Manage a game's offline, client, or server network session.

    Attributes:
        game: Game instance this session belongs to.
        role: Configured role: ``"offline"``, ``"client"``, or ``"server"``.
        state: Current connection lifecycle state.
        local_client: Client identity assigned to this client, if connected.
        last_error: Most recent connection error text, if any.
        snapshot_hz: Configured snapshot frequency.
        max_clients: Configured server client limit.
        accepting_clients: Whether the server admits new clients.
    """

    def __init__(self, game: Game) -> None:
        """Create a disconnected session for ``game``."""
        self.game = game
        self.role = "offline"
        self.state = "disconnected"
        self.local_client: ClientInfo | None = None
        self.last_error: str | None = None
        self.snapshot_hz = 20
        self.max_clients = 8
        self.accepting_clients = True
        self._snapshot_elapsed = 0.0
        self._objects: dict[str, NetworkedGameObject] = {}
        self._targets: dict[str, dict[str, Any]] = {}
        self._inputs: dict[str, dict[str, object]] = {}
        self._incoming: Queue[tuple[str, object]] = Queue(maxsize=1024)
        self._peers: dict[str, _Peer] = {}
        self._peer_lock = Lock()
        self._next_client_number = 1
        self._server: Any = None
        self._server_thread: Thread | None = None
        self._connection: Any = None
        self._connection_lock = Lock()
        self._client_thread: Thread | None = None
        self._client_send_thread: Thread | None = None
        self._client_outgoing: Queue[str | None] = Queue(maxsize=2)

    @property
    def is_server(self) -> bool:
        """Whether this session is configured as a server."""
        return self.role == "server"

    @property
    def is_client(self) -> bool:
        """Whether this session is configured as a client."""
        return self.role == "client"

    @property
    def clients(self) -> tuple[ClientInfo, ...]:
        """Connected remote clients ordered by assigned client number."""
        with self._peer_lock:
            return tuple(
                peer.info
                for peer in sorted(
                    self._peers.values(),
                    key=lambda peer: peer.info.number,
                )
            )

    def client(self, client_id: str | None) -> ClientInfo | None:
        """Look up a local or remote client by ID.

        Args:
            client_id: Client ID to find; ``None`` returns ``None``.

        Returns:
            Matching client information, or ``None`` when not connected.
        """
        if client_id is None:
            return None
        if self.local_client is not None and self.local_client.id == client_id:
            return self.local_client
        with self._peer_lock:
            peer = self._peers.get(client_id)
        return None if peer is None else peer.info

    def configure(
        self,
        role: str,
        *,
        snapshot_hz: int = 20,
        max_clients: int = 8,
    ) -> None:
        """Configure the session role and connection limits.

        Args:
            role: ``"offline"``, ``"client"``, or ``"server"``.
            snapshot_hz: Positive snapshot frequency.
            max_clients: Positive maximum remote client count.

        Raises:
            ValueError: If the role or either limit is invalid.
            NetworkError: If the session is currently active.
        """
        if role not in {"offline", "client", "server"}:
            raise ValueError("network role must be offline, client, or server")
        if snapshot_hz <= 0:
            raise ValueError("network snapshot rate must be positive")
        if max_clients <= 0:
            raise ValueError("network max clients must be positive")
        if self.state not in {"disconnected", "failed"}:
            raise NetworkError("cannot reconfigure an active network session")
        self.role = role
        self.snapshot_hz = snapshot_hz
        self.max_clients = max_clients
        self.accepting_clients = True
        self.last_error = None
        self.state = "disconnected"

    def register(self, marker: NetworkedGameObject) -> None:
        """Register a network marker belonging to the current scene.

        Scene objects receive a scene ID when needed; duplicate IDs or more
        than one marker on the same object are rejected.

        Args:
            marker: Networked game-object component to register.

        Raises:
            NetworkError: If the ID is duplicated or the object already has a
                different network marker.
        """
        owner = marker.game_object
        if owner.scene is not self.game.scenes.current:
            return
        identifier = owner.scene_id
        if identifier is None:
            identifier = str(uuid4())
            owner.scene_id = identifier
        existing = self._objects.get(identifier)
        if existing is not None and existing is not marker:
            raise NetworkError(f"duplicate network object ID: {identifier}")
        if any(
            item is not marker and item.game_object is owner
            for item in self._objects.values()
        ):
            raise NetworkError(
                "a game object may have only one NetworkedGameObject"
            )
        marker.network_id = identifier
        self._objects[identifier] = marker
        if self.is_server:
            self._assign_owner(marker)

    def unregister(self, marker: NetworkedGameObject) -> None:
        """Remove a marker and clear its assigned network and owner IDs."""
        identifier = marker.network_id
        if identifier is not None and self._objects.get(identifier) is marker:
            del self._objects[identifier]
            self._targets.pop(identifier, None)
            self._inputs.pop(identifier, None)
            if self.is_server and self.state == "listening":
                self._broadcast({
                    "type": "despawn",
                    "object": identifier,
                })
        marker.network_id = None
        marker.owner_id = None

    def listen(self, host: str = "127.0.0.1", port: int = 7777) -> int:
        """Start the server listener and return its bound port.

        Args:
            host: Interface address on which to listen.
            port: TCP port, with ``0`` allowing an automatically chosen port.

        Returns:
            Actual bound port.

        Raises:
            NetworkAuthorityError: If this session is not a server.
            NetworkError: If the session is active or the socket cannot bind.
        """
        if not self.is_server:
            raise NetworkAuthorityError("only a server session can listen")
        if self.state != "disconnected":
            raise NetworkError("network server is already active")
        self.state = "starting"
        try:
            self._server = websocket_serve(
                self._handle_server_connection,
                host,
                port,
                max_size=MAX_MESSAGE_SIZE,
                max_queue=16,
            )
        except OSError as error:
            self.state = "failed"
            self.last_error = str(error)
            raise NetworkError(f"could not listen on {host}:{port}: {error}") from error
        self._server_thread = Thread(
            target=self._server.serve_forever,
            name="g-network-server",
            daemon=True,
        )
        self._server_thread.start()
        self.state = "listening"
        return int(self._server.socket.getsockname()[1])

    def connect(self, endpoint: str, *, timeout: float = 10) -> None:
        """Begin connecting to a WebSocket server on a background thread.

        Args:
            endpoint: WebSocket server URL.
            timeout: Connection-open timeout in seconds.

        Raises:
            NetworkAuthorityError: If this session is not a client.
            NetworkError: If the session is already active.

        Connection errors after the background attempt begins are recorded in
        ``last_error`` and reflected in ``state``.
        """
        if not self.is_client:
            raise NetworkAuthorityError("only a client session can connect")
        if self.state != "disconnected":
            raise NetworkError("network client is already active")
        self.state = "connecting"
        self.last_error = None
        self._clear_client_outgoing()

        def run() -> None:
            sender: Thread | None = None
            try:
                connection = websocket_connect(
                    endpoint,
                    open_timeout=timeout,
                    max_size=MAX_MESSAGE_SIZE,
                    max_queue=16,
                )
                with self._connection_lock:
                    self._connection = connection
                connection.send(json.dumps({
                    "type": "hello",
                    "protocol": PROTOCOL_VERSION,
                }))
                sender = Thread(
                    target=self._send_client_messages,
                    args=(connection,),
                    name="g-network-client-send",
                    daemon=True,
                )
                self._client_send_thread = sender
                sender.start()
                for raw in connection:
                    self._queue("message", raw)
            except (OSError, TimeoutError, WebSocketException) as error:
                self._queue("client_error", str(error))
            finally:
                self._enqueue_client_payload(None)
                self._queue("client_closed", None)

        self._client_thread = Thread(
            target=run,
            name="g-network-client",
            daemon=True,
        )
        self._client_thread.start()

    def disconnect(self) -> None:
        """Close the active connection and clear the local client identity.

        The configured role is retained and ``state`` becomes disconnected.
        """
        if self.is_server:
            self._stop_server()
        else:
            self._enqueue_client_payload(None)
            with self._connection_lock:
                connection = self._connection
                self._connection = None
            if connection is not None:
                connection.close()
        self.local_client = None
        self.state = "disconnected"

    def set_input(
        self,
        marker: NetworkedGameObject,
        **values: object,
    ) -> None:
        """Submit input values for a registered network object.

        Args:
            marker: Registered object whose input is being submitted.
            **values: Values supported by the network value encoder.

        Raises:
            NetworkError: If the marker is unregistered or a value cannot be
                encoded.
            NetworkAuthorityError: If a client is not the object's local owner.
        """
        if marker.network_id is None:
            raise NetworkError("network object is not registered")
        encoded = {
            name: _encode_value(value)
            for name, value in values.items()
        }
        if self.is_server:
            self._inputs[marker.network_id] = encoded
            return
        if not self.is_client or not marker.is_local_owner:
            raise NetworkAuthorityError(
                "only the local owner may send object input"
            )
        self._send_client({
            "type": "input",
            "object": marker.network_id,
            "values": encoded,
        })

    def input_for(
        self,
        marker: NetworkedGameObject,
    ) -> dict[str, object]:
        """Return queued input for an object on the server.

        Args:
            marker: Object whose latest input is requested.

        Returns:
            A copy of its latest encoded input mapping, or an empty mapping if
            it has no registered network ID or no queued input.

        Raises:
            NetworkAuthorityError: If this session is not a server.
        """
        if not self.is_server:
            raise NetworkAuthorityError("only the server consumes network input")
        if marker.network_id is None:
            return {}
        return dict(self._inputs.get(marker.network_id, {}))

    def close_admissions(self) -> None:
        """Stop a server from admitting additional clients.

        Raises:
            NetworkAuthorityError: If this session is not a server.
        """
        if not self.is_server:
            raise NetworkAuthorityError(
                "only a server session can close admissions"
            )
        self.accepting_clients = False

    def _before_update(self) -> None:
        self._refresh_objects()
        while True:
            try:
                kind, payload = self._incoming.get_nowait()
            except Empty:
                break
            if kind == "server_connected":
                self._on_server_connected(str(payload))
            elif kind == "server_disconnected":
                self._on_server_disconnected(str(payload))
            elif kind == "server_message":
                client_id, raw = payload  # type: ignore[misc]
                self._handle_server_message(str(client_id), raw)
            elif kind == "message":
                self._handle_client_message(payload)
            elif kind == "client_error":
                self.last_error = str(payload)
                self.state = "failed"
            elif kind == "client_closed" and self.state not in {
                "disconnected", "failed",
            }:
                self.state = "disconnected"
                self.local_client = None
        if self.is_client:
            for identifier, state in tuple(self._targets.items()):
                self._targets.pop(identifier, None)
                marker = self._objects.get(identifier)
                if marker is not None:
                    self._apply_state(marker, state)

    def _after_update(self, delta_time: float) -> None:
        self._refresh_objects()
        if not self.is_server or self.state != "listening":
            return
        self._snapshot_elapsed += delta_time
        interval = 1 / self.snapshot_hz
        if self._snapshot_elapsed < interval:
            return
        self._snapshot_elapsed %= interval
        states = [
            self._capture_state(marker)
            for marker in tuple(self._objects.values())
            if marker.enabled and marker.network_id is not None
        ]
        self._broadcast({"type": "snapshot", "objects": states})

    def _refresh_objects(self) -> None:
        from .components import NetworkedGameObject
        from .nodes import GameObject

        active: dict[str, NetworkedGameObject] = {}
        for node in self.game.scenes.current.walk():
            if not isinstance(node, GameObject):
                continue
            marker = node.get_component(NetworkedGameObject)
            if marker is None or not marker.enabled:
                continue
            identifier = node.scene_id
            if identifier is None:
                identifier = str(uuid4())
                node.scene_id = identifier
            marker.network_id = identifier
            if identifier in active:
                raise NetworkError(
                    f"duplicate network object ID: {identifier}"
                )
            active[identifier] = marker
            if self.is_server:
                self._assign_owner(marker)
        for identifier, marker in tuple(self._objects.items()):
            if identifier not in active:
                marker.network_id = None
                marker.owner_id = None
                self._targets.pop(identifier, None)
                self._inputs.pop(identifier, None)
        self._objects = active

    def _capture_state(
        self,
        marker: NetworkedGameObject,
    ) -> dict[str, object]:
        from .physics import PhysicsBody

        owner = marker.game_object
        state: dict[str, object] = {
            "id": marker.network_id,
            "owner": marker.owner_id,
            "position": [owner.x, owner.y],
            "rotation": owner.rotation,
            "enabled": owner.enabled,
            "visible": owner.visible,
        }
        if marker.sync_scale:
            state["scale"] = [owner.scale_x, owner.scale_y]
        body = owner.get_component(PhysicsBody)
        if marker.synchronization == "automatic" and body is not None:
            state["velocity"] = list(body.velocity)
            state["angular_velocity"] = body.angular_velocity
        if marker.synchronization != "transform_only":
            fields: list[dict[str, object]] = []
            for index, component in enumerate(owner.components):
                for name, declaration in inspector_fields(type(component)).items():
                    if declaration.networked:
                        fields.append({
                            "component": index,
                            "field": name,
                            "value": _encode_value(getattr(component, name)),
                        })
            state["fields"] = fields
        return state

    def _apply_state(
        self,
        marker: NetworkedGameObject,
        state: dict[str, Any],
    ) -> None:
        from .physics import PhysicsBody

        owner = marker.game_object
        marker.owner_id = (
            state.get("owner") if isinstance(state.get("owner"), str) else None
        )
        body = owner.get_component(PhysicsBody)
        velocity = state.get("velocity")
        authoritative_velocity: tuple[float, float] | None = None
        if (
            isinstance(velocity, list)
            and len(velocity) == 2
            and all(isinstance(item, (int, float)) for item in velocity)
        ):
            authoritative_velocity = float(velocity[0]), float(velocity[1])
        angular = state.get("angular_velocity")
        authoritative_angular = (
            float(angular) if isinstance(angular, (int, float)) else None
        )
        position = state.get("position")
        rotation = state.get("rotation")
        if (
            isinstance(position, list)
            and len(position) == 2
            and all(isinstance(item, (int, float)) for item in position)
            and isinstance(rotation, (int, float))
        ):
            target_x, target_y = float(position[0]), float(position[1])
            target_rotation = float(rotation)
            apply_transform = True
            if marker.remote_smoothing == "interpolate":
                delta = (target_rotation - owner.rotation + 180) % 360 - 180
                error_x = target_x - owner.x
                error_y = target_y - owner.y
                error_squared = error_x * error_x + error_y * error_y
                if body is not None:
                    if marker.is_local_owner:
                        prediction_window = max(2 / self.snapshot_hz, 1 / 30)
                        speed = (
                            0.0
                            if authoritative_velocity is None
                            else (
                                authoritative_velocity[0] ** 2
                                + authoritative_velocity[1] ** 2
                            ) ** 0.5
                        )
                        angular_speed = abs(authoritative_angular or 0.0)
                        position_tolerance = (
                            LOCAL_POSITION_TOLERANCE
                            + speed * prediction_window
                        )
                        rotation_tolerance = (
                            LOCAL_ROTATION_TOLERANCE
                            + angular_speed * prediction_window
                        )
                        apply_transform = (
                            error_squared > LOCAL_HARD_POSITION_ERROR ** 2
                            or abs(delta) > LOCAL_HARD_ROTATION_ERROR
                        )
                        if not apply_transform and (
                            error_squared > position_tolerance ** 2
                            or abs(delta) > rotation_tolerance
                        ):
                            correction_x = max(
                                -MAX_LOCAL_CORRECTION_SPEED,
                                min(
                                    MAX_LOCAL_CORRECTION_SPEED,
                                    error_x * LOCAL_CORRECTION_RATE,
                                ),
                            )
                            correction_y = max(
                                -MAX_LOCAL_CORRECTION_SPEED,
                                min(
                                    MAX_LOCAL_CORRECTION_SPEED,
                                    error_y * LOCAL_CORRECTION_RATE,
                                ),
                            )
                            current_x, current_y = body.velocity
                            body.velocity = (
                                current_x + correction_x,
                                current_y + correction_y,
                            )
                            body.angular_velocity += delta * LOCAL_CORRECTION_RATE
                    else:
                        apply_transform = (
                            error_squared > REMOTE_HARD_POSITION_ERROR ** 2
                            or abs(delta) > REMOTE_HARD_ROTATION_ERROR
                        )
                        if not apply_transform:
                            base_x, base_y = (
                                body.velocity
                                if authoritative_velocity is None
                                else authoritative_velocity
                            )
                            body.velocity = (
                                base_x + error_x * REMOTE_CORRECTION_RATE,
                                base_y + error_y * REMOTE_CORRECTION_RATE,
                            )
                            if authoritative_angular is not None:
                                body.angular_velocity = (
                                    authoritative_angular
                                    + delta * REMOTE_CORRECTION_RATE
                                )
                else:
                    if marker.is_local_owner:
                        if (
                            error_squared <= LOCAL_POSITION_TOLERANCE ** 2
                            and abs(delta) <= LOCAL_ROTATION_TOLERANCE
                        ):
                            apply_transform = False
                        else:
                            target_x = owner.x + error_x * 0.2
                            target_y = owner.y + error_y * 0.2
                            target_rotation = owner.rotation + delta * 0.2
                    else:
                        target_x = owner.x + error_x * 0.5
                        target_y = owner.y + error_y * 0.5
                        target_rotation = owner.rotation + delta * 0.5
            if apply_transform:
                if body is not None:
                    body.teleport(target_x, target_y, target_rotation)
                else:
                    owner.position = target_x, target_y
                    owner.rotation = target_rotation
        owner.enabled = bool(state.get("enabled", owner.enabled))
        owner.visible = bool(state.get("visible", owner.visible))
        scale = state.get("scale")
        if isinstance(scale, list) and len(scale) == 2:
            owner.scale_x, owner.scale_y = float(scale[0]), float(scale[1])
        if body is not None and authoritative_velocity is not None:
            velocity_x, velocity_y = authoritative_velocity
            if marker.is_local_owner:
                current_x, current_y = body.velocity
                body.velocity = (
                    current_x + (velocity_x - current_x) * 0.2,
                    current_y + (velocity_y - current_y) * 0.2,
                )
            elif marker.remote_smoothing != "interpolate":
                body.velocity = velocity_x, velocity_y
            if authoritative_angular is not None:
                if marker.is_local_owner:
                    body.angular_velocity += (
                        authoritative_angular - body.angular_velocity
                    ) * 0.2
                elif marker.remote_smoothing != "interpolate":
                    body.angular_velocity = authoritative_angular
        fields = state.get("fields", [])
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                index, name = field.get("component"), field.get("field")
                if (
                    not isinstance(index, int)
                    or not isinstance(name, str)
                    or not 0 <= index < len(owner.components)
                ):
                    continue
                component = owner.components[index]
                metadata = inspector_fields(type(component)).get(name)
                if metadata is None or not metadata.networked:
                    continue
                current = getattr(component, name)
                setattr(
                    component,
                    name,
                    _decode_value(field.get("value"), current),
                )

    def _handle_server_connection(self, connection: Any) -> None:
        client_id: str | None = None
        try:
            raw = connection.recv(timeout=10)
            message = self._decode_message(raw)
            if (
                message.get("type") != "hello"
                or message.get("protocol") != PROTOCOL_VERSION
            ):
                connection.close(code=1002, reason="protocol mismatch")
                return
            with self._peer_lock:
                if not self.accepting_clients:
                    connection.close(code=1013, reason="server lobby is closed")
                    return
                if len(self._peers) >= self.max_clients:
                    connection.close(code=1013, reason="server is full")
                    return
                number = self._next_client_number
                self._next_client_number += 1
                client_id = str(uuid4())
                peer = _Peer(
                    ClientInfo(client_id, number),
                    connection,
                    Lock(),
                )
                self._peers[client_id] = peer
            connection.send(json.dumps({
                "type": "welcome",
                "protocol": PROTOCOL_VERSION,
                "client_id": client_id,
                "client_number": number,
            }))
            self._queue("server_connected", client_id)
            for raw in connection:
                self._queue("server_message", (client_id, raw))
        except (ConnectionClosed, TimeoutError, NetworkError):
            pass
        finally:
            if client_id is not None:
                with self._peer_lock:
                    self._peers.pop(client_id, None)
                self._queue("server_disconnected", client_id)

    def _handle_server_message(self, client_id: str, raw: object) -> None:
        message = self._decode_message(raw)
        if message.get("type") != "input":
            return
        identifier = message.get("object")
        values = message.get("values")
        marker = self._objects.get(identifier) if isinstance(identifier, str) else None
        if (
            not isinstance(identifier, str)
            or
            marker is None
            or marker.owner_id != client_id
            or not isinstance(values, dict)
            or len(values) > 16
        ):
            return
        self._inputs[identifier] = dict(values)

    def _handle_client_message(self, raw: object) -> None:
        message = self._decode_message(raw)
        message_type = message.get("type")
        if message_type == "welcome":
            client_id, number = message.get("client_id"), message.get("client_number")
            if not isinstance(client_id, str) or not isinstance(number, int):
                raise NetworkError("invalid server welcome")
            self.local_client = ClientInfo(client_id, number)
            self.state = "connected"
        elif message_type == "snapshot":
            objects = message.get("objects")
            if not isinstance(objects, list):
                raise NetworkError("invalid snapshot")
            for state in objects:
                if isinstance(state, dict) and isinstance(state.get("id"), str):
                    self._targets[state["id"]] = state
        elif message_type == "despawn":
            identifier = message.get("object")
            marker = (
                self._objects.get(identifier)
                if isinstance(identifier, str)
                else None
            )
            if isinstance(identifier, str) and marker is not None:
                marker.game_object.delete()
                self._targets.pop(identifier, None)

    def _on_server_connected(self, client_id: str) -> None:
        with self._peer_lock:
            peer = self._peers.get(client_id)
        if peer is None:
            return
        for marker in self._objects.values():
            if marker.owner_client_number == peer.info.number:
                marker.owner_id = client_id
        self._send_peer(peer, {
            "type": "snapshot",
            "objects": [
                self._capture_state(marker)
                for marker in self._objects.values()
                if marker.enabled and marker.network_id is not None
            ],
        })

    def _on_server_disconnected(self, client_id: str) -> None:
        for marker in tuple(self._objects.values()):
            if marker.owner_id != client_id:
                continue
            marker.owner_id = None
            if marker.network_id is not None:
                self._inputs.pop(marker.network_id, None)
            if marker.owner_disconnect == "despawn":
                marker.game_object.delete()

    def _assign_owner(self, marker: NetworkedGameObject) -> None:
        if marker.owner_client_number <= 0:
            return
        with self._peer_lock:
            peer = next(
                (
                    item
                    for item in self._peers.values()
                    if item.info.number == marker.owner_client_number
                ),
                None,
            )
        marker.owner_id = None if peer is None else peer.info.id

    def _broadcast(self, message: dict[str, object]) -> None:
        with self._peer_lock:
            peers = tuple(self._peers.values())
        for peer in peers:
            self._send_peer(peer, message)

    def _send_peer(self, peer: _Peer, message: dict[str, object]) -> None:
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > MAX_MESSAGE_SIZE:
            raise NetworkError("network message exceeds size limit")
        try:
            with peer.send_lock:
                peer.connection.send(payload)
        except ConnectionClosed:
            pass

    def _send_client(self, message: dict[str, object]) -> None:
        payload = json.dumps(message, separators=(",", ":"), allow_nan=False)
        if len(payload.encode("utf-8")) > MAX_MESSAGE_SIZE:
            raise NetworkError("network message exceeds size limit")
        with self._connection_lock:
            connection = self._connection
            if connection is None:
                raise NetworkError("network client is not connected")
        self._enqueue_client_payload(payload)

    def _send_client_messages(self, connection: Any) -> None:
        while True:
            payload = self._client_outgoing.get()
            if payload is None:
                return
            try:
                connection.send(payload)
            except (ConnectionClosed, OSError, WebSocketException) as error:
                self._queue("client_error", str(error))
                try:
                    connection.close()
                except ConnectionClosed:
                    pass
                return

    def _enqueue_client_payload(self, payload: str | None) -> None:
        try:
            self._client_outgoing.put_nowait(payload)
            return
        except Full:
            pass
        try:
            self._client_outgoing.get_nowait()
        except Empty:
            pass
        try:
            self._client_outgoing.put_nowait(payload)
        except Full:
            self.last_error = "network send queue is full"

    def _clear_client_outgoing(self) -> None:
        while True:
            try:
                self._client_outgoing.get_nowait()
            except Empty:
                return

    def _stop_server(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
        with self._peer_lock:
            peers = tuple(self._peers.values())
            self._peers.clear()
        for peer in peers:
            peer.connection.close()

    def _queue(self, kind: str, payload: object) -> None:
        try:
            self._incoming.put_nowait((kind, payload))
        except Full:
            self.last_error = "network receive queue is full"

    @staticmethod
    def _decode_message(raw: object) -> dict[str, Any]:
        if not isinstance(raw, str):
            raise NetworkError("binary network messages are unsupported")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise NetworkError(f"invalid network message: {error}") from error
        if not isinstance(value, dict):
            raise NetworkError("network message must be an object")
        return value
