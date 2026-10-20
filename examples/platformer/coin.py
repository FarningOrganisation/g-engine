"""Coin game behavior."""

from g import Script, scene_component, Area, GameObject


@scene_component("coin.Coin")
class Coin(Script):
    """Project-owned behavior attached to a game object."""

    def on_ready(self) -> None:
        self.area = self.game_object.require_component(Area)
        self.area.on_enter(self._on_enter_area)
        self.coin_sound = self.resources.load_sound("coin.mp3")

    def update(self, delta_time: float) -> None:
        pass

    def _on_enter_area(self, other: GameObject):
        self.coin_sound.play()
        if other.name == "Player":         
            self.game_object.delete()

