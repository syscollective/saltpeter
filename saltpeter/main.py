import signal
import os

# ... (rest of the existing code) ...

def timeout(which, process):
    global processlist
    if which == 'hard':
        print(f'Process {process.name} is about to reach hard timeout! It will be killed soon!')
        os.kill(process.pid, signal.SIGKILL)  # Forcefully kill the process
        processlist[process.name]['hard_timeout'] += timedelta(minutes=5)
    if which == 'soft':
        print(f'Process {process.name} reached soft timeout!')
        processlist[process.name]['soft_timeout'] += timedelta(minutes=5)

# ... (rest of the existing code) ...
