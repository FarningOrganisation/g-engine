"""Grid navigation API.

``GridPosition`` is an ``(column, row)`` integer pair. Navigation worlds use
pixel coordinates for game objects and grid coordinates for path results.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from enum import Enum
from heapq import heappop, heappush
from itertools import count
from math import floor, hypot
from typing import TYPE_CHECKING, TypeAlias

from .components import Component

if TYPE_CHECKING:
    from .nodes import GameObject, Node


GridPosition: TypeAlias = tuple[int, int]
ObjectProvider: TypeAlias = Callable[[], Iterable["Node"]]


class PathfindingAlgorithm(Enum):
    """Search strategy used by :meth:`NavigationWorld.find_path`.

    Attributes:
        A_STAR: Weighted search guided by a distance heuristic.
        DIJKSTRA: Weighted search without a heuristic.
        BREADTH_FIRST: Unweighted search that minimizes step count.
    """

    A_STAR = "astar"
    DIJKSTRA = "dijkstra"
    BREADTH_FIRST = "breadth_first"


class NavigationObstacle(Component):
    """Rectangular navigation cost or blocking region attached to an object.

    Attributes:
        width: Number of grid cells occupied horizontally.
        height: Number of grid cells occupied vertically.
        cost: Traversal cost, or ``None`` to block occupied cells.
    """

    def __init__(
        self,
        width: int = 1,
        height: int = 1,
        *,
        cost: float | None = None,
        enabled: bool = True,
    ) -> None:
        """Create an obstacle.

        Args:
            width: Positive number of occupied columns.
            height: Positive number of occupied rows.
            cost: Positive traversal cost, or ``None`` for impassable cells.
            enabled: Whether the component participates in navigation.

        Raises:
            ValueError: If dimensions are not positive or cost is non-positive.
        """
        super().__init__(enabled=enabled)
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if cost is not None and cost <= 0:
            raise ValueError("cost must be positive or None")
        self.width = width
        self.height = height
        self.cost = cost

    def occupied_cells(self, world: NavigationWorld) -> tuple[GridPosition, ...]:
        """Return cells covered by the owner's world-position-centered region.

        Args:
            world: Grid used to convert the owner's position to a cell.

        Returns:
            Occupied grid positions; empty when the component has no owner.
        """
        if self.owner is None:
            return ()
        center_x, center_y = world.pixel_to_grid(*self.owner.world_position)
        start_x = center_x - self.width // 2
        start_y = center_y - self.height // 2
        return tuple(
            (start_x + offset_x, start_y + offset_y)
            for offset_y in range(self.height)
            for offset_x in range(self.width)
        )


class NavigationWorld:
    """Finite grid for weighted pathfinding and object-based obstacles.

    Attributes:
        width: Grid width in cells.
        height: Grid height in cells.
        cell_size: Cell edge length in pixels.
        allow_diagonal: Whether diagonal neighbors are considered.
        allow_corner_cutting: Whether diagonals may pass blocked corners.
    """

    def __init__(
        self,
        width: int,
        height: int,
        cell_size: int,
        *,
        allow_diagonal: bool = False,
        allow_corner_cutting: bool = False,
    ) -> None:
        """Create a navigation grid.

        Args:
            width: Positive number of columns.
            height: Positive number of rows.
            cell_size: Positive cell size in pixels.
            allow_diagonal: Include diagonal movement when finding neighbors.
            allow_corner_cutting: Permit diagonals past blocked side cells.

        Raises:
            ValueError: If a grid dimension or cell size is not positive.
        """
        if width <= 0 or height <= 0 or cell_size <= 0:
            raise ValueError("width, height and cell_size must be positive")
        self.width = width
        self.height = height
        self.cell_size = cell_size
        self.allow_diagonal = allow_diagonal
        self.allow_corner_cutting = allow_corner_cutting
        self._base_costs: dict[GridPosition, float | None] = {}
        self._object_provider: ObjectProvider | None = None
        self._costs: dict[GridPosition, float | None] = {}

    def bind(self, object_provider: ObjectProvider) -> None:
        """Set the provider whose enabled objects supply obstacles.

        Args:
            object_provider: Callable returning the nodes to inspect.
        """
        self._object_provider = object_provider

    def pixel_to_grid(self, x: float, y: float) -> GridPosition:
        """Convert pixel coordinates to the containing grid cell.

        Args:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.
        """
        return floor(x / self.cell_size), floor(y / self.cell_size)

    def grid_to_pixel(self, position: GridPosition) -> tuple[float, float]:
        """Return the pixel-space center of a grid cell.

        Args:
            position: Grid cell as a ``(column, row)`` pair.
        """
        grid_x, grid_y = position
        offset = self.cell_size / 2
        return grid_x * self.cell_size + offset, grid_y * self.cell_size + offset

    def contains(self, position: GridPosition) -> bool:
        """Return whether a cell lies within the grid bounds.

        Args:
            position: Grid cell as a ``(column, row)`` pair.
        """
        x, y = position
        return 0 <= x < self.width and 0 <= y < self.height

    def set_cost(self, position: GridPosition, cost: float | None) -> None:
        """Set a persistent base traversal cost or mark a cell blocked.

        Args:
            position: Cell to change.
            cost: Positive cost, or ``None`` to block the cell.

        Raises:
            ValueError: If the cell is outside the grid or cost is non-positive.
        """
        if not self.contains(position):
            raise ValueError("position is outside the navigation world")
        if cost is not None and cost <= 0:
            raise ValueError("cost must be positive or None")
        self._base_costs[position] = cost

    def clear_cost(self, position: GridPosition) -> None:
        """Remove a cell's base cost override.

        Args:
            position: Cell whose override should be removed.
        """
        self._base_costs.pop(position, None)

    def refresh(self) -> None:
        """Rebuild effective costs from base overrides and enabled obstacles."""
        self._costs = dict(self._base_costs)
        if self._object_provider is None:
            return
        for node in self._object_provider():
            from .nodes import GameObject

            if not isinstance(node, GameObject) or not node.enabled_in_hierarchy:
                continue
            obstacle = node.get_component(NavigationObstacle)
            if obstacle is None or not obstacle.enabled:
                continue
            for position in obstacle.occupied_cells(self):
                if not self.contains(position):
                    continue
                current_cost = self._costs.get(position, 1.0)
                if obstacle.cost is None or current_cost is None:
                    self._costs[position] = None
                else:
                    self._costs[position] = max(current_cost, obstacle.cost)

    def cost_at(self, position: GridPosition) -> float | None:
        """Return the effective cost; ``None`` means blocked or out of bounds.

        Args:
            position: Grid cell to query.
        """
        if not self.contains(position):
            return None
        return self._costs.get(position, 1.0)

    def is_walkable(self, position: GridPosition) -> bool:
        """Return whether a cell is in bounds and not blocked.

        Args:
            position: Grid cell to query.
        """
        return self.contains(position) and self.cost_at(position) is not None

    def neighbors(self, position: GridPosition) -> tuple[GridPosition, ...]:
        """Return walkable adjacent cells respecting diagonal settings.

        Args:
            position: Grid cell whose neighbors are requested.
        """
        x, y = position
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if self.allow_diagonal:
            directions.extend(((1, 1), (1, -1), (-1, 1), (-1, -1)))

        neighbors: list[GridPosition] = []
        for offset_x, offset_y in directions:
            neighbor = x + offset_x, y + offset_y
            if not self.is_walkable(neighbor):
                continue
            if (
                offset_x
                and offset_y
                and not self.allow_corner_cutting
                and (
                    not self.is_walkable((x + offset_x, y))
                    or not self.is_walkable((x, y + offset_y))
                )
            ):
                continue
            neighbors.append(neighbor)
        return tuple(neighbors)

    def find_path(
        self,
        start: GridPosition,
        goal: GridPosition,
        algorithm: PathfindingAlgorithm = PathfindingAlgorithm.A_STAR,
    ) -> tuple[GridPosition, ...] | None:
        """Find a path after refreshing obstacle costs.

        Args:
            start: Starting grid cell.
            goal: Destination grid cell.
            algorithm: Search strategy; weighted algorithms honor cell costs.

        Returns:
            A path including both endpoints, or ``None`` when no route exists.

        Raises:
            ValueError: If either endpoint is outside the grid.
        """
        self.refresh()
        if not self.contains(start) or not self.contains(goal):
            raise ValueError("start and goal must be inside the navigation world")
        if start == goal:
            return (start,)
        if not self.is_walkable(goal):
            return None
        if algorithm is PathfindingAlgorithm.BREADTH_FIRST:
            return self._breadth_first(start, goal)
        return self._weighted_search(start, goal, algorithm)

    def _breadth_first(
        self, start: GridPosition, goal: GridPosition
    ) -> tuple[GridPosition, ...] | None:
        frontier = deque([start])
        previous: dict[GridPosition, GridPosition | None] = {start: None}
        while frontier:
            current = frontier.popleft()
            if current == goal:
                return self._reconstruct(previous, goal)
            for neighbor in self.neighbors(current):
                if neighbor not in previous:
                    previous[neighbor] = current
                    frontier.append(neighbor)
        return None

    def _weighted_search(
        self,
        start: GridPosition,
        goal: GridPosition,
        algorithm: PathfindingAlgorithm,
    ) -> tuple[GridPosition, ...] | None:
        order = count()
        frontier: list[tuple[float, int, GridPosition]] = [(0, next(order), start)]
        previous: dict[GridPosition, GridPosition | None] = {start: None}
        costs: dict[GridPosition, float] = {start: 0}

        while frontier:
            _, _, current = heappop(frontier)
            if current == goal:
                return self._reconstruct(previous, goal)
            for neighbor in self.neighbors(current):
                cell_cost = self.cost_at(neighbor)
                if cell_cost is None:
                    continue
                diagonal_factor = (
                    2**0.5
                    if current[0] != neighbor[0] and current[1] != neighbor[1]
                    else 1.0
                )
                new_cost = costs[current] + cell_cost * diagonal_factor
                if neighbor in costs and new_cost >= costs[neighbor]:
                    continue
                costs[neighbor] = new_cost
                previous[neighbor] = current
                heuristic = (
                    self._heuristic(neighbor, goal)
                    if algorithm is PathfindingAlgorithm.A_STAR
                    else 0
                )
                heappush(
                    frontier,
                    (new_cost + heuristic, next(order), neighbor),
                )
        return None

    def _heuristic(self, start: GridPosition, goal: GridPosition) -> float:
        difference_x = abs(start[0] - goal[0])
        difference_y = abs(start[1] - goal[1])
        minimum_cost = min(
            (1.0, *(cost for cost in self._costs.values() if cost is not None))
        )
        if self.allow_diagonal:
            return hypot(difference_x, difference_y) * minimum_cost
        return (difference_x + difference_y) * minimum_cost

    @staticmethod
    def _reconstruct(
        previous: dict[GridPosition, GridPosition | None],
        goal: GridPosition,
    ) -> tuple[GridPosition, ...]:
        path = [goal]
        current = goal
        while previous[current] is not None:
            parent = previous[current]
            if parent is None:
                break
            current = parent
            path.append(current)
        path.reverse()
        return tuple(path)


class NavigationAgent(Component):
    """Move a root game object along a path in a navigation world.

    Attributes:
        speed: Movement speed in pixels per second.
        algorithm: Search algorithm used to plan routes.
        world: Navigation world assigned by the game, if any.
        path: Current path as grid positions.
    """

    def __init__(
        self,
        speed: float,
        *,
        algorithm: PathfindingAlgorithm = PathfindingAlgorithm.A_STAR,
        enabled: bool = True,
    ) -> None:
        """Create an agent.

        Args:
            speed: Positive movement speed in pixels per second.
            algorithm: Pathfinding strategy to use.
            enabled: Whether the component updates.

        Raises:
            ValueError: If ``speed`` is not positive.
        """
        super().__init__(enabled=enabled)
        if speed <= 0:
            raise ValueError("speed must be positive")
        self.speed = speed
        self.algorithm = algorithm
        self.world: NavigationWorld | None = None
        self.path: tuple[GridPosition, ...] = ()
        self._path_index = 0

    @property
    def destination(self) -> GridPosition | None:
        """Last cell in the current path, or ``None`` when idle."""
        return self.path[-1] if self.path else None

    @property
    def moving(self) -> bool:
        """Whether the agent has an unvisited path cell."""
        return self._path_index < len(self.path)

    def move_to(self, destination: GridPosition) -> bool:
        """Plan a route from the owner's current cell to ``destination``.

        Args:
            destination: Destination grid cell.

        Returns:
            ``True`` when a route is available; otherwise clears the path and
            returns ``False``.

        Raises:
            RuntimeError: If the agent is not attached to a game with a world.
        """
        if self.owner is None or self.world is None:
            raise RuntimeError("agent must belong to a game before moving")
        start = self.world.pixel_to_grid(*self.owner.world_position)
        path = self.world.find_path(start, destination, self.algorithm)
        if path is None:
            self.stop()
            return False
        self.path = path
        self._path_index = 1
        return True

    def stop(self) -> None:
        """Discard the current path."""
        self.path = ()
        self._path_index = 0

    def update(self, delta_time: float) -> None:
        """Advance along the path by at most ``speed * delta_time`` pixels.

        Args:
            delta_time: Elapsed time in seconds.

        Raises:
            RuntimeError: If a moving agent is not a root game object.
        """
        if self.owner is None or self.world is None or not self.moving:
            return
        if self.owner.parent is not None and self.owner.parent.parent is not None:
            raise RuntimeError("navigation agents must be root game objects")

        remaining_distance = self.speed * delta_time
        while remaining_distance > 0 and self.moving:
            target_x, target_y = self.world.grid_to_pixel(
                self.path[self._path_index]
            )
            difference_x = target_x - self.owner.x
            difference_y = target_y - self.owner.y
            distance = hypot(difference_x, difference_y)
            if distance <= remaining_distance:
                self.owner.position = target_x, target_y
                remaining_distance -= distance
                self._path_index += 1
                continue
            self.owner.x += difference_x / distance * remaining_distance
            self.owner.y += difference_y / distance * remaining_distance
            remaining_distance = 0
