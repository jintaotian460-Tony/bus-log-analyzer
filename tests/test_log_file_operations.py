import zipfile

import pytest

from core import log_file_operations as ops


def test_copy_inside_parent_and_same_directory(tmp_path):
    folder = tmp_path / 'logs'
    folder.mkdir()
    source = folder / '中文 记录.MF4'
    source.write_bytes(b'original samples')
    same = ops.paste([source], folder)
    assert not same.errors
    assert same.paths == [folder / '中文 记录 (1).MF4']
    assert same.paths[0].read_bytes() == source.read_bytes()
    parent = ops.paste([folder], tmp_path)
    assert not parent.errors
    assert parent.paths == [tmp_path / 'logs (1)']
    assert (parent.paths[0] / source.name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize('nested', [False, True])
def test_cannot_paste_folder_into_itself_or_descendant(tmp_path, nested):
    source = tmp_path / 'case'
    child = source / 'nested'
    child.mkdir(parents=True)
    result = ops.paste([source], child if nested else source)
    assert not result.paths
    assert 'itself or its subfolder' in result.errors[0]
    assert not (child / 'case').exists()


def test_archive_round_trip_preserves_empty_dirs_and_does_not_read_source(tmp_path):
    source = tmp_path / '工况'
    (source / 'empty').mkdir(parents=True)
    (source / '记录.csv').write_bytes(b'time,X\n0,42\n')
    result = ops.compress([source])
    assert not result.errors
    with zipfile.ZipFile(result.paths[0]) as archive:
        assert '工况/empty/' in archive.namelist()
    # Delete the source, so reading it instead of the extracted tree cannot pass.
    import shutil
    shutil.rmtree(source)
    extracted = ops.extract(result.paths[0])
    assert not extracted.errors
    assert (extracted.paths[0] / '工况' / '记录.csv').read_bytes() == b'time,X\n0,42\n'
    assert (extracted.paths[0] / '工况' / 'empty').is_dir()


@pytest.mark.parametrize('entry', ['../escape.txt', '/absolute.txt', 'C:/escape.txt',
                                  '..\\escape.txt', 'safe/../../escape.txt', 'foo:bar'])
def test_bad_zip_names_are_rejected_before_writing(tmp_path, entry):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('safe.txt', 'would be partial')
        archive.writestr(entry, 'unsafe')
    result = ops.extract(path)
    assert not result.paths
    assert 'unsafe entry' in result.errors[0]
    assert not (tmp_path / 'bad').exists()
    assert not (tmp_path.parent / 'escape.txt').exists()


def test_empty_and_corrupt_archives(tmp_path):
    archive_path = tmp_path / 'empty.zip'
    with zipfile.ZipFile(archive_path, 'w'):
        pass
    result = ops.extract(archive_path)
    assert not result.errors
    assert result.paths[0].is_dir()
    broken = tmp_path / 'broken.zip'
    broken.write_bytes(b'bad ZIP')
    result = ops.extract(broken)
    assert not result.paths and result.errors
    assert not (tmp_path / 'broken').exists()


def test_copy_cancellation_removes_partial_and_keeps_input(tmp_path):
    source = tmp_path / 'log.mf4'
    source.write_bytes(b'x' * (3 * 1024 * 1024))
    output = tmp_path / 'output'
    output.mkdir()
    calls = []
    def cancel(name):
        calls.append(name)
        if len(calls) == 2:
            raise ops.Cancelled
    result = ops.paste([source], output, progress=cancel)
    assert result.cancelled and not result.paths
    assert source.stat().st_size == 3 * 1024 * 1024
    assert not list(output.iterdir())


def test_folder_selection_does_not_duplicate_descendants(tmp_path):
    folder = tmp_path / 'case'
    folder.mkdir()
    child = folder / 'data.csv'
    child.write_text('data')
    result = ops.compress([folder, child])
    assert not result.errors
    with zipfile.ZipFile(result.paths[0]) as archive:
        assert archive.namelist() == ['case/', 'case/data.csv']


def test_archive_of_same_file_names_in_different_folders_preserves_paths(tmp_path):
    paths = []
    for folder_name in ('CAN', 'LIN'):
        folder = tmp_path / folder_name
        folder.mkdir()
        path = folder / 'Recorder.csv'
        path.write_text(folder_name)
        paths.append(path)
    result = ops.compress(paths)
    assert not result.errors
    with zipfile.ZipFile(result.paths[0]) as archive:
        assert set(archive.namelist()) == {'CAN/Recorder.csv', 'LIN/Recorder.csv'}
    restored = ops.extract(result.paths[0])
    assert not restored.errors
    for folder_name in ('CAN', 'LIN'):
        assert (restored.paths[0] / folder_name / 'Recorder.csv').read_text() == folder_name
