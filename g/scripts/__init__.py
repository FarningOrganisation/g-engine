"""Built-in gameplay behaviors composed from core engine components."""

from .camera_follow import CameraFollow
from .platformer_motor import PlatformerMotor
from .top_down_motor import TopDownMotor

__all__ = ["CameraFollow", "PlatformerMotor", "TopDownMotor"]
