from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from os import PathLike
from pathlib import Path
from struct import unpack
from types import MappingProxyType
from typing import Literal, TypeAlias, overload

import pyglet
from fontTools.ttLib import TTFont
from pyglet import gl
from pyglet.image import AbstractImage as PygletImage
from pyglet.image.animation import Animation as PygletAnimation
from pyglet.image.animation import AnimationFrame as PygletAnimationFrame
from pyglet.media import Player as PygletPlayer
from pyglet.media import Source as PygletSource


Color: TypeAlias = tuple[int, int, int] | tuple[int, int, int, int]
ResourceKind: TypeAlias = Literal["image", "animation", "sound", "font"]


class TextureFilter(str, Enum):
    """Texture sampling used when images are scaled."""

    LINEAR = "linear"
    NEAREST = "nearest"


def _apply_texture_filter(
    source: PygletImage,
    filtering: TextureFilter,
) -> None:
    texture = source.get_texture()
    value = (
        gl.GL_NEAREST
        if filtering is TextureFilter.NEAREST
        else gl.GL_LINEAR
    )
    previous = gl.GLint()
    gl.glGetIntegerv(gl.GL_TEXTURE_BINDING_2D, previous)
    try:
        gl.glBindTexture(texture.target, texture.id)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MIN_FILTER, value)
        gl.glTexParameteri(texture.target, gl.GL_TEXTURE_MAG_FILTER, value)
    finally:
        gl.glBindTexture(gl.GL_TEXTURE_2D, previous.value)


class Font:
    """A font file registered for text rendering.

    Attributes:
        family: Font family name read from the font metadata.
        style: Subfamily name, defaulting to ``"Regular"`` when absent.
    """
    def __init__(
        self,
        filename: str | PathLike[str],
        *,
        presentation_enabled: bool = True,
    ) -> None:
        """Load font metadata and optionally register the file with pyglet.

        Args:
            filename: Path to a supported font file.
            presentation_enabled: Register the font with pyglet when true.

        Raises:
            ValueError: The font has no family name.
        """
        self._filename = Path(filename).expanduser().resolve()
        with TTFont(self._filename, lazy=True) as font:
            names = font["name"]
            family = names.getBestFamilyName()
            style = names.getBestSubFamilyName()
        if family is None:
            raise ValueError(f"font has no family name: {filename}")
        self.family = family
        self.style = style or "Regular"
        if presentation_enabled:
            pyglet.font.add_file(str(self._filename))

    @property
    def filename(self) -> Path:
        """Resolved path to the font file."""
        return self._filename


def clamp_texture_edges(texture: pyglet.image.Texture) -> None:
    previous = gl.GLint()
    gl.glGetIntegerv(gl.GL_TEXTURE_BINDING_2D, previous)
    try:
        gl.glBindTexture(texture.target, texture.id)
        gl.glTexParameteri(
            texture.target,
            gl.GL_TEXTURE_WRAP_S,
            gl.GL_CLAMP_TO_EDGE,
        )
        gl.glTexParameteri(
            texture.target,
            gl.GL_TEXTURE_WRAP_T,
            gl.GL_CLAMP_TO_EDGE,
        )
    finally:
        gl.glBindTexture(gl.GL_TEXTURE_2D, previous.value)


class Sound:
    """A sound asset loaded for playback."""
    def __init__(
        self,
        filename: str | PathLike[str],
        *,
        presentation_enabled: bool = True,
    ) -> None:
        """Load a sound source, or retain metadata for headless use.

        Args:
            filename: Sound file path.
            presentation_enabled: Whether to load a pyglet media source.
        """
        self._filename = Path(filename).expanduser().resolve()
        self._source: PygletSource | None = (
            pyglet.media.load(str(filename), streaming=False)
            if presentation_enabled else None
        )

    def play(
        self,
        volume: float = 1.0,
        pitch: float = 1.0,
        loop: bool = False,
    ) -> PygletPlayer:
        """Start playback with the requested volume, pitch, and looping mode.

        Args:
            volume: Playback volume.
            pitch: Playback pitch multiplier.
            loop: Whether playback repeats.

        Returns:
            The active pyglet player, or a silent no-op player in headless mode.
        """
        if self._source is None:
            return _SilentPlayer()  # type: ignore[return-value]
        player = self._source.play()
        player.volume = volume
        player.pitch = pitch
        player.loop = loop
        return player


