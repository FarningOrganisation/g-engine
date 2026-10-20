from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar
from ..inspector import inspector_field

if TYPE_CHECKING:
    from ..behaviours import AnimationBuilder
    from ..nodes import GameObject

class Component:
    """Base unit of behavior attached to a game object.

    Attributes:
        enabled: Whether the component participates in updates and rendering.
        owner: The attached game object, or ``None`` before attachment.
    """
    def save_state(self) -> Mapping[str, object]:
        """Return runtime checkpoint state; ordinary scene saves omit it."""
        return {}

    def load_state(self, state: Mapping[str, object]) -> None:
        """Restore runtime checkpoint state.

        Args:
            state: State previously returned by :meth:`save_state`.

        Raises:
            ValueError: The subclass does not accept non-empty checkpoint state.
        """
        if state:
            raise ValueError(f"{type(self).__name__} must implement load_state for its checkpoint state")

    __inspector_fields__ = {
        "enabled": inspector_field(True, tooltip="Enable this component's behavior."),
    }
    def __init__(self, *, enabled: bool = True) -> None:
        """Initialize an unattached component.

        Args:
            enabled: Whether the component's behavior is enabled.
        """
        self.enabled = enabled
        self._owner: GameObject | None = None
        self._ready = False

    @property
    def owner(self) -> GameObject | None:
        """Game object to which this component is attached, if any."""
        return self._owner

    @property
    def game_object(self) -> GameObject:
        """Return the owning game object.

        Raises:
            RuntimeError: The component is not attached.
        """
        if self.owner is None:
            raise RuntimeError("component is not attached to a game object")
        return self.owner

    def get_component(self, component_type: type[ComponentType]) -> ComponentType | None:
        """Find an attached component of the requested type.

        Args:
            component_type: Component class to find.

        Returns:
            The first matching component, or ``None`` if none is attached.
        """
        return self.game_object.get_component(component_type)

    def require_component(self, component_type: type[ComponentType]) -> ComponentType:
        """Find a required attached component.

        Args:
            component_type: Component class to find.

        Returns:
            The matching component.

        Raises:
            LookupError: No matching component is attached.
        """
        return self.game_object.require_component(component_type)

    def get_child(self, name: str, *, recursive: bool = False) -> GameObject | None:
        """Find a child game object by name.

        Args:
            name: Child name to search for.
            recursive: Include descendants beyond direct children.
        """
        return self.game_object.get_child(name, recursive=recursive)

    def require_child(self, name: str, *, recursive: bool = False) -> GameObject:
        """Find a named child or raise if it is absent.

        Args:
            name: Child name to search for.
            recursive: Include descendants beyond direct children.
        """
        return self.game_object.require_child(name, recursive=recursive)

    def get_children(self, *, recursive: bool = False) -> tuple[GameObject, ...]:
        """Return child game objects.

        Args:
            recursive: Include all descendants when true.
        """
        return self.game_object.get_children(recursive=recursive)

    def on_attach(self) -> None:
        """Run after this component is attached to a game object."""
        pass

    def on_detach(self) -> None:
        """Run when this component is detached from its game object."""
        pass

    def on_scene_detach(self) -> None:
        """Run before the owning object is non-destructively detached.

        The owner still has access to its current scene while this hook runs.
        Reattaching the object makes the component ready again.
        """
        pass

    def on_ready(self) -> None:
        """Run when the owning object becomes ready in a scene."""
        pass

    def update(self, delta_time: float) -> None:
        """Advance component behavior by the simulation time step.

        Args:
            delta_time: Elapsed simulation time in seconds.
        """
        del delta_time

    def animate(self) -> AnimationBuilder:
        """Create an animation builder targeting the owning game object.

        Raises:
            RuntimeError: The component is not attached.
        """
        from ..behaviours import AnimationBuilder

        if self.owner is None:
            raise RuntimeError("component must be attached before animating")
        return AnimationBuilder(self.owner, self)

    def _mark_ready(self) -> None:
        if not self._ready:
            self.on_ready()
            self._ready = True

ComponentType = TypeVar("ComponentType", bound=Component)
RequiredComponent = TypeVar("RequiredComponent", bound=type[Component])


@dataclass(frozen=True)
class ComponentRequirement:
    """A set of component types required by another component.

    Attributes:
        component_types: Types that satisfy the requirement.
        any_of: Whether at least one type, rather than all, is required.
        auto_add: Whether missing types may be added automatically.
    """
    component_types: tuple[type[Component], ...]
    any_of: bool = False
    auto_add: bool = False


def requires_components(
    *component_types: type[Component],
    auto_add: bool = False,
) -> Callable[[RequiredComponent], RequiredComponent]:
    """Declare component types that must accompany a decorated component.

    Args:
        component_types: Component classes all required on the same object.
        auto_add: Whether the engine may add missing required components.

    Raises:
        ValueError: No component types were supplied.
        TypeError: A supplied type is not a Component subclass.
    """
    return _component_requirement(
        ComponentRequirement(component_types, auto_add=auto_add)
    )


def requires_any_component(
    *component_types: type[Component],
) -> Callable[[RequiredComponent], RequiredComponent]:
    """Declare alternative component types for a decorated component.

    Args:
        component_types: Component classes; at least one must be present.

    Raises:
        ValueError: No component types were supplied.
        TypeError: A supplied type is not a Component subclass.
    """
    return _component_requirement(
        ComponentRequirement(component_types, any_of=True)
    )


def component_requirements(
    component_type: type[Component],
) -> tuple[ComponentRequirement, ...]:
    """Return the requirements declared by a component class.

    Args:
        component_type: Component class to inspect.
    """
    return getattr(component_type, "__component_requirements__", ())


def missing_component_requirements(
    component_type: type[Component],
    present_types: tuple[type[Component], ...],
    *,
    include_auto_add: bool = True,
) -> tuple[ComponentRequirement, ...]:
    """Find unsatisfied declared requirements for present component types.

    Args:
        component_type: Component class whose requirements are checked.
        present_types: Component classes currently present on the object.
        include_auto_add: Include requirements marked for automatic addition.
    """
    missing: list[ComponentRequirement] = []
    for requirement in component_requirements(component_type):
        if not include_auto_add and requirement.auto_add:
            continue
        matches = tuple(
            required
            for required in requirement.component_types
            if any(
                issubclass(present, required)
                for present in present_types
            )
        )
        satisfied = bool(matches) if requirement.any_of else (
            len(matches) == len(requirement.component_types)
        )
        if not satisfied:
            missing.append(requirement)
    return tuple(missing)


def describe_component_requirement(
    requirement: ComponentRequirement,
) -> str:
    """Format a component requirement as a readable type-name expression.

    Args:
        requirement: Requirement to describe.
    """
    names = [item.__name__ for item in requirement.component_types]
    if requirement.any_of:
        return " or ".join(names)
    return ", ".join(names)


def _component_requirement(
    requirement: ComponentRequirement,
) -> Callable[[RequiredComponent], RequiredComponent]:
    if not requirement.component_types:
        raise ValueError("at least one required component type is needed")
    if requirement.any_of and requirement.auto_add:
        raise ValueError("alternative component requirements cannot auto-add")
    if not all(
        isinstance(component_type, type)
        and issubclass(component_type, Component)
        for component_type in requirement.component_types
    ):
        raise TypeError("required component types must extend Component")

    def decorate(
        component_type: RequiredComponent,
    ) -> RequiredComponent:
        inherited = component_requirements(component_type)
        setattr(
            component_type,
            "__component_requirements__",
            (*inherited, requirement),
        )
        return component_type

    return decorate
