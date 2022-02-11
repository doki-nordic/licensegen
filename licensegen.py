
import argparse
from genericpath import exists
from math import ceil, floor
import os
import yaml
from pathlib import Path
import re
import subprocess
import tempfile
import sys
from tokenize import group

# TODO: handle dual license in tinycrypt ecc_dh

class GeneratorError(Exception):
    pass

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

def parse_args():
    parser = argparse.ArgumentParser(description='Create license report for application', add_help=False)
    parser.add_argument('build_directory', nargs="?", type=str,
                        help='The build directory where "build.ninja" file is located. '
                             'By default, current directory is used.')
    parser.add_argument('--debug', action='store_true',
                        help='Show details in case of exception (for debugging purpose).')
    parser.add_argument('--help', action='help',
                        help='Show this help message and exit')
    sys.argv = ['a', '/home/doki/work/ncs/nrf/samples/bluetooth/rpc_host/build_nrf5340dk_nrf5340_cpunet']
    return parser.parse_args()

def validate_build_directory():
    global args
    if args.build_directory is None:
        args.build_directory = os.getcwd()
    args.build_directory = os.path.abspath(args.build_directory)
    if not os.path.exists(args.build_directory):
        raise GeneratorError(f'Build directory "{args.build_directory}" does not exists.')
    if not os.path.exists(Path(args.build_directory) / 'build.ninja'):
        raise GeneratorError(f'Build directory "{args.build_directory}" does not contain "build.ninja" file.')

def ninja_tool_generate(command):
    ninja_out_name = tempfile.mktemp('.txt', 'licgen_stdout_')
    with open(ninja_out_name, 'w') as ninja_out_fd:
        ninja_err_name = tempfile.mktemp('.txt', 'licgen_stderr_')
        with open(ninja_err_name, 'w') as ninja_err_fd:
            try:
                cp = subprocess.run(command, shell=True, stdout=ninja_out_fd,
                                    stderr=ninja_err_fd, cwd=args.build_directory)
            except Exception as e:
                raise GeneratorError(f'Unable to start "{command}" command: {str(e)}')
    with open(ninja_err_name, 'r') as ninja_err_fd:
        err = ninja_err_fd.read().strip()
        if len(err) > 0:
            eprint(err)
    if cp.returncode != 0:
        raise GeneratorError(f'"{command}" command exited with error code {cp.returncode}')
    os.unlink(ninja_err_name)
    return ninja_out_name


def parse_targets_file(all_files, deps_file_name):
    global args
    with open(deps_file_name, 'r') as fd:
        line_no = 0
        while True:
            line = fd.readline()
            line_no += 1
            if len(line) == 0:
                break
            line = line.strip()
            if len(line) == 0:
                continue
            file = Path(args.build_directory, line).resolve()
            all_files.add(file)


def parse_deps_file(all_files, deps_file_name):
    global args
    TARGET_LINE_RE = re.compile(r'[^\s].*:\s*(#.*)?')
    DEP_LINE_RE = re.compile(r'\s+(.*?)\s*(#.*)?')
    EMPTY_LINE_RE = re.compile(r'\s*(#.*)?')
    with open(deps_file_name, 'r') as fd:
        line_no = 0
        while True:
            line = fd.readline()
            line_no += 1
            if len(line) == 0:
                break
            line = line.rstrip()
            m = DEP_LINE_RE.fullmatch(line)
            if (m is None):
                if ((TARGET_LINE_RE.fullmatch(line) is None) and (EMPTY_LINE_RE.fullmatch(line) is None)):
                    raise GeneratorError(f'{deps_file_name}:{line_no}: Cannot parse dependency file')
                continue
            file = Path(args.build_directory, m.group(1)).resolve()
            all_files.add(file)

spdx_licenses = { '': set() }
spdx_licenses_names = { '': 'NOASSERTION' }

def simplify_license_text(text):
    text = re.sub(r'[^a-z]+', '', text, flags=re.IGNORECASE)
    text = text.strip().lower()
    return text

def explode_list(text):
    return re.split(r'\s*[,;]\s*', text)

def load_license_texts():
    global license_texts, file_types
    this_path = Path(__file__).absolute().parents[0]
    with open(this_path / 'config.yaml', 'r') as fs:
        texts = yaml.safe_load(fs)
    license_texts = []
    for t in texts['license-texts']:
        if 'detect-pattern' in t:
            text = ''
            arr = t['detect-pattern'].split('</regex>')
            for part in arr:
                plain, *regex = part.split('<regex>') + [ '' ]
                text += simplify_license_text(plain) + ''.join(regex)
            text = re.compile(text)
        elif 'detect-text' in t:
            text = simplify_license_text(t['detect-text'])
        else:
            text = simplify_license_text(t['text'])
        license_texts.append((t['id'], text))
    for a in license_texts:
        for b in license_texts:
            pass#TODO: later if (a[0] != b[0]) and (a[1].find(b[1]) >= 0):
                # raise Exception(f'License text for {b[0]} is part of {a[0]}. '
                #     'This situation is not implemented. Implement it before adding to '
                #     'licenses.yaml file.')
    file_types = []
    for t in texts['file-types']:
        if 'extensions' in t:
            re_str = r'.*\.(' + '|'.join((ext for ext in explode_list(t['extensions']))) + ')'
        else:
            re_str = t['regexp']
        file_types.append({
            're': re_str,
            'for': set(explode_list(t['for'] if 'for' in t else 'app,global')),
            'category': t['category'] if 'category' in t else 'Uncategorised',
            'exclude': t['exclude'] if 'exclude' in t else False
        })