class Image:
    """A static image asset with dimensions available in headless mode."""
    def __init__(
        self,
        filename: str | PathLike[str],
        *,
        filtering: TextureFilter = TextureFilter.LINEAR,
        presentation_enabled: bool = True,
    ) -> None:
        """Load an image or read its metadata without creating a presentation.

        Args:
            filename: Image file path.
            filtering: Sampling used when the image is scaled.
            presentation_enabled: Whether to decode the image with pyglet.

        Raises:
            ValueError: Headless mode is used with an unsupported image format.
        """
        self._filename: Path | None = Path(filename).expanduser().resolve()
        self.filtering = TextureFilter(filtering)
        self._width = self._height = 0
        if not presentation_enabled:
            self._width, self._height = _image_dimensions(self._filename)
        self._source = (
            pyglet.image.load(str(filename))
            if presentation_enabled else None
        )

    @classmethod
    def solid(
        cls,
        width: int,
        height: int,
        color: Color,
        *,
        filtering: TextureFilter = TextureFilter.LINEAR,
    ) -> Image:
        """Create an image filled with a solid RGB or RGBA color.

        Args:
            width: Positive image width in pixels.
            height: Positive image height in pixels.
            color: Three- or four-channel pixel color.

        Raises:
            ValueError: Either dimension is not positive.
        """
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        rgba = color if len(color) == 4 else (*color, 255)
        source = pyglet.image.SolidColorImagePattern(rgba).create_image(
            width, height
        )
        return cls._from_source(source, filtering=filtering)

    @classmethod
    def _from_source(
        cls,
        source: PygletImage,
        *,
        filtering: TextureFilter = TextureFilter.LINEAR,
    ) -> Image:
        image = cls.__new__(cls)
        image._source = source
        image._filename = None
        image._width = source.width
        image._height = source.height
        image.filtering = TextureFilter(filtering)
        return image

    @property
    def width(self) -> int:
        """Image width in pixels."""
        return self._width if self._source is None else self._source.width

    @property
    def height(self) -> int:
        """Image height in pixels."""
        return self._height if self._source is None else self._source.height


class Animation:
    """An image animation asset."""
    def __init__(
        self,
        filename: str | PathLike[str],
        *,
        presentation_enabled: bool = True,
    ) -> None:
        """Load an animation file, or retain its path for headless use.

        Args:
            filename: Animation file path.
            presentation_enabled: Whether to decode the animation with pyglet.
        """
        self._filename: Path | None = Path(filename).expanduser().resolve()
        self._source = (
            pyglet.image.load_animation(str(filename))
            if presentation_enabled else None
        )

    @classmethod
    def from_images(
        cls,
        images: Sequence[Image],
        frame_duration: float,
        *,
        loop: bool = True,
    ) -> Animation:
        """Build an animation from static images with a shared frame duration.

        Args:
            images: Ordered animation frames.
            frame_duration: Positive duration for each frame in seconds.
            loop: Whether the final frame transitions back to the first.

        Raises:
            ValueError: No images are supplied or the duration is not positive.
        """
        if not images:
            raise ValueError("images must not be empty")
        if frame_duration <= 0:
            raise ValueError("frame_duration must be positive")
        frames = [
            PygletAnimationFrame(image._source, frame_duration)
            for image in images
        ]
        if not loop:
            frames[-1].duration = None
        return cls._from_source(PygletAnimation(frames))

    @classmethod
    def from_files(
        cls,
        filenames: Sequence[str | PathLike[str]],
        frame_duration: float,
        *,
        loop: bool = True,
    ) -> Animation:
        """Load image files and build an animation from them.

        Args:
            filenames: Ordered image file paths.
            frame_duration: Positive duration for each frame in seconds.
            loop: Whether the animation loops.
        """
        return cls.from_images(
            [Image(filename) for filename in filenames],
            frame_duration,
            loop=loop,
        )

    @classmethod
    def _from_source(cls, source: PygletAnimation) -> Animation:
        animation = cls.__new__(cls)
        animation._source = source
        animation._filename = None
        return animation


@dataclass(frozen=True)
class _ResourceRequest:
    kind: ResourceKind
    name: str
    filename: str | PathLike[str]


