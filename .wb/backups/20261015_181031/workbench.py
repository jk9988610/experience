#!/usr/bin/env python3
import os, sys, re, json, time, shutil, subprocess, tempfile, atexit
import tkinter as tk
from tkinter import scrolledtext, messagebox

WB_VERSION = "1.0 (2026-10-15)"

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
SELF = os.path.abspath(__file__)
WB_HOME = os.path.join(ROOT, '.wb')
try:
    os.makedirs(WB_HOME, exist_ok=True)
except Exception:
    pass

CONFIG = os.path.join(WB_HOME, 'config.json')
BACKUP_DIR = os.path.join(WB_HOME, 'backups')
MAX_BACKUPS = 20

PUNCT = '{}();,'
ALLOWED_HEADS = ('git','python','python3','cmake','make','ninja','nmake','msbuild','powershell','pwsh','chcp')
CREATE_NO_WINDOW = 0x08000000

class PersistPS:
    def __init__(self):
        self.proc = None; self.counter = 0
        self._start(); atexit.register(self.close)
    def _start(self):
        try:
            self.proc = subprocess.Popen(
                ['powershell','-NoProfile','-NoLogo','-NonInteractive','-ExecutionPolicy','Bypass','-Command','-'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                creationflags=CREATE_NO_WINDOW)
        except Exception: self.proc = None
    def run(self, script):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
            if self.proc is None: return '[ERR] PowerShell 无法启动'
        self.counter += 1
        sentinel = '__AI_SENT_%d__' % self.counter
        fd, path = tempfile.mkstemp(suffix='.ps1')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(script)
            self.proc.stdin.write("& '%s'; Write-Host '%s'\n" % (path, sentinel))
            self.proc.stdin.flush()
            out = []
            while True:
                line = self.proc.stdout.readline()
                if not line: break
                if sentinel in line: break
                out.append(line)
            return ''.join(out)
        except Exception as e: return '[ERR] %s' % e
        finally:
            try: os.unlink(path)
            except Exception: pass
    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.close(); self.proc.terminate()
        except Exception: pass

PS = PersistPS()

def load_cfg():
    try:
        with open(CONFIG, 'r', encoding='utf-8') as f: return json.load(f)
    except Exception: return {}

def save_cfg(c):
    try:
        with open(CONFIG, 'w', encoding='utf-8') as f: json.dump(c, f, indent=2)
    except Exception: pass

def canonical(s):
    return re.sub(r'\s+', ' ', s).strip()

def sim(a, b):
    ta = set(re.findall(r'\S+', canonical(a)))
    tb = set(re.findall(r'\S+', canonical(b)))
    if not ta or not tb: return 0.0
    return len(ta & tb) / len(ta | tb)

def parse_all(text):
    patches, commands, reads, pss = [], [], [], []
    cur_file = None; cur_op = None
    cur_old = []; cur_new = []; cur_anchor = None; cur_nth = None
    in_new = False; state = 'idle'
    cmd_buf = []; read_buf = []; ps_buf = []

    def flush_patch():
        nonlocal cur_op, cur_old, cur_new, cur_anchor, cur_nth, in_new
        if cur_op and cur_file:
            old = cur_old[:]; new = cur_new[:]
            while old and not old[0].strip(): old.pop(0)
            while old and not old[-1].strip(): old.pop()
            while new and not new[0].strip(): new.pop(0)
            while new and not new[-1].strip(): new.pop()
            anchor = cur_anchor
            if cur_op in ('INSERT_AFTER','INSERT_BEFORE') and not anchor and old:
                anchor = old[0].strip()
            patches.append({'file':cur_file,'op':cur_op,'old':old,'new':new,'anchor':anchor,'nth':cur_nth})
        cur_op=None; cur_old=[]; cur_new=[]; cur_anchor=None; cur_nth=None; in_new=False

    def flush_lines(buf, target):
        for line in buf:
            if not line.strip(): continue
            if line.strip().startswith('#'): continue
            target.append(line.rstrip())

    for raw in text.splitlines():
        s = raw.strip()
        if s in ('=== GIT ===','=== RUN ===','=== CMD ==='):
            flush_patch(); state='cmd'; cmd_buf=[]; continue
        if s == '=== READ ===':
            flush_patch(); flush_lines(cmd_buf, commands); state='read'; read_buf=[]; continue
        if s in ('=== PS ===','=== PWSH ==='):
            flush_patch(); state='ps'; ps_buf=[]; continue
        if state=='cmd':
            if s=='===': flush_lines(cmd_buf, commands); state='idle'; continue
            cmd_buf.append(raw); continue
        if state=='read':
            if s=='===': flush_lines(read_buf, reads); state='idle'; continue
            read_buf.append(raw); continue
        if state=='ps':
            if s=='===': pss.append('\n'.join(ps_buf)); state='idle'; continue
            ps_buf.append(raw); continue
        if s.startswith('=== FILE:'):
            flush_patch(); cur_file = s[len('=== FILE:'):].strip(); continue
        if s.startswith('=== END'):
            flush_patch(); cur_file = None; continue
        m = re.match(r'^\[(REPLACE|DELETE|INSERT_AFTER|INSERT_BEFORE)(?:\s+#(\d+))?\]\s*(.*)$', s)
        if m:
            flush_patch()
            cur_op = m.group(1)
            cur_nth = int(m.group(2)) if m.group(2) else None
            cur_anchor = m.group(3).strip() or None
            continue
        if s == '==>': in_new = True; continue
        if cur_op is None: continue
        (cur_new if in_new else cur_old).append(raw)

    flush_patch()
    if state=='cmd': flush_lines(cmd_buf, commands)
    if state=='read': flush_lines(read_buf, reads)
    if state=='ps' and ps_buf: pss.append('\n'.join(ps_buf))
    return patches, commands, reads, pss

class PatchError(Exception): pass

def find_unique(lines, target, nth=None):
    if not target: return None
    tgt = [canonical(t) for t in target]; n = len(tgt)
    hits = [i for i in range(len(lines)-n+1) if [canonical(x) for x in lines[i:i+n]] == tgt]
    if not hits: return None
    if len(hits) == 1: return hits[0], hits[0]+n
    if nth is not None:
        if 1 <= nth <= len(hits):
            s = hits[nth-1]; return s, s+n
        raise PatchError('指定第 %d 处, 但只找到 %d 处' % (nth, len(hits)))
    raise PatchError('多匹配 (%d 处, 行号: %s), 可加 [REPLACE #N] 指定第几处' % (len(hits), [h+1 for h in hits]))

def best_matches(lines, target, topn=3):
    if not target: return []
    scored = [(sim(lines[i], target[0]), i+1, lines[i]) for i in range(len(lines))]
    scored.sort(reverse=True); return scored[:topn]

def indent_of(line):
    return line[:len(line)-len(line.lstrip())]

def detect_indent_unit(lines):
    tab_count = 0
    space_widths = []
    for line in lines:
        if not line.strip(): continue
        lead = line[:len(line)-len(line.lstrip())]
        if not lead: continue
        if '\t' in lead:
            tab_count += 1
        else:
            space_widths.append(len(lead))
    if tab_count > len(space_widths):
        return '\t'
    if space_widths:
        from math import gcd
        from functools import reduce
        step = reduce(gcd, space_widths) if len(space_widths) > 1 else space_widths[0]
        if step in (2, 3, 4, 8):
            return ' ' * step
    return '    '


def indent_preserve(new_lines, base_indent, unit='    '):
    base = 0
    for n in new_lines:
        if n.strip():
            base = len(n) - len(n.lstrip())
            break
    out = []
    for n in new_lines:
        if not n.strip():
            out.append('')
            continue
        rel = len(n) - len(n.lstrip()) - base
        if rel < 0: rel = 0
        if unit == '\t':
            levels = round(rel / 4)
            out.append(base_indent + '\t' * levels + n.lstrip())
        else:
            levels = rel // len(unit)
            out.append(base_indent + unit * levels + n.lstrip())
    return out

def apply_patch_block(lines, blk, indent_unit='    '):
    op = blk['op']
    if op in ('REPLACE','DELETE'):
        if op == 'REPLACE' and blk['new']:
            pos_new = find_unique(lines, blk['new'])
            if pos_new is not None:
                return '跳过(已是目标)',None
        pos = find_unique(lines, blk['old'], blk.get('nth'))
        if pos is None:
            msg = ['未找到: %r' % (blk['old'][0] if blk['old'] else '')]
            msg.append('最相似的行:')
            for score, ln, l in best_matches(lines, blk['old']):
                msg.append('  行 %d (%.2f): %s' % (ln, score, l.rstrip()))
            raise PatchError('\n'.join(msg))
        s, e = pos
        if op == 'DELETE':
            del lines[s:e]
            return 'OK', (op, s+1, e-s, 0)
        else:
            ind = indent_of(lines[s])
            new = indent_preserve(blk['new'], ind, indent_unit)
            if [canonical(x) for x in lines[s:e]] == [canonical(x) for x in blk['new']]:
                return '跳过(已是目标)',None
            lines[s:e] = new
            return 'OK', (op, s+1, e-s, len(new))
    elif op in ('INSERT_AFTER','INSERT_BEFORE'):
        if not blk['anchor']: raise PatchError('缺少锚点')
        hits = [i for i, l in enumerate(lines) if canonical(l) == canonical(blk['anchor'])]
        if not hits:
            msg = ['锚点未找到: %r' % blk['anchor']]
            msg.append('最相似的行:')
            for score, ln, l in best_matches(lines, [blk['anchor']]):
                msg.append('  行 %d (%.2f): %s' % (ln, score, l.rstrip()))
            raise PatchError('\n'.join(msg))
        if len(hits) > 1: raise PatchError('锚点不唯一 (%d 处)' % len(hits))
        i = hits[0]; ind = indent_of(lines[i])
        new = indent_preserve(blk['new'], ind, indent_unit)
        pos = i+1 if op == 'INSERT_AFTER' else i
        existing = lines[pos:pos+len(new)]
        if [canonical(x) for x in existing] == [canonical(x) for x in blk['new']]:
            return '跳过(已存在)',None
        lines[pos:pos] = new
        return 'OK', (op, pos+1, 0, len(new))
    return 'OK', None

def backup_files(files):
    if not files: return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    d = os.path.join(BACKUP_DIR, ts)
    os.makedirs(d, exist_ok=True)
    manifest = []
    for f in files:
        full = os.path.abspath(os.path.join(ROOT, f))
        if not os.path.exists(full): continue
        safe = f.replace('/', '__').replace('\\', '__')
        dst = os.path.join(d, safe)
        shutil.copy2(full, dst)
        manifest.append({'file': f, 'backup': safe})
    with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as fp:
        json.dump(manifest, fp, ensure_ascii=False)
    dirs = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, x))])
    while len(dirs) > MAX_BACKUPS:
        shutil.rmtree(os.path.join(BACKUP_DIR, dirs.pop(0)), ignore_errors=True)
    return ts

