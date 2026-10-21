import g

SPEED = 300
B_SPEED  = 1000

spiel = g.Game(1200, 800)

spieler = spiel.add_circle(600, 400, g.colors.WHITE, 40)

bullets: list[g.GameObject] = []

def update(delta_time):
    spieler.position.x += spiel.input.get_axis("x") * SPEED * delta_time
    spieler.position.y += spiel.input.get_axis("y") * SPEED * delta_time

    if spiel.input.mouse_just_pressed():
        bullet = spiel.add_circle(spieler.x, spieler.y, g.colors.YELLOW, 10)
        bullet.look_at(spiel.input.mouse_position)
        bullets.append(bullet)

    for bullet in bullets:
        bullet.move(B_SPEED * delta_time)

spiel.run("Game", update=update)