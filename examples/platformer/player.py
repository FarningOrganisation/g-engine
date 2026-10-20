"""Player game behavior."""

from math import exp
from collections.abc import Mapping

from g import (
    Script, 
    scene_component, 
    PlatformerMotor, 
    requires_components, 
    Key, 
    Area,
    GameObject,
    PhysicsBody,
    Vec2,
    SpriteAnimator,
    SpriteRenderer
)


@requires_components(PlatformerMotor, auto_add=True)
@scene_component("player.Player", display_name="Player")
class Player(Script):
    """Behavior attached to a game object."""

    def on_ready(self) -> None:
        self.platformer_motor = self.game_object.require_component(PlatformerMotor)
        self.visual = self.game_object.require_child("Visual")
        self.animator = self.visual.require_component(SpriteAnimator)
        self.sprite_renderer = self.visual.require_component(SpriteRenderer)
        self.fell_area = self.game_object.require_layer().require_child("PlayerFellArea").require_component(Area)
        self.spawn_position = self.game_object.position
        self.physics_body = self.game_object.require_component(PhysicsBody)
        self.fell_area.on_enter(self._on_fell_area_enter)

    def update(self, delta_time: float) -> None:
        self.platformer_motor.move(self.input.get_axis("x"))
        if any(self.input.just_pressed(key) for key in (Key.W, Key.SPACE)):
            self.platformer_motor.jump()
        target_angle = self.platformer_motor.ground_angle
        tilt_weight = 1 - exp(-8 * delta_time)
        self.visual.rotation += (target_angle - self.visual.rotation) * tilt_weight

        if self.platformer_motor.grounded:
            relative_x, relative_y = self.platformer_motor.relative_velocity
            if abs(relative_x) < 0.01 and abs(relative_y) < 0.01:
                self.animator.play("idle")
            else:
                self.sprite_renderer.flip_x = relative_x < 0
                self.animator.play("run")
        else:
            self.animator.play("jump")
            self.sprite_renderer.flip_x = self.input.get_axis("x") < 0

    def reset(self):
        self.game.scenes.restart()

    def save_state(self) -> Mapping[str, object]:
        return {"spawn_position": self.spawn_position}

    def load_state(self, state: Mapping[str, object]) -> None:
        position = state.get("spawn_position")
        if not isinstance(position, Vec2):
            raise ValueError("Invalid Player checkpoint spawn_position")
        self.spawn_position = position.copy()

    def _on_fell_area_enter(self, other: GameObject):
        if other is not self.game_object:
            return
        self.reset()

        