def undo_last():
    if not os.path.exists(BACKUP_DIR): return '没有可撤销的记录'
    dirs = sorted([x for x in os.listdir(BACKUP_DIR) if os.path.isdir(os.path.join(BACKUP_DIR, x))], reverse=True)
    if not dirs: return '没有可撤销的记录'
    ts = dirs[0]
    d = os.path.join(BACKUP_DIR, ts)
    mf = os.path.join(d, 'manifest.json')
    if not os.path.exists(mf): return '备份损坏，无法撤销'
    with open(mf, 'r', encoding='utf-8') as fp:
        manifest = json.load(fp)
    restored = []
    for item in manifest:
        src = os.path.join(d, item['backup'])
        dst = os.path.join(ROOT, item['file'])
        if os.path.exists(src):
            shutil.copy2(src, dst)
            restored.append(item['file'])
    shutil.rmtree(d, ignore_errors=True)
    return '已撤销上一次 patch（恢复 %d 个文件）:\n%s' % (len(restored), '\n'.join('  ' + r for r in restored))

def execute_patches(patches):
    if not patches: return None, False
    files_to_backup = list(set(b['file'] for b in patches))
    backup_files(files_to_backup)
    touched_self = False
    by_file = {}
    for b in patches: by_file.setdefault(b['file'], []).append(b)
    out = []
    for path, ops in by_file.items():
        full = os.path.abspath(os.path.join(ROOT, path))
        if os.path.abspath(full) == SELF: touched_self = True
        if not os.path.exists(full):
            raise PatchError('文件不存在: %s' % path)
        with open(full, 'r', encoding='utf-8', newline='') as f: raw = f.read()
        crlf_count = raw.count('\r\n')
        lf_count = raw.count('\n') - crlf_count
        eol = '\r\n' if crlf_count >= lf_count else '\n'
        lines = raw.replace('\r\n','\n').split('\n')
        indent_unit = detect_indent_unit(lines)
        applied, skipped = 0, 0
        details = []
        for op in ops:
            r, info = apply_patch_block(lines, op, indent_unit)
            if info is None:
                skipped += 1
                details.append('  [%s] %s' % (r, op['op']))
            else:
                applied += 1
                op_type, start, deleted, added = info
                details.append('  %s L%d  -%d/+%d' % (op_type, start, deleted, added))
        with open(full, 'w', encoding='utf-8', newline='') as f: f.write(eol.join(lines))
        if skipped == 0:
            out.append('[OK] %s (%d op)' % (path, applied))
        else:
            out.append('[OK] %s (%d op, %d 跳过)' % (path, applied, skipped))
            out.extend(details)
        return '\n'.join(out), touched_self

