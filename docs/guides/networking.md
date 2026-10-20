# Experimental networking

Networking is opt-in and server-authoritative. Its first vertical slice
supports authored object identity, ownership, bounded WebSocket transport,
replicated transforms/physics, custom runtime fields, owner input, and
disconnect cleanup.

```python
from g import NetworkedGameObject, Script, network_field


class RaceProgress(Script):
    lap: int = network_field(1)


# Assume player exists.
player.add_component(NetworkedGameObject(owner_client_number=1))
```

Clients submit input with `game.network.set_input(...)`.
Server gameplay reads owner input through `game.network.input_for(...)`.
Marking an object for replication does not make arbitrary single-player code
authoritative or safe automatically.

## Scope and limitations

- Dedicated server and clients, not a peer-to-peer mesh
- Project-level endpoint, bind address, client limit, and snapshot rate
- A presentation-disabled simulation path for servers
- Separate client/server export targets

Networking is experimental. Matchmaking, accounts, relay hosting, reconnect,
general rollback/prediction, and public-server discovery are not provided as
a complete service.

For Internet deployments, configure TLS, access control, input validation,
firewalls, and hosting separately. Never put server credentials into scenes
or client assets.

The repository's racing example demonstrates server/client operation:

```bash
python examples/racing/run_network.py server
python examples/racing/run_network.py client
```

See the [session API](../api/network.md) and network components in the
[component reference](../api/components.md).
