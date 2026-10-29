# Start, stop and view all services

Run these commands in Ubuntu/WSL from the project:

~~~bash
cd /home/administrator/projects/nse-fo-low-latency-decoder
~~~

| Action | Command |
| --- | --- |
| Start all five services | bash start_all.sh |
| Stop all five services and pause BOD recovery | bash stop_all.sh |
| Follow all service logs | bash logs_all.sh |

Services: decoder 9001, KE2 adapter 9002, Gamma simulator 8000,
indicative server 8100 and FastAPI tester 9003. A configured simulator port
in data/bod/active.json is honored.

Start resumes BOD recovery and uses the existing supervisor. A healthy stack
is reused; repeated start does not deliberately restart it. The command
returns after readiness, and the services run in the background.
BOD validation and its weekday/08:45 IST policy still apply: a pending BOD
is reported as pending rather than as five running services. This environment
already has the BOD supervisor installed.

Stop writes the operator pause first, waits for an in-progress BOD publication,
stops the supervised stack and any remaining positively identified project
listeners, then checks that their ports are closed. Unknown processes are
reported and left alone. The pause persists across scheduled BOD checks
until an explicit start/refresh removes it.

View logs from another terminal. Ctrl+C stops only the viewer. It follows
the latest launch directory automatically after future service restarts,
and includes BOD and control audit logs.

~~~bash
bash logs_all.sh --once
bash logs_all.sh --once --lines 100
~~~

The one-time view also includes the supervisor journal, displayed in IST.
The default live view follows the saved files. Run a script with --help
for its options.

## Where logs are saved

All filesystem paths below are relative to:

~~~text
/home/administrator/projects/nse-fo-low-latency-decoder/
~~~

Each new service launch creates:

~~~text
logs/system-YYYYMMDD-HHMMSS-PID/
    decoder.log
    ke2-adapter.log
    simulator.log
    indicative-close.log
    api-tester.log
~~~

The directory timestamp is IST. Additional logs:

| Location | Contents |
| --- | --- |
| logs/bod/YYYYMMDD.log | Scheduled BOD checks, input refresh and failures |
| logs/control/start-YYYYMMDD-HHMMSS-PID.log | Start command output |
| logs/control/stop-YYYYMMDD-HHMMSS-PID.log | Stop command output |
| logs/service-control.jsonl | Timestamped control requests/results, username, UID, PID and parent PID |
| dalal_backend.log | Decoder's additional backend diagnostics |
| data/bod/status.json | Latest BOD state; this file is current status rather than history |

Control scripts and direct BOD --start/--stop/--refresh commands now write
audit requests. Direct systemctl commands still appear in the supervisor
journal and may lack actor identification. Audit logging begins with this
change; it cannot recover the caller of older restart events.

Read the journal separately if needed:

~~~bash
TZ=Asia/Kolkata journalctl --user -u nse-ke2-stack.service -n 100 --no-pager
~~~

From Windows Explorer, open:

~~~text
\\wsl.localhost\Ubuntu\home\administrator\projects\nse-fo-low-latency-decoder\logs
~~~

Stopping services retains their logs and databases. Service control does not
submit broker orders or add a simulator manual-square-off action.

## Verification

Six isolated control tests and five BOD tests passed. They cover manager-lock
coordination during stop, verified process selection, start reuse, log-directory
switching, Ctrl+C behavior and audit entries. Shell syntax and the real
read-only log-view command were checked. The existing live supervisor PID
was unchanged during these checks.