MAX_OUT_LINES = 60
HEAD_LINES = 30
TAIL_LINES = 20


def truncate_output(text, max_lines=MAX_OUT_LINES, head=HEAD_LINES, tail=TAIL_LINES):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    omitted = len(lines) - head - tail
    if omitted <= 0:
        return text
    return '\n'.join(
        lines[:head]
        + ['... [省略 %d 行，如需完整输出请手动运行] ...' % omitted]
        + lines[-tail:]
    )


def execute_commands(cmds):
    if not cmds: return None
    out = []
    for cmd in cmds:
        try:
            head = re.split(r'\s+', cmd.strip())[0].lower()
            if head.endswith('.exe'): head = head[:-4]
            if head not in ALLOWED_HEADS:
                out.append('$ %s\n[BLOCKED] %s' % (cmd, head)); continue
        except Exception: continue
        r = PS.run(cmd)
        body = r.rstrip() if r.strip() else '(无输出)'
        out.append('$ %s\n%s' % (cmd, truncate_output(body)))
    return '\n'.join(out)

def execute_ps(scripts):
    if not scripts: return None
    out = []
    for i, script in enumerate(scripts, 1):
        r = PS.run(script)
        body = r.rstrip() if r.strip() else '(无输出)'
        out.append('$ [PS #%d]\n%s' % (i, truncate_output(body)))
    return '\n'.join(out)