def add_spdx_license(file_name, name):
    global spdx_licenses, spdx_licenses_names
    name = name.strip()
    lower_name = name.lower()
    if lower_name not in spdx_licenses:
        spdx_licenses_names[lower_name] = name
        spdx_licenses[lower_name] = set()
    spdx_licenses[lower_name].add(file_name)

def detect_spdx_tag(source):
    result = set()
    for m in re.finditer(r'SPDX-License-Identifier\s*:?\s*([a-z0-9 :\(\)\.\+\-]+)', source, re.IGNORECASE):
        id = m.group(1).strip()
        if len(id):
            result.add(id)
    return result

def detect_license_text(source):
    global license_texts
    result = set()
    source = simplify_license_text(source)
    for spdx_id, text in license_texts:
        if type(text) is re.Pattern:
            if text.search(source) is not None:
                result.add(spdx_id)
        elif source.find(text) >= 0:
            result.add(spdx_id)
    return result

scanned_dirs = set()
scanned_files = set()
files_from_spdx = dict()

def detect_license_spdx_dir(dir_path, file_path):
    global scanned_dirs, scanned_files
    dir_path = Path()
    file_path = Path()
    if str(dir_path) in scanned_dirs:
        return
    search_files = []
    search_dirs = []
    for f in os.listdir(dir_path):
        f_path = dir_path / f
        if f_path.is_file():
            if f.lower().endswith('.spdx'):
                search_files.append(f_path)
        elif f_path.is_dir():
            if f.lower().find('spdx') >= 0:
                search_dirs.append(f_path)
    scanned_dirs.add(str(dir_path))

def detect_license_spdx_file(file_name):
    global files_from_spdx
    path = Path(file_name).absolute()
    if str(path) in files_from_spdx:
        return files_from_spdx[str(path)]['id']
    for parent in path.parents:
        ids = detect_license_spdx_dir(parent, path)
        if ids is not None:
            return ids
    return set()

def detect_license(file_name, source):
    ids = detect_spdx_tag(source)
    ids = ids.union(detect_license_text(source))
    ids = ids.union(detect_license_spdx_file(file_name))
    if len(ids) == 0:
        add_spdx_license(file_name, '')
    for id in ids:
        add_spdx_license(file_name, id)

def is_included(mode, path):
    global file_types
    for type in file_types:
        if mode not in type['for']:
            continue
        if re.fullmatch(type['re'], str(path)) is not None:
            return not type['exclude']
    raise Exception('"config.yaml" does not contains default fallback file type.')

def find_files(path, all_files, processed):
    real = str(path.resolve())
    if real in processed:
        return
    processed.add(real)
    for f in os.listdir(path):
        f = path / f
        if f.is_file():
            if is_included('global', f):
                all_files.add(str(f))
        if f.is_dir():
            find_files(f, all_files, processed)

try:
    all_files = set()
    args = parse_args()
    validate_build_directory()
    load_license_texts()
    # deps_file_name = ninja_tool_generate('ninja -t deps')
    # parse_deps_file(all_files, deps_file_name)
    # targets_file_name = ninja_tool_generate('ninja -t targets rule')
    # parse_targets_file(all_files, targets_file_name)
    processed = set()
    find_files(Path('/home/doki/work/ncs').resolve(), all_files, processed)
    #all_files = {'/home/doki/work/ncs/zephyr/scripts/ci/check_compliance.py'}
    print(f'Total dependent source files: {len(all_files)}')
    num = 0
    step = max(1, len(all_files) // 10)
    for file_name in all_files:
        if (num % step == 0):
            print(f'Processed {num} of {len(all_files)} files')
        num += 1
        try:
            with open(file_name, 'r', encoding='8859') as fd:
                source = fd.read(65536)
        except:
            spdx_licenses[''].add(file_name)
            eprint(f'{file_name}:0: Error reading file')
            continue
        if len(source.strip()) == 0:
            continue
        detect_license(file_name, source)
    for k, v in spdx_licenses.items():
        print(f'SPDX-License-Identifier: {spdx_licenses_names[k]}')
        for f in v:
            print(f'    {f}')
        print(f'        count: {len(v)}')
        exit()
except GeneratorError as e:
    eprint(str(e))
    if args.debug:
        raise
    exit(1)

