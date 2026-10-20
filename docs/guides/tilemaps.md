# Tile maps and navigation

World layers can contain object hierarchies or compact tile data. Individual
tiles are not `GameObject` instances.

```python
from g import Image, TileFlags, TileSet

# Assume scene exists.
terrain = TileSet(Image("assets/terrain.png"), 16)
ground = scene.layers.add_tile("Ground", terrain, chunk_size=16)
ground.set_tile(2, 4, 0)
ground.set_tile(3, 4, 0, flags=TileFlags.FLIP_HORIZONTAL)
cell = ground.tile_at(3, 4)
ground.set_tile(2, 4, None)
```

Coordinates can be negative. Cells use layer-global tile IDs; tilesets occupy
stable non-overlapping ranges. Removing a tileset does not renumber another
tileset's range.

Tile rendering uses chunk meshes and camera culling. Editing one tile
rebuilds the affected chunk rather than constructing a game object per cell.

## Editor authoring

Use the Tilemap panel to configure tilesets and the Tile Palette to select
tiles, stamps, and metadata. Pencil, rectangle fill, eraser, random painting,
rotation, and flips support rapid level building.

The editor can generate static collision geometry using full cells, existing
metadata, or alpha-derived shapes. Adjacent full solid cells can be merged
into rectangles.

## Navigation

Grid navigation and physics answer different questions: a path selects a
route, while physics handles contact and motion.

`NavigationWorld` holds traversability and pathfinding state.
`NavigationAgent` follows routes, and `NavigationObstacle` marks obstacles.
Tiles can carry navigation metadata; see the
[tile-map API](../api/tilemap.md) and
[navigation API](../api/navigation.md) for generation and update behavior.

Validate pathfinding with the actual cell size, movement model, and obstacle
layout used by the game. A valid route alone does not guarantee a physics
body can squeeze through every passage.