MAX_READ_LINES = 200


def execute_reads(reads):
    if not reads: return None
    out = []
    for spec in reads:
        parts = spec.split()
        if not parts: continue
        path = parts[0]
        try:
            start = int(parts[1])-1 if len(parts) > 1 else 0
            end = int(parts[2]) if len(parts) > 2 else None
        except ValueError:
            out.append('--- %s ---\n[ERR] 行号解析失败' % path); continue
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            out.append('--- %s ---\n[ERR] 文件不存在' % path); continue
        with open(full, 'r', encoding='utf-8', errors='replace') as f: lines = f.readlines()
        total = len(lines)
        if start < 0: start = 0
        truncated = False
        if end is None:
            if total - start > MAX_READ_LINES:
                end = start + MAX_READ_LINES
                truncated = True
            else:
                end = total
        selected = lines[start:end]
        if not selected:
            out.append('--- %s (空范围) ---' % path); continue
        end_real = start + len(selected)
        header = '--- %s (行 %d-%d / 共 %d)' % (path, start+1, end_real, total)
        if truncated:
            next_start = end_real + 1
            header += ' [截断, 继续看用: %s %d %d]' % (path, next_start, total)
        buf = [header]
        for i, line in enumerate(selected, start+1):
            buf.append('%5d | %s' % (i, line.rstrip('\n').rstrip('\r')))
        out.append('\n'.join(buf))
    return '\n\n'.join(out) if out else None

