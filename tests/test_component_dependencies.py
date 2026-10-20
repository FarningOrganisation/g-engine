import unittest

from g import (
    Component,
    GameObject,
    requires_any_component,
    requires_components,
)


class RequiredComponent(Component):
    pass


class AlternativeComponent(Component):
    pass


@requires_components(RequiredComponent)
class HardDependent(Component):
    pass


@requires_components(RequiredComponent, auto_add=True)
class AutoDependent(Component):
    pass


@requires_any_component(RequiredComponent, AlternativeComponent)
class AlternativeDependent(Component):
    pass


class ComponentDependencyTests(unittest.TestCase):
    def test_hard_requirement_is_enforced(self) -> None:
        game_object = GameObject()
        dependent = HardDependent()

        with self.assertRaisesRegex(
            ValueError,
            "HardDependent requires RequiredComponent",
        ):
            game_object.add_component(dependent)

        self.assertIsNone(dependent.owner)
        self.assertEqual(game_object.components, ())

    def test_safe_requirement_is_automatically_added(self) -> None:
        game_object = GameObject()

        dependent = game_object.add_component(AutoDependent())

        self.assertIsNotNone(
            game_object.get_component(RequiredComponent)
        )
        self.assertIs(dependent.owner, game_object)

    def test_alternative_requirement_accepts_either_type(self) -> None:
        game_object = GameObject()
        game_object.add_component(AlternativeComponent())

        game_object.add_component(AlternativeDependent())

        self.assertIsNotNone(
            game_object.get_component(AlternativeDependent)
        )

    def test_required_component_cannot_be_removed(self) -> None:
        game_object = GameObject()
        required = game_object.add_component(RequiredComponent())
        game_object.add_component(HardDependent())

        with self.assertRaisesRegex(
            ValueError,
            "cannot remove RequiredComponent",
        ):
            game_object.remove_component(required)

        self.assertIs(required.owner, game_object)

    def test_require_component_returns_attached_component(self) -> None:
        game_object = GameObject()
        required = game_object.add_component(RequiredComponent())

        self.assertIs(
            game_object.require_component(RequiredComponent),
            required,
        )

    def test_require_component_raises_descriptive_lookup_error(self) -> None:
        game_object = GameObject()
        game_object.name = "Player"

        with self.assertRaisesRegex(
            LookupError,
            "GameObject 'Player' does not have component RequiredComponent",
        ):
            game_object.require_component(RequiredComponent)

    def test_auto_add_dependency_cycle_is_rejected(self) -> None:
        class First(Component):
            pass

        class Second(Component):
            pass

        requires_components(Second, auto_add=True)(First)
        requires_components(First, auto_add=True)(Second)
        game_object = GameObject()

        with self.assertRaisesRegex(
            ValueError,
            "component dependency cycle",
        ):
            game_object.add_component(First())

        self.assertEqual(game_object.components, ())

    def test_failed_attachment_rolls_back_auto_dependencies(self) -> None:
        @requires_components(RequiredComponent, auto_add=True)
        class FailingDependent(Component):
            def on_attach(self) -> None:
                raise RuntimeError("attach failed")

        game_object = GameObject()

        with self.assertRaisesRegex(RuntimeError, "attach failed"):
            game_object.add_component(FailingDependent())

        self.assertEqual(game_object.components, ())


if __name__ == "__main__":
    unittest.main()
