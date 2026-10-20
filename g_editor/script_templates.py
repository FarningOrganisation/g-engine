from __future__ import annotations


SCRIPT_TEMPLATES = ("Empty Script", "Platformer Player", "Top-Down Player")


def script_source(class_name: str, component_type: str, template: str) -> str:
    if template not in SCRIPT_TEMPLATES:
        raise ValueError(f"unknown script template: {template!r}")
    if template == "Empty Script":
        imports = "from g import Script, scene_component"
        requirement = ""
        body = (
            "    def update(self, delta_time: float) -> None:\n"
            "        pass\n"
        )
    else:
        platformer = template == "Platformer Player"
        motor = "PlatformerMotor" if platformer else "TopDownMotor"
        imports = f"from g import Script, scene_component, requires_components, {motor}"
        if platformer:
            imports += ", Key"
        requirement = f"@requires_components({motor}, auto_add=True)\n"
        body = (
            "    def on_ready(self) -> None:\n"
            f"        self.motor = self.game_object.require_component({motor})\n\n"
            "    def update(self, delta_time: float) -> None:\n"
        )
        if platformer:
            body += (
                '        self.motor.move(self.input.get_axis("x"))\n'
                "        if any(self.input.just_pressed(key) for key in (Key.W, Key.SPACE)):\n"
                "            self.motor.jump()\n"
            )
        else:
            body += (
                '        self.motor.move((self.input.get_axis("x"), self.input.get_axis("y")))\n'
            )
    return (
        f'"""{class_name} game behavior."""\n\n'
        f"{imports}\n\n\n"
        f"{requirement}"
        f'@scene_component("{component_type}")\n'
        f"class {class_name}(Script):\n"
        '    """Project-owned behavior attached to a game object."""\n\n'
        f"{body}"
    )