def git_cmd(args):
    return PS.run('git ' + ' '.join(args))

_E = "=" * 3

_DEFAULT_HELP = (
    "工作台帮助\n"
    "==========\n"
    "\n"
    "【补丁块】修改文件\n"
    f"  首行: {_E} FILE: 相对路径/文件名 {_E}\n"
    "  操作符各自独立一行:\n"
    "    写 [REPLACE] 旧内容 ==> 新内容\n"
    "    写 [REPLACE #2] 旧内容 ==> 新内容     (多处匹配时指定第 2 处)\n"
    "    写 [DELETE] 要删除的内容\n"
    "    写 [INSERT_AFTER] 锚点行 ==> 要插入的内容\n"
    "    写 [INSERT_BEFORE] 锚点行 ==> 要插入的内容\n"
    "  一个 FILE 块里可以写多个操作，按顺序执行。\n"
    "\n"
    "【读文件】\n"
    f"  写 {_E} READ {_E}\n"
    "  再跟 文件名 起始行 结束行\n"
    "  不写行号时默认只读前 200 行，超出会提示继续读的起始行\n"
    "\n"
    "【运行命令】\n"
    f"  写 {_E} CMD {_E}\n"
    "  允许的命令: git python python3 cmake make ninja nmake msbuild powershell pwsh chcp\n"
    "\n"
    "【PowerShell 脚本】\n"
    f"  写 {_E} PS {_E}\n"
    "  脚本内容另起一行\n"
    "\n"
    "【Git 命令】\n"
    f"  写 {_E} GIT {_E}\n"
    "  比如 status\n"
    "\n"
    "【输出说明】\n"
    "  - 补丁全成功 → [OK] 文件 (N op)\n"
    "  - 有跳过 → [OK] 文件 (N op, M 跳过) + 每条明细\n"
    "  - 命令输出超过 60 行 → 头尾截断\n"
    "  - 结果自动复制到剪贴板\n"
    "  - 撤销上一步可回退上次 patch\n"
    "\n"
    "【流程】\n"
    "  1. 把要执行的内容复制到剪贴板\n"
    "  2. 点运行按钮\n"
    "  3. 看输出框结果（已自动复制）\n"
)


HELP_FILE = os.path.join(ROOT, "help.txt")


