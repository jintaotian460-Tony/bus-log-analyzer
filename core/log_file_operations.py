"""Copy and ZIP operations for the log explorer; never replace an existing item."""
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
import os
import shutil
import stat
import zipfile


class Cancelled(Exception):
    pass


@dataclass
class Result:
    paths: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def unique_path(path):
    path = Path(path)
    if not path.exists():
        return path
    for number in range(1, 10000):
        candidate = path.with_name(f'{path.stem} ({number}){path.suffix}')
        if not candidate.exists():
            return candidate
    raise OSError(f'Could not find a free name for {path.name}')


def _sources(paths):
    paths = list(dict.fromkeys(Path(path).absolute() for path in paths))
    # Selecting a folder and a file inside it must copy/archive that file once.
    return [path for path in paths if not any(other in path.parents for other in paths)]


def _check_source(path):
    if path.is_symlink():
        raise OSError(f'Symbolic links are not supported: {path.name}')
    if not path.exists():
        raise FileNotFoundError(str(path))


def _copy_bytes(source, destination, progress, name):
    while True:
        progress(name)
        block = source.read(1024 * 1024)
        if not block:
            break
        destination.write(block)


def _copy_path(source, destination, progress):
    _check_source(source)
    progress(source.name)
    if source.is_dir():
        destination.mkdir()
        for child in source.iterdir():
            _copy_path(child, destination / child.name, progress)
    else:
        with source.open('rb') as input_file, destination.open('xb') as output_file:
            _copy_bytes(input_file, output_file, progress, source.name)
    shutil.copystat(source, destination)


def _remove_partial(path):
    # Callers only pass the exclusively created output, never an input or an
    # existing destination. A failed operation leaves no half-written log.
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def paste(paths, directory, progress=lambda _name: None):
    result = Result()
    directory = Path(directory).resolve()
    for source in _sources(paths):
        destination = None
        created = False
        try:
            _check_source(source)
            resolved = source.resolve()
            if source.is_dir() and (directory == resolved or resolved in directory.parents):
                raise OSError(f'Cannot paste {source.name} into itself or its subfolder')
            destination = unique_path(directory / source.name)
            # The copy helpers use exclusive creation. If another application
            # claims this name first, it must not be removed during rollback.
            if source.is_dir():
                destination.mkdir()
                created = True
                for child in source.iterdir():
                    _copy_path(child, destination / child.name, progress)
                shutil.copystat(source, destination)
            else:
                output = destination.open('xb')
                created = True
                try:
                    with source.open('rb') as input_file, output:
                        _copy_bytes(input_file, output, progress, source.name)
                    shutil.copystat(source, destination)
                except BaseException:
                    output.close()
                    raise
            result.paths.append(destination)
        except (OSError, Cancelled) as exc:
            if created:
                _remove_partial(destination)
            if isinstance(exc, Cancelled):
                result.cancelled = True
                break
            result.errors.append(f'{source.name}: {exc}')
    return result


def _write_entry(archive, path, base, progress):
    _check_source(path)
    progress(path.name)
    name = path.relative_to(base).as_posix()
    if path.is_dir():
        archive.write(path, name + '/')
        for child in path.iterdir():
            _write_entry(archive, child, base, progress)
    else:
        info = zipfile.ZipInfo.from_file(path, name)
        info.compress_type = zipfile.ZIP_DEFLATED
        with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as target:
            _copy_bytes(source, target, progress, path.name)


def compress(paths, progress=lambda _name: None):
    result = Result()
    paths = _sources(paths)
    if not paths:
        return result
    stem = paths[0].name if len(paths) == 1 else paths[0].parent.name
    destination = unique_path(paths[0].parent / f'{stem}.zip')
    created = False
    try:
        base = Path(os.path.commonpath([str(path.parent) for path in paths]))
        with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
            created = True
            for path in paths:
                _write_entry(archive, path, base, progress)
        result.paths.append(destination)
    except (OSError, ValueError, RuntimeError, Cancelled) as exc:
        if created:
            _remove_partial(destination)
        if isinstance(exc, Cancelled):
            result.cancelled = True
        else:
            result.errors.append(f'{stem}: {exc}')
    return result


def _zip_target(root, member):
    name = member.filename.replace('\\', '/')
    windows = PureWindowsPath(name)
    parts = name.split('/')
    if (windows.drive or name.startswith('/') or '..' in parts
            or any(':' in part or (part and (part.endswith((' ', '.'))
                                            or PureWindowsPath(part).is_reserved())) for part in parts)
            or stat.S_ISLNK(member.external_attr >> 16)):
        raise OSError(f'unsafe entry: {member.filename}')
    target = (root / name).resolve()
    if target != root and root not in target.parents:
        raise OSError(f'unsafe entry: {member.filename}')
    return target


def extract(archive_path, progress=lambda _name: None):
    archive_path = Path(archive_path)
    result = Result()
    destination = unique_path(archive_path.parent / archive_path.stem)
    created = False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            root = destination.resolve()
            entries = [(member, _zip_target(root, member)) for member in archive.infolist()]
            destination.mkdir()
            created = True
            for member, target in entries:
                progress(member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open('xb') as output:
                        _copy_bytes(source, output, progress, member.filename)
        result.paths.append(destination)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, Cancelled) as exc:
        if created:
            _remove_partial(destination)
        if isinstance(exc, Cancelled):
            result.cancelled = True
        else:
            result.errors.append(f'{archive_path.name}: {exc}')
    return result
