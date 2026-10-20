import unittest

from g import Component, GameObject, Script, SpriteRenderer


class ComponentLookupTests(unittest.TestCase):
    def test_component_lookup_is_owner_only_and_preserves_identity(self) -> None:
        for cls in (Component, Script):
            with self.subTest(component=cls.__name__):
                owner = GameObject()
                owner.name = "Owner"
                component = owner.add_component(cls())
                renderer = owner.add_component(SpriteRenderer())
                child = GameObject()
                child.name = "Child"
                owner.add_child(child)
                child.add_component(Script())
                self.assertIs(component.game_object, owner)
                self.assertIs(component.get_component(SpriteRenderer), renderer)
                self.assertIs(component.require_component(SpriteRenderer), renderer)
                self.assertIs(component.require_component(cls), component)
                if cls is Component:
                    self.assertIsNone(component.get_component(Script))
                    with self.assertRaisesRegex(LookupError, "Owner.*Script"):
                        component.require_component(Script)

    def test_child_lookup_preserves_scope_order_and_required_errors(self) -> None:
        for cls in (Component, Script):
            with self.subTest(component=cls.__name__):
                owner = GameObject()
                owner.name = "Owner"
                component = owner.add_component(cls())
                first = GameObject()
                second = GameObject()
                nested = GameObject()
                first.name = second.name = "Visual"
                nested.name = "Nested"
                owner.add_child(first)
                owner.add_child(second)
                first.add_child(nested)
                self.assertIs(component.get_child("Visual"), first)
                self.assertIs(component.require_child("Visual"), first)
                self.assertEqual(component.get_children(), (first, second))
                self.assertEqual(component.get_children(recursive=True), (first, nested, second))
                self.assertIsNone(component.get_child("Nested"))
                self.assertIs(component.require_child("Nested", recursive=True), nested)
                self.assertIsNone(component.get_child("Owner", recursive=True))
                with self.assertRaisesRegex(LookupError, "Owner.*child.*Nested"):
                    component.require_child("Nested")
                with self.assertRaisesRegex(LookupError, "Owner.*descendant.*Missing"):
                    component.require_child("Missing", recursive=True)

    def test_unattached_and_detached_lookups_raise_explicit_errors(self) -> None:
        for cls in (Component, Script):
            with self.subTest(component=cls.__name__):
                component = cls()
                owner = GameObject()
                for attached_then_removed in (False, True):
                    if attached_then_removed:
                        owner.add_component(component)
                        owner.remove_component(component)
                    for lookup in (
                        lambda: component.game_object,
                        lambda: component.get_component(SpriteRenderer),
                        lambda: component.require_component(SpriteRenderer),
                        lambda: component.get_child("Visual"),
                        lambda: component.require_child("Visual"),
                        lambda: component.get_children(),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "not attached"):
                            lookup()