def load_help():
    if os.path.exists(HELP_FILE):
        try:
            with open(HELP_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return "[读取 help.txt 失败: %s]\n\n%s" % (e, _DEFAULT_HELP)
    return _DEFAULT_HELP


class App:
    def __init__(self, root):
        self.root = root
        root.title('工作台')
        cfg = load_cfg()
        root.geometry(cfg.get('geometry', '1000x700+100+100'))
        root.minsize(600, 400)
        root.configure(bg='#1e1e1e')
        self._running = False

        self.toolbar = tk.Frame(root, bg='#1e1e1e', height=40)
        self.toolbar.pack(fill='x', padx=8, pady=(8,4))
        self.toolbar.pack_propagate(False)
        self.buttons = []
        self.var_autocopy = tk.BooleanVar(value=True)
        self.var_autoclear = tk.BooleanVar(value=True)
        self._add_btn('▶ 运行', self.run, '#0078d7')
        self._add_btn('❓ 帮助', self.show_help, '#4a4a8a')
        self._add_btn('📋 复制', self.copy, '#3c3c3c')
        self._add_btn('↶ 撤销上一步', self.undo, '#7a3c3c')
        self._add_btn('🔄 重启', self.restart, '#2a6a2a')
        self._add_check('自动复制', self.var_autocopy)
        self._add_check('自动清空', self.var_autoclear)
        self.toolbar.bind('<Configure>', lambda e: self._reflow())

        self.status = tk.Label(root, text='就绪', font=('Microsoft YaHei', 10),
                               bg='#1e1e1e', fg='#cccccc', anchor='w')
        self.status.pack(fill='x', padx=10, pady=(0,6))

        self.text = scrolledtext.ScrolledText(root, font=('Consolas', 10),
                                              bg='#141414', fg='#dcdcdc', wrap='none',
                                              insertbackground='white')
        self.text.pack(fill='both', expand=True, padx=8, pady=(0,8))
        root.protocol('WM_DELETE_WINDOW', self.on_close)
        root.after(100, self._reflow)

    def _add_btn(self, text, cmd, color):
        b = tk.Button(self.toolbar, text=text, command=cmd,
                      font=('Microsoft YaHei', 10), bg=color, fg='white',
                      relief='flat', padx=12, pady=6, cursor='hand2')
        self.buttons.append(b)

    def _add_check(self, text, var):
        cb = tk.Checkbutton(self.toolbar, text=text, variable=var,
                            font=('Microsoft YaHei', 10),
                            bg='#1e1e1e', fg='#cccccc',
                            activebackground='#1e1e1e',
                            activeforeground='#ffffff',
                            selectcolor='#2d2d2d',
                            relief='flat', bd=0, highlightthickness=0,
                            padx=6, pady=6, cursor='hand2')
        self.buttons.append(cb)

    def _reflow(self):
        w = self.toolbar.winfo_width()
        if w < 50: return
        x, y, row_h = 0, 0, 0; gap = 6
        for b in self.buttons:
            b.update_idletasks()
            bw, bh = b.winfo_reqwidth(), b.winfo_reqheight()
            if x + bw > w and x > 0:
                x = 0; y += row_h + 4; row_h = 0
            b.place(x=x, y=y); x += bw + gap; row_h = max(row_h, bh)
        self.toolbar.configure(height=y + row_h + 6)

    def set_status(self, msg, color='#cccccc'):
        self.status.config(text=msg, fg=color)

    def set_output(self, txt):
        self.text.delete('1.0','end'); self.text.insert('1.0', txt)

    def copy_to_clip(self, s):
        self.root.clipboard_clear(); self.root.clipboard_append(s); self.root.update()

    def run(self):
        if self._running:
            self.set_status('⏳ 上次还没跑完','yellow'); return
        self._running = True
        try:
            self._run_body()
        finally:
            self._running = False

    def _run_body(self):
        try: script = self.root.clipboard_get()
        except Exception: self.set_status('✗ 剪贴板读取失败','#ff6b6b'); return
        if not script.strip(): self.set_status('✗ 剪贴板为空','#ff6b6b'); return
        blocks = ('=== FILE:','=== GIT ===','=== RUN ===','=== CMD ===','=== READ ===','=== PS ===','=== PWSH ===')
        if not any(k in script for k in blocks):
            self.set_status('✗ 无已知块','#ff6b6b')
            self.set_output('前 300 字符:\n' + script[:300]); return
        self.set_status('⏳ 运行中...','yellow')
        if self.var_autoclear.get():
            self.set_output('')
        self.root.update()
        try:
            with open(os.path.join(WB_HOME,'last_patch.txt'),'w',encoding='utf-8',newline='') as f:
                f.write(script)
        except Exception as e:
            self.set_status('✗ 写 last_patch.txt 失败: %s' % e,'#ff6b6b'); return
        patches, cmds, reads, pss = parse_all(script)
        parts = []; ok = True; touched = False; self_updated = False
        if reads:
            try:
                body = execute_reads(reads)
                if body: parts.append('=== read ===\n%s' % body)
            except Exception as e:
                parts.append('=== read ===\n[ERR] %s' % e); ok = False
        if patches:
            try:
                out_p, self_updated = execute_patches(patches)
                if out_p: parts.append('=== patch ===\n%s' % out_p)
                touched = True
            except PatchError as e:
                parts.append('=== patch ===\n[FAIL]\n%s' % e); ok = False
            except Exception as e:
                parts.append('=== patch ===\n[ERR] %s' % e); ok = False
        if cmds:
            out_c = execute_commands(cmds) or ''
            if any(t in out_c for t in ('[BLOCKED]','[ERR]','[TIMEOUT]')): ok = False
            if out_c: parts.append('=== run ===\n%s' % out_c)
            if any(re.search(r'\bgit\s+(add|commit|checkout|reset|rm|mv)\b', c) for c in cmds):
                touched = True
        if pss:
            body = execute_ps(pss)
            if body: parts.append('=== ps ===\n%s' % body)
            touched = True
        result = '\n\n'.join(parts)
        self.set_output(result); self.copy_to_clip(result)
        if self_updated and ok:
            self.set_status('✓ 工作台自身已更新 — 点击 🔄 重启','#7bd88f')
            if messagebox.askyesno('重启','工作台已更新，是否立即重启？'): self.restart()
            return
        if ok: self.set_status('✓ 完成 — 结果已复制','#7bd88f')
        else: self.set_status('✗ 有错误 — 结果已复制','#ff6b6b')
    def show_help(self):
        text = "工作台 v" + WB_VERSION + "\n\n" + load_help()
        self.set_output(text)
        if self.var_autocopy.get():
            self.copy_to_clip(text)
            self.set_status('✓ 帮助已显示 (v%s)，并已复制到剪贴板' % WB_VERSION, '#7bd88f')
        else:
            self.set_status('✓ 帮助已显示 (v%s)' % WB_VERSION, '#7bd88f')

    def copy(self):
        txt = self.text.get('1.0','end-1c')
        if txt: self.copy_to_clip(txt); self.set_status('✓ 已复制','#7bd88f')

    def undo(self):
        msg = undo_last()
        self.set_output(msg); self.copy_to_clip(msg)
        self.set_status('↶ ' + msg.split('\n')[0], '#7bd88f')

    def restart(self):
        cfg = load_cfg(); cfg['geometry'] = self.root.geometry(); save_cfg(cfg)
        try:
            flags = CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.Popen([sys.executable, SELF], creationflags=flags)
        except Exception as e:
            self.set_status('✗ 重启失败: %s' % e,'#ff6b6b'); return
        self.root.destroy()

    def on_close(self):
        cfg = load_cfg(); cfg['geometry'] = self.root.geometry(); save_cfg(cfg)
        self.root.destroy()

def main():
    lastgood = os.path.join(WB_HOME, 'lastgood.py')
    try:
        root = tk.Tk()
        App(root)
        try:
            shutil.copy2(SELF, lastgood)
        except Exception:
            pass
        root.mainloop()
    except Exception as e:
        log_path = os.path.join(WB_HOME, 'rollback.log')
        if os.path.exists(lastgood):
            try:
                shutil.copy2(lastgood, SELF)
                msg = (
                    '工作台启动失败，已自动回滚到上一个可用版本。\n\n'
                    '原因: %s\n\n'
                    '请重新运行 workbench.py\n'
                    '日志: %s' % (e, log_path)
                )
                try:
                    with open(log_path, 'a', encoding='utf-8') as lf:
                        lf.write('[%s] 启动失败: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), e))
                        lf.write('已回滚到: %s\n\n' % lastgood)
                except Exception:
                    pass
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, msg, '工作台回滚', 0x40)
                except Exception:
                    print(msg)
                return
            except Exception as ee:
                try:
                    with open(log_path, 'a', encoding='utf-8') as lf:
                        lf.write('[%s] 回滚失败: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), ee))
                except Exception:
                    pass
                raise
        raise

if __name__ == '__main__':
    main()
