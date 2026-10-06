"""Offline spatial queries; canonical writes belong exclusively to Unreal."""
import hashlib
from functools import lru_cache
from pathlib import Path


def file_signature(path):
    s=Path(path).stat()
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns


def native_library_path(engine_directory, library):
    """Legacy Win64 exports used an engine-binary-relative module filename."""
    path=Path(library)
    if path.is_absolute():return path
    engine=Path(engine_directory)
    if not engine.is_absolute():raise ValueError('Absolute engine directory required for legacy native library path')
    return (engine/'Binaries/Win64'/path).resolve()


@lru_cache(maxsize=128)
def _source_digest(path,signature):
    return hashlib.sha256(path.read_bytes()).digest()


def _runtime_fingerprint():
    digest=hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(path.name.encode());digest.update(_source_digest(path,file_signature(path)))
    return digest.hexdigest()


_LOADED_FINGERPRINT=_runtime_fingerprint()


def require_current_runtime():
    try:current=_runtime_fingerprint()
    except OSError as error:raise ValueError('Spatial Twin source unavailable; restart MCP after deployment') from error
    if current!=_LOADED_FINGERPRINT:
        raise ValueError('Spatial Twin source changed since process startup; restart MCP before continuing')
    return current
