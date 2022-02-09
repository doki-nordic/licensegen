
import argparse
from genericpath import exists
import os
import yaml
from pathlib import Path
import re
import subprocess
import tempfile
import sys
from tokenize import group

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

def generate_deps():
    ninja_out_name = tempfile.mktemp('.txt', 'licgen_stdout_')
    with open(ninja_out_name, 'w') as ninja_out_fd:
        ninja_err_name = tempfile.mktemp('.txt', 'licgen_stderr_')
        with open(ninja_err_name, 'w') as ninja_err_fd:
            try:
                cp = subprocess.run('ninja -t deps', shell=True, stdout=ninja_out_fd,
                                    stderr=ninja_err_fd, cwd=args.build_directory)
            except Exception as e:
                raise GeneratorError(f'Unable to start "ninja -t deps" command: {str(e)}')
    with open(ninja_err_name, 'r') as ninja_err_fd:
        err = ninja_err_fd.read().strip()
        if len(err) > 0:
            eprint(err)
    if cp.returncode != 0:
        raise GeneratorError(f'"ninja -t deps" command exited with error code {cp.returncode}')
    os.unlink(ninja_err_name)
    return ninja_out_name


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
spdx_licenses_names = { '': 'None' }

def simplify_license_text(text):
    text = re.sub(r'[^a-z0-9]+', '', text, flags=re.IGNORECASE)
    text = text.strip().lower()
    return text

def load_license_texts():
    global license_texts
    this_path = Path(__file__).absolute().parents[0]
    with open(this_path / 'licenses.yaml', 'r') as fs:
        texts = yaml.safe_load(fs)
    license_texts = []
    for t in texts['licenses']:
        text = simplify_license_text(t['text'])
        license_texts.append((t['id'], text))
    for a in license_texts:
        for b in license_texts:
            if (a[0] != b[0]) and (a[1].find(b[1]) >= 0):
                raise Exception(f'License text for {b[0]} is part of {a[0]}. '
                    'This situation is not implemented. Implement it before adding to '
                    'licenses.yaml file.')

def add_spdx_license(file_name, name):
    global spdx_licenses, spdx_licenses_names
    name = name.strip()
    lower_name = name.lower()
    spdx_licenses_names[lower_name] = name
    if lower_name not in spdx_licenses:
        spdx_licenses[lower_name] = set()
    spdx_licenses[lower_name].add(file_name)

def parse_spdx_license(file_name, source):
    m = tuple(re.finditer(r'SPDX-License-Identifier\s*:?\s*([^\r\n\*]+)', source, re.IGNORECASE))
    if len(m) > 1:
        raise GeneratorError(f'{file_name}:0: File contains more than one SPDX-License-Identifier')
    elif len(m) == 1:
        add_spdx_license(file_name, m[0].group(1))
    elif parse_license_text(file_name, source):
        pass
    else:
        spdx_licenses[''].add(file_name)

def parse_license_text(file_name, source):
    global license_texts
    source = simplify_license_text(source)
    for spdx_id, text in license_texts:
        if source.find(text) >= 0:
            add_spdx_license(file_name, spdx_id)
            return True
    return False

try:
    all_files = set()
    args = parse_args()
    validate_build_directory()
    load_license_texts()
    deps_file_name = generate_deps()
    parse_deps_file(all_files, deps_file_name)
    for file_name in all_files:
        try:
            with open(file_name) as fd:
                source = fd.read()
        except:
            spdx_licenses[''].add(file_name)
            eprint(f'{file_name}:0: Error reading file')
            continue
        if len(source.strip()) == 0:
            continue
        parse_spdx_license(file_name, source)
    for k, v in spdx_licenses.items():
        print(spdx_licenses_names[k])
        for f in v:
            print(f'    {f}')
    print(f'Total dependent source files: {len(all_files)}')
except GeneratorError as e:
    eprint(str(e))
    if args.debug:
        raise
    exit(1)

