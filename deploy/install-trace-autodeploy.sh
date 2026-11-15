#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

if [[ $(id -u) -ne 0 ]]; then
    echo 'Run this installer with sudo.' >&2
    exit 1
fi

trace_source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
trace_unit_dir="$trace_source_dir/systemd"
if [[ ! -f "$trace_unit_dir/trace-deploy.service" ]]; then
    trace_unit_dir="$trace_source_dir"
fi
for trace_source_file in "$trace_source_dir/trace-deploy.py" "$trace_unit_dir/trace-deploy.service" "$trace_unit_dir/trace-deploy.timer"; do
    if [[ ! -f "$trace_source_file" ]]; then
        echo 'A deployment file is missing; copy all four files before installing.' >&2
        exit 1
    fi
done

for trace_command in /usr/bin/python3 /usr/bin/git /usr/bin/ssh /usr/sbin/runuser /usr/bin/systemctl /usr/bin/systemd-analyze; do
    if [[ ! -x "$trace_command" ]]; then
        echo 'A required Ubuntu command is missing.' >&2
        exit 1
    fi
done
if [[ ! -d /run/systemd/system || ! -d /opt/trace/.git ]]; then
    echo 'This installer needs the existing Ubuntu TRACE VM and checkout.' >&2
    exit 1
fi
if ! /usr/sbin/runuser -u trace -- test -s /home/trace/.ssh/id_ed25519 || ! /usr/sbin/runuser -u trace -- test -s /home/trace/.ssh/known_hosts; then
    echo 'The trace account needs its existing deploy key and verified GitHub known_hosts.' >&2
    exit 1
fi

# Stop future ticks, but never interrupt a deployment transaction already running.
/usr/bin/systemctl stop trace-deploy.timer 2>/dev/null || true
trace_active_state=$(/usr/bin/systemctl show trace-deploy.service --property=ActiveState --value 2>/dev/null || true)
case "$trace_active_state" in
    active|activating|deactivating|reloading)
        echo 'A deployment is running. Wait for it to finish, then rerun this installer.' >&2
        exit 1
        ;;
esac

/usr/bin/python3 -I -B -c 'import pathlib,sys; compile(pathlib.Path(sys.argv[1]).read_bytes(), sys.argv[1], "exec")' "$trace_source_dir/trace-deploy.py"
install -d -o root -g root -m 755 /usr/local/lib/trace
install -d -o root -g root -m 700 /var/lib/trace-deploy
install -o root -g root -m 644 "$trace_source_dir/trace-deploy.py" /usr/local/lib/trace/deploy_trace.py
install -o root -g root -m 644 "$trace_unit_dir/trace-deploy.service" /etc/systemd/system/trace-deploy.service
install -o root -g root -m 644 "$trace_unit_dir/trace-deploy.timer" /etc/systemd/system/trace-deploy.timer
/usr/bin/systemd-analyze verify /etc/systemd/system/trace-deploy.service /etc/systemd/system/trace-deploy.timer
/usr/bin/systemctl daemon-reload

echo 'Checking the current release. Configuration values are not printed.'
/usr/bin/python3 -I -B /usr/local/lib/trace/deploy_trace.py --check
echo 'Running the first check before enabling automatic deployment.'
if ! /usr/bin/systemctl start trace-deploy.service; then
    echo 'The first deployment check failed; the timer has not been enabled.' >&2
    echo 'Inspect: sudo journalctl -u trace-deploy -n 40 --no-pager -o cat' >&2
    exit 1
fi
/usr/bin/systemctl enable --now trace-deploy.timer
echo 'Automatic code deployment enabled. main is checked every minute.'
echo 'Status: systemctl list-timers trace-deploy.timer --no-pager'
echo 'Logs: sudo journalctl -u trace-deploy -n 40 --no-pager -o cat'
