"""Packages transfer non-copyable handles without automatic close."""

from pathlib import Path

import pytest

from tests.cli.test_cli import run_cli


def includes(*packages):
    """Use checkout sources without installing packages or building examples."""
    root = Path(__file__).resolve().parents[2]
    return [arg for package in packages
            for arg in ('-I', root / 'packages' / package / 'src')]


@pytest.mark.parametrize('container', ['List', 'Queue', 'Stack'])
def test_container_transfer(tmp_path, monkeypatch, container):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { List, Queue, Stack } from std.collections;
    @nocopy struct Handle { id:i32; }
    fn main()->i32 {
        let items=CONTAINER<Handle>();
        let a:Handle={7}; items.push(move a);
        let b=items.pop();
        return b.id-7;
    }
    '''.replace('CONTAINER', container))
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '-o', tmp_path / 'program', '--run') == 0


@pytest.mark.parametrize('statement', [
    'let second=items;',
    'items.push_from(items[0]);',
    'let second=items[0];',
])
def test_container_copy_rejected(tmp_path, monkeypatch, capsys, statement):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { List } from std.collections;
    @nocopy struct Handle { id:i32; }
    fn main() {
        let items=List<Handle>();
        items.push(Handle(7));
        STATEMENT
    }
    '''.replace('Handle(7)', '{7}').replace('STATEMENT', statement))
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '--emit-llvm') == 1
    assert 'nocopy' in capsys.readouterr().err


def test_real_handle_factories_compile(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { File, Directory, FileMode } from std.fs;
    import { Archive, ArchiveMode } from archive.zip;
    fn main()->i32 {
        let file=try File::open("x", FileMode::Read) except(e){return 1;}
        let f=move file;
        let fc=f.close();
        let dir=try Directory::open(".") except(e){return 2;}
        let dc=dir.close();
        let archive=try Archive::open("x", ArchiveMode::Read) except(e){return 3;}
        let entry=try archive.get_file(0) except(e){return 4;}
        let ec=entry.close();
        let ac=archive.close();
        return 0;
    }
    ''')
    assert run_cli(monkeypatch, source,
                   *includes('core', 'libc', 'posix', 'archive', 'libzip'),
                   '--emit-llvm') == 0
    capsys.readouterr()


@pytest.mark.parametrize('container', ['List', 'Queue', 'Stack'])
def test_explicit_container_clone(tmp_path, monkeypatch, container):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { List, Queue, Stack } from std.collections;
    @static let clones:i32=0;
    @nocopy struct Handle: Clone { id:i32; }
    fn Handle::clone(const &self)->Handle {clones+=1;return {self.id};}
    fn main()->i32 {
        let items=CONTAINER<Handle>();items.push({7});
        let copied=items.clone();
        let a=items.pop();let b=copied.pop();
        return clones==1 and a.id==7 and b.id==7 ? 0 : 1;
    }
    '''.replace('CONTAINER', container))
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '-o', tmp_path / 'program', '--run') == 0


def test_map_moves_values_and_borrows_entries(tmp_path, monkeypatch):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { Map } from std.collections;
    @nocopy struct Handle {id:i32;}
    fn main()->i32 {
        let m=Map<i32,Handle>();
        for(let i:i32=0;i<40;i+=1){let h:Handle={i};m.set(i,move h);}
        let total:i32=0;
        foreach(entry:m){total+=entry.value.id;}
        return total-780;
    }
    ''')
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '-o', tmp_path / 'program', '--run') == 0


def test_map_entry_copy_rejected(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { Map } from std.collections;
    @nocopy struct Handle {id:i32;}
    fn main(){let m=Map<i32,Handle>();m.set(1,{7});
        let it=m.iterator();let copy=it.next();}
    ''')
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '--emit-llvm') == 1
    assert 'cannot copy @nocopy' in capsys.readouterr().err


@pytest.mark.parametrize('imports,factory', [
    ('import { File, FileMode } from std.fs;',
     'File::open("x",FileMode::Read)'),
    ('import { Directory } from std.fs;', 'Directory::open(".")'),
    ('import { Archive, ArchiveMode } from archive.zip;',
     'Archive::open("x",ArchiveMode::Read)'),
    ('import { File } from archive.zip;', 'Ok<File,i32>(File(null))'),
])
def test_package_handle_copies_rejected(tmp_path, monkeypatch, capsys,
                                      imports, factory):
    source = tmp_path / 'main.sie'
    source.write_text(imports + '''
    fn main()->i32 {
        let handle=try FACTORY except(e){return 1;}
        let copy=handle;
        return 0;
    }
    '''.replace('FACTORY', factory))
    assert run_cli(monkeypatch, source,
                   *includes('core', 'libc', 'posix', 'archive', 'libzip'),
                   '--emit-llvm') == 1
    assert 'cannot copy @nocopy' in capsys.readouterr().err


def test_random_seed_keeps_manual_file_close(tmp_path, monkeypatch):
    source = tmp_path / 'main.sie'
    source.write_text('''
    import { random } from std.random;
    fn main()->i32 {let value=random();return 0;}
    ''')
    assert run_cli(monkeypatch, source, *includes('core', 'libc', 'posix'),
                   '-o', tmp_path / 'program', '--run') == 0
