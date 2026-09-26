"""Run the classic room server in the app container's network namespace only.

No container restart or Node installation is required. After joining the network
namespace, drop to nobody before running the pinned server and ws dependency.
"""
import json
import os
import subprocess

info = json.loads(subprocess.check_output([
    '/usr/bin/docker', 'inspect', 'Odysseia_Guidance'
]))[0]
assert info['State']['Running'] and info['State']['Pid'] > 0
os.execv('/usr/bin/nsenter', [
    'nsenter', '--target', str(info['State']['Pid']), '--net',
    '/usr/bin/setpriv', '--reuid=65534', '--regid=65534', '--clear-groups', '--no-new-privs',
    '/usr/bin/node', '/opt/Odysseia-Guidance/data/noname/server/activity-server.cjs',
    '--port', '18082',
])