class ResourceBundle:
    """A named collection of assets loaded incrementally by a game.

    Use :meth:`ResourceManager.bundle` to create a bundle and
    :meth:`Game.load_assets` to load it without blocking drawing.
    """

    def __init__(
        self,
        manager: ResourceManager,
        requests: Sequence[_ResourceRequest],
    ) -> None:
        """Create a pending bundle for one resource manager."""
        self._manager = manager
        self._requests = tuple(requests)
        self._loaded_count = 0
        self._error: Exception | None = None
        self._images: dict[str, Image] = {}
        self._animations: dict[str, Animation] = {}
        self._sounds: dict[str, Sound] = {}
        self._fonts: dict[str, Font] = {}

    @property
    def images(self) -> Mapping[str, Image]:
        """Loaded images keyed by their declared names."""
        return MappingProxyType(self._images)

    @property
    def animations(self) -> Mapping[str, Animation]:
        """Loaded image animations keyed by their declared names."""
        return MappingProxyType(self._animations)

    @property
    def sounds(self) -> Mapping[str, Sound]:
        """Loaded sounds keyed by their declared names."""
        return MappingProxyType(self._sounds)

    @property
    def fonts(self) -> Mapping[str, Font]:
        """Loaded fonts keyed by their declared names."""
        return MappingProxyType(self._fonts)

    @property
    def loaded_count(self) -> int:
        """Number of assets loaded successfully."""
        return self._loaded_count

    @property
    def total_count(self) -> int:
        """Total number of assets declared in this bundle."""
        return len(self._requests)

    @property
    def progress(self) -> float:
        """Loading progress from ``0.0`` through ``1.0``."""
        if not self._requests:
            return 1.0
        return self._loaded_count / len(self._requests)

    @property
    def loaded(self) -> bool:
        """Whether every declared asset loaded successfully."""
        return self._loaded_count == len(self._requests)

    @property
    def failed(self) -> bool:
        """Whether loading stopped because an asset raised an error."""
        return self._error is not None

    @property
    def error(self) -> Exception | None:
        """Error raised by the most recent failed asset load."""
        return self._error

    @property
    def current_name(self) -> str | None:
        """Name of the next asset, or ``None`` when loading is complete."""
        if self.loaded:
            return None
        return self._requests[self._loaded_count].name

    def _load_next(self) -> None:
        if self.loaded:
            return
        request = self._requests[self._loaded_count]
        try:
            if request.kind == "image":
                self._images[request.name] = self._manager.load_image(
                    request.filename
                )
            elif request.kind == "animation":
                self._animations[request.name] = (
                    self._manager.load_animation(request.filename)
                )
            elif request.kind == "sound":
                self._sounds[request.name] = self._manager.load_sound(
                    request.filename
                )
            else:
                self._fonts[request.name] = self._manager.load_font(
                    request.filename
                )
        except Exception as error:
            self._error = error
            raise
        self._error = None
        self._loaded_count += 1


