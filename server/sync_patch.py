"""Keep the files embedded in xiaozhi-esp32-server.patch identical to the repository copies.

The patch adds whole new files (the provider, wakeGate); their canonical source is this
repository, so the patch must never drift from it.

  python server/sync_patch.py          # rewrite the embedded files
  python server/sync_patch.py --check  # exit 1 if the patch is out of date (CI)
"""
import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'xiaozhi-esp32-server.patch'
EMBEDDED = {
    'main/xiaozhi-server/core/handle/wakeGate.py': HERE / 'core/handle/wakeGate.py',
    'main/xiaozhi-server/core/providers/llm/llamacpp_paced/llamacpp_paced.py':
        HERE / 'core/providers/llm/llamacpp_paced/llamacpp_paced.py',
}


def blob_hash(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()[:7]


def new_file_section(path, source):
    data = source.read_bytes().replace(b'\r\n', b'\n')  # the patch always carries LF files
    text = data.decode('utf-8')
    lines = text.split('\n')
    if lines[-1] == '':
        lines.pop()
    body = ''.join('+' + line + '\n' for line in lines)
    if not text.endswith('\n'):
        body += '\\ No newline at end of file\n'
    return (f'diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..{blob_hash(data)}\n'
            f'--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}')


def current_patch():
    return PATCH.read_bytes().decode('utf-8')  # no newline translation on Windows


def synced_patch():
    sections = current_patch().split('diff --git ')
    out = [sections[0]]
    for section in sections[1:]:
        path = section.split(' ', 1)[0][2:]  # "a/<path> b/<path>..." -> <path>
        out.append(new_file_section(path, EMBEDDED[path]) if path in EMBEDDED else 'diff --git ' + section)
    return ''.join(out)


def main():
    patch = synced_patch()
    current = current_patch()
    if '--check' in sys.argv[1:]:
        if patch != current:
            print(f'{PATCH.name} is out of date: run python server/sync_patch.py', file=sys.stderr)
            sys.exit(1)
        print('patch in sync')
        return
    if patch != current:
        PATCH.write_bytes(patch.encode('utf-8'))
        print(f'rewrote {PATCH.name}')
    else:
        print('patch already in sync')


if __name__ == '__main__':
    main()
