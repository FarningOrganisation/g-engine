# Runtime Splines

`g.Spline` is a component for editable 2D paths. It owns an immutable snapshot
of its local control points and exposes a cached `CatmullRomSpline` through
`curve`. `world_curve` transforms those points through the complete owning
object hierarchy and is refreshed when the geometry or world transform changes.
Nonuniform scaling transforms the original local curve, rather than refitting
its control points, so `world_curve.point_at(t)` preserves the original
parameterization. Its distance table is measured in transformed world units.

```python
from g import GameObject, Spline

path = GameObject().add_component(Spline())
path.points = ((0, 0), (80, 60), (160, 0))
position = path.point_at_distance(40)
direction = path.tangent_at_distance(40)
```

The default is an open path through `(-64, 0)`, `(0, 64)`, and `(64, 0)`.
Open paths need at least two points and reflect their endpoints for tangent
calculation. Closed paths need at least three points; the closing segment is
implicit, so the first point must not be repeated at the end. Adjacent
coincident points are rejected. `tolerance` is a finite positive bound in
world/local curve units for adaptive arc-length sampling (default `0.5`).

`point_at(t)` and `tangent_at(t)` use a global parameter in `[0, 1]`; open
curves clamp outside that range and closed curves wrap. Distance-based methods
use curve length units, clamp on open curves, and wrap on closed curves. The
`tangent_at` methods return normalized direction vectors and raise `ValueError`
at an exact zero-derivative cusp, where a unique direction does not exist.
Nonfinite queries, coordinates, or tolerances are rejected; transforms that
collapse adjacent points cannot create a world curve. The curve exposes
matching `sampled_points` and `sample_parameters` tuples, as
well as `sample()` pairs, for editor segment hit testing.

Splines serialize as the editable `g.spline` component in the `Movement`
category, including points, closed state, tolerance, and component enabled
state.