class ResourceManager:
    """Resolve and cache resource assets under a configurable asset root."""
    def __init__(
        self,
        asset_root: str | PathLike[str] | None = None,
        *,
        texture_filter: TextureFilter = TextureFilter.LINEAR,
        presentation_enabled: bool = True,
    ) -> None:
        """Create an asset cache.

        Args:
            asset_root: Base directory for relative asset paths; defaults to
                the current working directory.
            texture_filter: Default sampling used for loaded images.
            presentation_enabled: Whether loaded assets create pyglet objects.
        """
        self.presentation_enabled = presentation_enabled
        self.texture_filter = TextureFilter(texture_filter)
        self.asset_root = asset_root if asset_root is not None else Path.cwd()
        self._images: dict[tuple[str, TextureFilter], Image] = {}
        self._animations: dict[str, Animation] = {}
        self._sounds: dict[str, Sound] = {}
        self._fonts: dict[str, Font] = {}

    @property
    def asset_root(self) -> Path:
        """Resolved root directory used to resolve relative resource paths."""
        return self._asset_root

    @asset_root.setter
    def asset_root(self, value: str | PathLike[str]) -> None:
        """Set the asset root to a resolved filesystem path."""
        self._asset_root = Path(value).expanduser().resolve()

    def _resource_path(self, filename: str | PathLike[str]) -> Path:
        return (self.asset_root / Path(filename).expanduser()).resolve()

    def bundle(
        self,
        *,
        images: Mapping[str, str | PathLike[str]] | None = None,
        animations: Mapping[str, str | PathLike[str]] | None = None,
        sounds: Mapping[str, str | PathLike[str]] | None = None,
        fonts: Mapping[str, str | PathLike[str]] | None = None,
    ) -> ResourceBundle:
        """Declare named assets for automatic incremental loading.

        Asset names must be non-empty and unique across all categories.
        Paths use the same asset root as the individual loading methods.
        """
        requests: list[_ResourceRequest] = []
        names: set[str] = set()
        categories: tuple[
            tuple[
                ResourceKind,
                Mapping[str, str | PathLike[str]] | None,
            ],
            ...,
        ] = (
            ("image", images),
            ("animation", animations),
            ("sound", sounds),
            ("font", fonts),
        )
        for kind, assets in categories:
            for name, filename in (assets or {}).items():
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("asset names must be non-empty strings")
                if name in names:
                    raise ValueError(f"duplicate asset name: {name}")
                if not isinstance(filename, (str, PathLike)):
                    raise TypeError(
                        f"asset path for {name!r} must be path-like"
                    )
                names.add(name)
                requests.append(
                    _ResourceRequest(kind, name, filename)
                )
        return ResourceBundle(self, requests)

    def load_image(
        self,
        filename: str | PathLike[str],
        *,
        filtering: TextureFilter | None = None,
    ) -> Image:
        """Load an image or return its cached instance.

        Args:
            filename: Path relative to :attr:`asset_root` unless absolute.
            filtering: Sampling override; defaults to :attr:`texture_filter`.
        """
        path = self._resource_path(filename)
        resolved_filter = self.texture_filter if filtering is None else TextureFilter(filtering)
        key = str(path), resolved_filter
        image = self._images.get(key)
        if image is None:
            options: dict[str, object] = {}
            if resolved_filter is not TextureFilter.LINEAR:
                options["filtering"] = resolved_filter
            if not self.presentation_enabled:
                options["presentation_enabled"] = False
            image = Image(path, **options)
            self._images[key] = image
        return image

    def load_animation(self, filename: str | PathLike[str]) -> Animation:
        """Load an animation asset or return its cached instance.

        Args:
            filename: Path relative to :attr:`asset_root` unless absolute.
        """
        path = self._resource_path(filename)
        key = str(path)
        animation = self._animations.get(key)
        if animation is None:
            animation = (
                Animation(path)
                if self.presentation_enabled
                else Animation(path, presentation_enabled=False)
            )
            self._animations[key] = animation
        return animation

    def load_sound(self, filename: str | PathLike[str]) -> Sound:
        """Load a sound asset or return its cached instance.

        Args:
            filename: Path relative to :attr:`asset_root` unless absolute.
        """
        path = self._resource_path(filename)
        key = str(path)
        sound = self._sounds.get(key)
        if sound is None:
            sound = (
                Sound(path)
                if self.presentation_enabled
                else Sound(path, presentation_enabled=False)
            )
            self._sounds[key] = sound
        return sound

    def load_font(self, filename: str | PathLike[str]) -> Font:
        """Load a font asset or return its cached instance.

        Args:
            filename: Path relative to :attr:`asset_root` unless absolute.
        """
        path = self._resource_path(filename)
        key = str(path)
        font = self._fonts.get(key)
        if font is None:
            font = Font(
                path,
                presentation_enabled=self.presentation_enabled,
            )
            self._fonts[key] = font
        return font

    def unload(self, filename: str | PathLike[str]) -> None:
        """Remove a path's cached image, animation, sound, and font."""
        key = str(self._resource_path(filename))
        self._images = {
            image_key: image
            for image_key, image in self._images.items()
            if image_key[0] != key
        }
        self._animations.pop(key, None)
        self._sounds.pop(key, None)
        self._fonts.pop(key, None)

    def clear(self) -> None:
        """Clear every resource cache."""
        self._images.clear()
        self._animations.clear()
        self._sounds.clear()
        self._fonts.clear()


ImageSource: TypeAlias = Image | Animation


@overload
def source_of(image: Image) -> PygletImage:
    ...


@overload
def source_of(image: Animation) -> PygletAnimation:
    ...


def source_of(
    image: ImageSource,
) -> PygletImage | PygletAnimation | None:
    source = image._source
    if isinstance(image, Image) and source is not None:
        _apply_texture_filter(source, image.filtering)
    return source


def center_source(image: ImageSource) -> None:
    source = image._source
    if source is None:
        return
    frames = (
        (frame.image for frame in source.frames)
        if isinstance(source, PygletAnimation)
        else (source,)
    )
    for frame_image in frames:
        frame_image.anchor_x = frame_image.width // 2
        frame_image.anchor_y = frame_image.height // 2


class _SilentPlayer:
    volume = 1.0
    pitch = 1.0
    loop = False

    def pause(self) -> None:
        pass

    def delete(self) -> None:
        pass


def _image_dimensions(filename: Path) -> tuple[int, int]:
    with filename.open("rb") as source:
        header = source.read(24)
    if (
        len(header) >= 24
        and header[:8] == b"\x89PNG\r\n\x1a\n"
        and header[12:16] == b"IHDR"
    ):
        return unpack(">II", header[16:24])
    raise ValueError(
        f"headless image metadata supports PNG assets only: {filename}"
    )
