"""Cone game behavior."""

from g import CollisionContact, Script, scene_component, SpriteRenderer


@scene_component("cone.Cone")
class Cone(Script):
    """Project-owned behavior attached to a game object."""

    def on_ready(self) -> None:
        self.sprite_renderer = self.require_component(SpriteRenderer)

    def update(self, delta_time: float) -> None:
        pass

    def on_collision(self, contact: CollisionContact) -> None:
        self.sprite_renderer.image = self.resources.load_image("./kenney_racing-pack/PNG/Objects/cone_down.png")
