"""Finish a local Kokomi V4 + systemd AstrBot deployment (Python >= 3.10).

Run on the Linux server, not on the desktop. Never prints API credentials.
Uses the uploaded original release and the already installed plugin/runtime.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


class InstallError(RuntimeError):
    pass


def atomic_write(path: Path, data: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Changes:
    """Back up each original file once before an installation change."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        self.originals = {}

    def write(self, path: Path, data: bytes, mode=0o600):
        if path.is_symlink():
            raise InstallError('目标配置是符号链接，请人工检查：' + str(path))
        if path not in self.originals:
            backup = None
            if path.exists():
                backup = self.directory / ('%02d-%s' % (len(self.originals), path.name))
                shutil.copy2(path, backup)
                os.chmod(backup, 0o600)
            self.originals[path] = backup
            index = {str(p): str(b) if b else None for p, b in self.originals.items()}
            atomic_write(self.directory / 'files.json', json.dumps(index, indent=2).encode())
        atomic_write(path, data, mode)

    def restore(self):
        for path, backup in reversed(list(self.originals.items())):
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, backup.read_bytes())


def extract_release(archive: Path, target: Path) -> Path:
    if target.exists():
        engines = list(target.glob('*/command_select.py'))
        if len(engines) == 1 and (engines[0].parent / 'config.py').is_file():
            return engines[0].parent
        raise InstallError('已有 release 目录不完整；请保留并检查它，脚本不会覆盖。')
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if sum(entry.file_size for entry in entries) > 3 * 1024**3:
            raise InstallError('原版解压大小异常。')
        for entry in entries:
            name = entry.filename
            if '\\' in entry.orig_filename or not (target / name).resolve().is_relative_to(target.resolve()):
                raise InstallError('ZIP 内含不安全路径。')
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise InstallError('ZIP 内含符号链接。')
        commands = [e.filename for e in entries if e.filename.endswith('/command_select.py')]
        if len(commands) != 1 or len(Path(commands[0]).parts) != 2:
            raise InstallError('请选择包含 command_select.py 的官方 V4 ZIP，不能使用 V5 源码。')
        root_name = Path(commands[0]).parts[0]
        if not root_name.isidentifier() or root_name + '/config.py' not in package.namelist():
            raise InstallError('原版 V4 包结构不符合调用要求。')
        print('正在校验 ZIP 完整性……', flush=True)
        if package.testzip() is not None:
            raise InstallError('ZIP 校验失败，请重新上传完整文件。')
        package.extractall(target)
    return target / root_name


def file_image_config(source: str) -> str:
    tree = ast.parse(source)
    values = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'RETURN_PIC_TYPE' for t in node.targets)]
    if len(values) != 1 or values[0].lineno != values[0].end_lineno:
        raise InstallError('无法唯一定位 RETURN_PIC_TYPE，请人工设为 file。')
    value = values[0]
    lines = source.splitlines(keepends=True)
    line = lines[value.lineno - 1].encode('utf-8')
    lines[value.lineno - 1] = (line[:value.col_offset] + b"'file'" + line[value.end_col_offset:]).decode('utf-8')
    return ''.join(lines)


def bridge_token(path: Path) -> str:
    if path.exists():
        match = re.search(r'^WOWS_KOKOMI_BRIDGE_TOKEN=([A-Za-z0-9_-]{24,})$', path.read_text(), re.M)
        if not match:
            raise InstallError('已有 bridge.env 格式不同；请人工核对，脚本不会替换已有密钥。')
        return match[1]
    return secrets.token_urlsafe(32)


def systemctl(*args):
    result = subprocess.run(['systemctl', *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=100)
    if result.returncode:
        raise InstallError('systemctl 操作失败：' + args[0] + '；请在服务器本地查看相关服务日志。')
    return result.stdout.decode().strip()


def health(token):
    request = urllib.request.Request('http://127.0.0.1:19090/health',
                                     headers={'Authorization': 'Bearer ' + token})
    for _ in range(30):
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                data = json.load(response)
            if data.get('ok') is True and data.get('dispatcher_loaded') is True and data.get('protocol') == 1:
                return
            raise InstallError('19090 上的服务不是预期 V4 桥接。')
        except urllib.error.HTTPError:
            raise InstallError('桥接鉴权失败或端口被其他服务占用。') from None
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise InstallError('桥接未启动。请本地检查：journalctl -u kokomi-bridge.service -n 40 --no-pager')


def install(base: Path, astrbot: Path):
    plugin = astrbot / 'data/plugins/astrbot_plugin_wows_captain'
    config = astrbot / 'data/config/astrbot_plugin_wows_captain_config.json'
    python = base / '.venv/bin/python'
    astr_python = Path('/root/.local/share/uv/tools/astrbot/bin/python')
    for path in [python, astr_python, plugin / 'bridge/kokomi_server.py', plugin / 'core/kokomi.py', config]:
        if not path.is_file():
            raise InstallError('缺少必要文件：' + str(path))
    settings = json.loads(config.read_text(encoding='utf-8-sig'))
    if not isinstance(settings, dict):
        raise InstallError('插件配置不是 JSON 对象。')
    systemctl('is-active', 'astrbot.service')
    dependencies = subprocess.run([str(python), '-c', 'import PIL,cv2,httpx,aiohttp'], capture_output=True)
    if dependencies.returncode:
        raise InstallError('独立环境缺少依赖，请检查 /opt/kokomi-v4/deps-mirror.log。')
    engine = extract_release(base / 'Kokomi_Bot-full.zip', base / 'release')
    backups = base / 'backups'
    backups.mkdir(exist_ok=True, mode=0o700)
    changes = Changes(backups / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)))
    print('备份目录：' + str(changes.directory), flush=True)
    token_path = base / 'bridge.env'
    token = bridge_token(token_path)
    changes.write(token_path, ('WOWS_KOKOMI_BRIDGE_TOKEN=' + token + '\n').encode())
    original = engine / 'config.py'
    changes.write(original, file_image_config(original.read_text(encoding='utf-8-sig')).encode('utf-8'))
    unit = Path('/etc/systemd/system/kokomi-bridge.service')
    unit_text = f'''[Unit]
Description=Kokomi V4 bridge for AstrBot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={base}
EnvironmentFile={token_path}
ExecStart={python} {plugin}/bridge/kokomi_server.py --kokomi-root {engine} --host 127.0.0.1 --port 19090
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=multi-user.target
'''
    changes.write(unit, unit_text.encode(), 0o644)
    systemctl('daemon-reload')
    systemctl('restart', 'kokomi-bridge.service')
    health(token)
    print('BRIDGE_HEALTH_OK：已加载真实 V4 dispatcher，仅监听本机。', flush=True)
    # Test the installed plugin client in AstrBot's actual Python environment.
    # The result is private; a text error from upstream is not proof of help success.
    probe = '''import asyncio, json, os
from pathlib import Path
from core.kokomi import KokomiClient
async def main():
    output=Path(os.environ['KOKOMI_PROBE_DIR'])
    client=KokomiClient('http://127.0.0.1:19090',os.environ['WOWS_KOKOMI_BRIDGE_TOKEN'],output)
    try:
        result=await client.send('wws help','0',channel_id='0')
        path=output/'help-result.json'
        path.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        path.chmod(0o600)
        print('HELP_RESPONSE_RECEIVED:'+result['type'])
    finally:
        await client.close()
asyncio.run(main())
'''
    private = base / 'verification'
    private.mkdir(mode=0o700, exist_ok=True)
    env = dict(os.environ, WOWS_KOKOMI_BRIDGE_TOKEN=token, KOKOMI_PROBE_DIR=str(private))
    result = subprocess.run([str(astr_python), '-c', probe], cwd=plugin, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=115)
    if result.returncode:
        atomic_write(private / 'probe-error.log', result.stderr)
        raise InstallError('原生 help 调用失败，尚未修改 AstrBot 配置。本地诊断：' + str(private / 'probe-error.log'))
    print(result.stdout.decode().strip(), flush=True)
    # Only after both health and real command transport work, connect AstrBot.
    connection = Changes(backups / ('astrbot-' + secrets.token_hex(6)))
    dropin = Path('/etc/systemd/system/astrbot.service.d/99-kokomi-bridge-env.conf')
    try:
        settings.update(kokomi_bridge_url='http://127.0.0.1:19090',
                        kokomi_token_env='WOWS_KOKOMI_BRIDGE_TOKEN', kokomi_platform='qq_bot')
        connection.write(config, (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode())
        connection.write(dropin, ('[Service]\nEnvironmentFile=' + str(token_path) + '\n').encode(), 0o644)
        systemctl('daemon-reload')
        systemctl('restart', 'astrbot.service')
        time.sleep(5)
        systemctl('is-active', 'astrbot.service')
        pid = systemctl('show', 'astrbot.service', '-p', 'MainPID', '--value')
        expected = ('WOWS_KOKOMI_BRIDGE_TOKEN=' + token).encode()
        if expected not in Path('/proc/' + pid + '/environ').read_bytes().split(b'\0'):
            raise InstallError('AstrBot 进程未读取到桥接密钥。')
    except Exception:
        connection.restore()
        systemctl('daemon-reload')
        systemctl('restart', 'astrbot.service')
        raise
    systemctl('enable', 'kokomi-bridge.service')
    print('ASTRBOT_CONNECTED：已保存桥接配置，AstrBot 进程已读取密钥。', flush=True)
    print('下一步：在机器人聊天里发送 /wws help，确认收到的是帮助；再按原版帮助绑定账号并查询。')
    print('帮助响应保存于 ' + str(private / 'help-result.json') + '，不要公开其中的原始错误或凭据。')
    print('帮助响应不等于上游账号、历史战绩和全服榜已全部验证。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path('/opt/kokomi-v4'))
    parser.add_argument('--astrbot-root', type=Path, default=Path('/opt/astrbot'))
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.exit(2, '请在目标 Linux 服务器的 root 终端执行。\n')
    if any(re.search(r'[^A-Za-z0-9_./-]', str(p)) for p in [args.base, args.astrbot_root]):
        parser.exit(2, '安装路径仅支持英文、数字、下划线、点、斜线和短横线。\n')
    try:
        install(args.base.resolve(), args.astrbot_root.resolve())
    except Exception as exc:
        # Never echo subprocess or upstream exception details containing secrets.
        message = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        parser.exit(1, 'INSTALL_INCOMPLETE：' + message + '\n')


if __name__ == '__main__':
    main()
