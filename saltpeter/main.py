#!/usr/bin/env python3

from saltpeter import ui_endpoint, version
from saltpeter import machines_endpoint
import json
import os
import argparse
import re
import yaml
import time
import traceback
from sys import exit
from datetime import datetime,timedelta,date,timezone
from crontab import CronTab
import multiprocessing
import threading
#from pprint import pprint

# Global debug flag (set from params after config load)
debug_enabled = False
debug_flag_shared = None  # Will be set to Manager.Value in main()

def debug_print(message, flush=True):
    """Print message only if debug mode is enabled"""
    # Use shared flag if available, otherwise fall back to process-local flag
    if debug_flag_shared and hasattr(debug_flag_shared, 'value'):
        if debug_flag_shared.value:
            print(message, flush=flush)
    elif debug_enabled:
        print(message, flush=flush)

def readconfig(configdir):
    global bad_files
    crons = {}
    saltpeter_maintenance = {'global': False, 'machines': []}
    saltpeter_config_defaults = {
        'logdir': '/var/log/saltpeter',
        'debug': False,
        'api_ws': False,
        'api_ws_port': 8888,
        'api_ws_bind_addr': '0.0.0.0',
        'machines_ws_port': 8889,
        'machines_ws_bind_addr': '0.0.0.0',
        'default_saltpeter_server_host': os.uname().nodename,
        'default_wrapper_path': '/usr/local/bin/sp_wrapper.py',
        'default_wrapper_loglevel': 'normal',
        'default_wrapper_logdir': '/var/log/sp_wrapper',
        'elasticsearch': '',
        'opensearch': '',
        'elasticsearch_index': 'saltpeter',
        'opensearch_index': 'saltpeter'
    }
    saltpeter_config = {}
    
    for f in os.listdir(configdir):
        if not re.match('^.+\.yaml$',f):
            continue
        try:
            config_string = open(configdir+'/'+f,'r').read()
            group = f[0:-5]
            loaded_config = yaml.load(config_string, Loader=yaml.FullLoader)
            add_config = {}
            for cron in loaded_config:
                if cron == 'saltpeter_maintenance':
                    if 'global' in loaded_config[cron] and isinstance(loaded_config[cron]['global'], bool):
                        saltpeter_maintenance['global'] = loaded_config[cron]['global']
                    if 'machines' in loaded_config[cron] and isinstance(loaded_config[cron]['machines'], list):
                        saltpeter_maintenance['machines'] = loaded_config[cron]['machines']
                elif cron == 'saltpeter_config':
                    # Load global saltpeter configuration
                    saltpeter_config.update(loaded_config[cron])
                elif parsecron(cron,loaded_config[cron]) is not False:
                    add_config[cron] = loaded_config[cron]
                    add_config[cron]['group'] = group
                    
                    # Apply defaults for missing cron fields
                    if 'sec' not in add_config[cron]:
                        add_config[cron]['sec'] = 0
                    if 'year' not in add_config[cron]:
                        add_config[cron]['year'] = '*'
                    if 'utc' not in add_config[cron]:
                        add_config[cron]['utc'] = False
                    if 'timeout' not in add_config[cron]:
                        add_config[cron]['timeout'] = 3600 
            crons.update(add_config)
            if f in bad_files:
                bad_files.remove(f)
        except Exception as e:
            if f not in bad_files:
                print('[MAIN] Could not parse file %s: %s' % (f,e), flush=True)
                bad_files.append(f)
    
    # Apply defaults for any missing saltpeter_config keys
    for key, default_value in saltpeter_config_defaults.items():
        if key not in saltpeter_config:
            saltpeter_config[key] = default_value
    
    return (crons, saltpeter_maintenance, saltpeter_config)


def parsecron(name, data, time=datetime.now(timezone.utc)):
    try:
        dow = data['dow']
        dom = data['dom']
        mon = data['mon']
        hour = data['hour']
        minute = data['min']

        ### TODO: replace this with a timezone specification
        if 'utc' in data:
            utc = data['utc']
        else:
            utc = False
    except KeyError as e:
        if name not in bad_crons:
            print('[MAIN] Missing required %s property from "%s"' % (e,name), flush=True)
            bad_crons.append(name)
        return False
    ret = {}
    if 'sec' in data:
        sec = data['sec']
    else:
        sec = 0
    if 'year' in data:
        year = data['year']
    else:
        year = '*'
    if 'hard_timeout' in data and data['hard_timeout'] is not None:
        ret['timeout'] = data['hard_timeout']
    if 'timeout' in data and data['timeout'] is not None:
        ret['timeout'] = data['timeout']
    
    # Ensure timeout always has a value (default 3600s if not explicitly set)
    # Note: timeout=0 means unlimited (no timeout)
    if 'timeout' not in ret:
        ret['timeout'] = 3600


    try:
        entry = CronTab('%s %s %s %s %s %s %s' % (sec, minute, hour, dom, mon, dow, year))
    except Exception as e:
        if name not in bad_crons:
            print('[MAIN] Could not parse execution time in "%s":' % name, flush=True)
            print('[MAIN]', e, flush=True)
            bad_crons.append(name)
        return False

    if name in bad_crons:
        bad_crons.remove(name)

    if utc:
        ret['nextrun'] = entry.next(now=time,default_utc=True)
    else:
        ### TODO: replace this with a timezone specification
        ret['nextrun'] = entry.next(now=time,default_utc=False)

    return ret

def processstart(chunk,name,group,procname,state):
    results = {}

    for target in chunk:
        starttime = datetime.now(timezone.utc)
        result = { 'ret': '', 'retcode': '',
            'starttime': starttime, 'endtime': ''}
        #do this crap to propagate changes; this is somewhat acceptable since this object is not modified anywhere else
        job_state = state[name]
        if 'results' not in job_state:
            job_state['results'] = {}
        job_state['results'][target] = result
        state[name] = job_state

        log(cron=name, group=group, what='machine_start', instance=procname,
                time=starttime, machine=target)


def handle_wrapper_failure(machine, retcode, output, name, group, procname, running, state):
    """
    Handle wrapper startup failure: update state, log, and remove from running list
    """
    now = datetime.now(timezone.utc)
    
    # Update state with failure
    job_state = state[name]
    if 'results' not in job_state:
        job_state['results'] = {}
        
    # Always create/update entry with endtime to prevent heartbeat monitoring
    if machine not in job_state['results']:
        job_state['results'][machine] = {
            'ret': '',
            'retcode': '',
            'starttime': now,
            'endtime': ''
        }
        
    job_state['results'][machine]['endtime'] = now
    job_state['results'][machine]['retcode'] = retcode
    job_state['results'][machine]['ret'] = f"Wrapper execution failed:\n{output}"
    state[name] = job_state
    
    # Log the failure
    log(what='machine_result', cron=name, group=group,
        instance=procname, machine=machine, code=retcode,
        out=f"Wrapper execution failed:\n{output}", time=now)
    
    # Remove from running list
    if procname in running and 'machines' in running[procname]:
        if machine in running[procname]['machines']:
            tmprunning = dict(running[procname])
            tmprunning['machines'] = [m for m in tmprunning['machines'] if m != machine]
            running[procname] = tmprunning


def process_wrapper_results(wrapper_results, name, group, procname, running, state, expected_targets):
    """
    Process salt.cmd() results for wrapper execution
    Returns list of successfully started targets
    Handles missing targets (not in wrapper_results) as startup failures
    """
    debug_print(f"[SALT DEBUG] wrapper_results type: {type(wrapper_results)}")
    debug_print(f"[SALT DEBUG] wrapper_results content: {wrapper_results}")
    debug_print(f"[SALT DEBUG] expected_targets: {expected_targets}")
    
    targets_confirmed_started = []
    
    for machine, result in wrapper_results.items():
        debug_print(f"[SALT DEBUG] Processing {machine}: type={type(result)}, result={result}")
        
        if isinstance(result, dict) and 'retcode' in result:
            retcode = result['retcode']
            # Capture both stdout and stderr from Salt execution
            stdout = result.get('ret', '')
            stderr = result.get('stderr', '')
            # Combine stdout and stderr for complete output
            output = stdout
            if stderr:
                output += f"\n--- STDERR ---\n{stderr}"
            
            if retcode == 0:
                # Wrapper started successfully
                targets_confirmed_started.append(machine)
                print(f"[JOB:{procname}] Wrapper started successfully on {machine}", flush=True)
            else:
                # Wrapper failed to start
                print(f"[JOB:{procname}] Wrapper failed to start on {machine} (retcode={retcode})", flush=True)
                handle_wrapper_failure(machine, retcode, output, name, group, procname, running, state)
        else:
            # No response or malformed response
            print(f"[JOB:{procname}] No response from {machine} - result type: {type(result)}", flush=True)
            handle_wrapper_failure(machine, 255, "Target did not respond", name, group, procname, running, state)
    
    # Handle targets missing from wrapper_results (Salt timeout or no response)
    missing_targets = set(expected_targets) - set(wrapper_results.keys())
    if missing_targets:
        print(f"[JOB:{procname}] Targets missing from Salt response (timeout during wrapper startup): {missing_targets}", flush=True)
        for machine in missing_targets:
            handle_wrapper_failure(machine, 255, "Salt timeout - wrapper did not start within 30s", 
                                 name, group, procname, running, state)
    
    return targets_confirmed_started


def processresults_websocket(name, group, procname, running, state, targets, timeout=None, state_update_queues=None, manager=None, salt_result=None):
    """
    Wait for WebSocket-based job results with optional timeout
    Monitors state updates from WebSocket server for job completion
    SINGLE WRITER pattern: This function is the ONLY place that modifies state[name]
    
    Args:
        salt_result: Optional dict with 'completed' and 'result'/'error' from async Salt thread
    """
    start_time = time.time()
    queue_timeout = 0.1  # Block on queue for 100ms, then check other conditions
    
    # Default timeout of 1 hour if not specified
    # Note: timeout=0 means unlimited (no timeout)
    if timeout is None:
        timeout = 3600
    
    # Heartbeat timeout should be job timeout + grace period
    # If timeout=0 (unlimited), still need heartbeat timeout for connection loss detection
    if timeout == 0:
        heartbeat_timeout = float('inf')  # Never timeout on heartbeat if job has unlimited timeout
    else:
        heartbeat_timeout = timeout + 30
    
    # Get or create queue for this job instance to receive updates from WebSocket
    # Queue should already exist (created before wrapper launch), but create if missing
    import queue
    if state_update_queues is not None and procname in state_update_queues:
        update_queue = state_update_queues[procname]
        debug_print(f"[JOB:{procname}] Using existing state update queue", flush=True)
    elif manager is not None and state_update_queues is not None:
        update_queue = manager.Queue()
        state_update_queues[procname] = update_queue
        print(f"[JOB:{procname}] WARNING - Queue created late (should exist before wrapper launch)", flush=True)
    else:
        print(f"[JOB:{procname}] ERROR - manager or state_update_queues not provided", flush=True)
        return
    
    # Monitor WebSocket results for job completion
    pending_targets = set(targets)
    connected_targets = set()  # Track which targets have connected via WebSocket
    startup_verified = {}  # Track startup verification for each target
    last_heartbeat = {}  # Track last activity time for each target
    job_start_time = time.time()
    startup_window = 30  # 30 second window to verify wrapper startup
    
    # Initialize tracking for all targets
    for tgt in targets:
        last_heartbeat[tgt] = time.time()
        startup_verified[tgt] = False
    
    # Track when kill was initiated
    kill_initiated_time = None
    
    while pending_targets:
        # Process state updates from WebSocket (SINGLE WRITER pattern)
        try:
            # Block waiting for first message (with timeout for other checks)
            try:
                msg = update_queue.get(timeout=queue_timeout)
                
                # Process this message
                msg_type = msg.get('type')
                machine = msg.get('machine')
                timestamp = msg.get('timestamp')
                
                # Only this function modifies state[name] - no race conditions!
                job_state = state[name]
                if 'results' not in job_state:
                    job_state['results'] = {}
                    
                if msg_type == 'start':
                        # Initialize machine result
                        if machine not in job_state['results']:
                            job_state['results'][machine] = {}
                        job_state['results'][machine]['starttime'] = timestamp
                        job_state['results'][machine]['ret'] = ''
                        job_state['results'][machine]['retcode'] = ''
                        job_state['results'][machine]['endtime'] = ''
                        job_state['results'][machine]['wrapper_version'] = msg.get('wrapper_version', 'unknown')
                        last_heartbeat[machine] = time.time()
                        connected_targets.add(machine)
                        startup_verified[machine] = True
                        print(f"[JOB:{procname}] Machine {machine} started", flush=True)
                    
                elif msg_type == 'heartbeat':
                        # Update heartbeat timestamp
                        if machine not in job_state['results']:
                            job_state['results'][machine] = {}
                        job_state['results'][machine]['last_heartbeat'] = timestamp
                        last_heartbeat[machine] = time.time()
                    
                elif msg_type == 'output':
                        # Append output
                        if machine not in job_state['results']:
                            job_state['results'][machine] = {'ret': '', 'retcode': '', 'starttime': timestamp, 'endtime': ''}
                            
                        current_output = job_state['results'][machine].get('ret', '')
                        new_data = msg.get('data', '')
                        job_state['results'][machine]['ret'] = current_output + new_data
                            
                        seq = msg.get('seq')
                        if seq is not None:
                            job_state['results'][machine]['last_output_seq'] = seq
                        last_heartbeat[machine] = time.time()
                    
                elif msg_type == 'complete':
                        # Mark completion
                        if machine not in job_state['results']:
                            job_state['results'][machine] = {}
                            
                        # Get existing data
                        starttime = job_state['results'][machine].get('starttime', timestamp)
                        output = job_state['results'][machine].get('ret', '')
                        wrapper_version = job_state['results'][machine].get('wrapper_version')
                        last_heartbeat_val = job_state['results'][machine].get('last_heartbeat')
                            
                        # Set final result
                        job_state['results'][machine] = {
                            'ret': output,
                            'retcode': msg.get('retcode'),
                            'starttime': starttime,
                            'endtime': timestamp
                        }
                        if wrapper_version:
                            job_state['results'][machine]['wrapper_version'] = wrapper_version
                        if last_heartbeat_val:
                            job_state['results'][machine]['last_heartbeat'] = last_heartbeat_val
                            
                        print(f"[JOB:{procname}] Machine {machine} completed with retcode {msg.get('retcode')}", flush=True)
                    
                elif msg_type == 'error':
                    # Mark error
                    job_state['results'][machine] = {
                        'ret': f"Wrapper error: {msg.get('error')}",
                        'retcode': 255,
                        'starttime': timestamp,
                        'endtime': timestamp
                    }
                    print(f"[JOB:{procname}] Machine {machine} error: {msg.get('error')}", flush=True)
                
                elif msg_type == 'force_kill':
                    # Force kill after grace period expired
                    if machine in job_state['results']:
                        result = job_state['results'][machine]
                        if not result.get('endtime') or result.get('endtime') == '':
                            result['endtime'] = datetime.now(timezone.utc)
                            result['retcode'] = 143
                            if 'ret' not in result:
                                result['ret'] = ''
                            result['ret'] += "\n[Job terminated by user request - grace period expired after 30s]\n"
                            print(f"[JOB:{procname}] Forcefully killed {machine} after 30s grace period", flush=True)
                    
                # Commit state update (SINGLE WRITER - safe without deepcopy)
                state[name] = job_state
                
                # Debug: verify state was actually committed to Manager dict
                readback = state.get(name)
                if readback and 'results' in readback and machine in readback['results']:
                    debug_print(f"[JOB:{procname}][{machine}] State committed: type={msg_type}, has_results=True", flush=True)
                else:
                    print(f"[JOB:{procname}][{machine}] ERROR - State NOT in Manager dict after commit! type={msg_type}", flush=True)
                
                # After processing first message, drain any remaining messages
                while True:
                    try:
                        msg = update_queue.get_nowait()
                        msg_type = msg.get('type')
                        machine = msg.get('machine')
                        timestamp = msg.get('timestamp')
                        
                        # Process same logic as above
                        job_state = state[name]
                        if 'results' not in job_state:
                            job_state['results'] = {}
                            
                        if msg_type == 'start':
                            if machine not in job_state['results']:
                                job_state['results'][machine] = {}
                            job_state['results'][machine]['starttime'] = timestamp
                            job_state['results'][machine]['ret'] = ''
                            job_state['results'][machine]['retcode'] = ''
                            job_state['results'][machine]['endtime'] = ''
                            job_state['results'][machine]['wrapper_version'] = msg.get('wrapper_version', 'unknown')
                            last_heartbeat[machine] = time.time()
                            connected_targets.add(machine)
                            startup_verified[machine] = True
                            print(f"[JOB:{procname}] Machine {machine} started", flush=True)
                            
                        elif msg_type == 'heartbeat':
                            if machine not in job_state['results']:
                                job_state['results'][machine] = {}
                            job_state['results'][machine]['last_heartbeat'] = timestamp
                            last_heartbeat[machine] = time.time()
                            
                        elif msg_type == 'output':
                            if machine not in job_state['results']:
                                job_state['results'][machine] = {'ret': '', 'retcode': '', 'starttime': timestamp, 'endtime': ''}
                            current_output = job_state['results'][machine].get('ret', '')
                            job_state['results'][machine]['ret'] = current_output + msg.get('data', '')
                            seq = msg.get('seq')
                            if seq is not None:
                                job_state['results'][machine]['last_output_seq'] = seq
                            last_heartbeat[machine] = time.time()
                            
                        elif msg_type == 'complete':
                            if machine not in job_state['results']:
                                job_state['results'][machine] = {}
                            starttime = job_state['results'][machine].get('starttime', timestamp)
                            output = job_state['results'][machine].get('ret', '')
                            wrapper_version = job_state['results'][machine].get('wrapper_version')
                            last_heartbeat_val = job_state['results'][machine].get('last_heartbeat')
                            job_state['results'][machine] = {
                                'ret': output,
                                'retcode': msg.get('retcode'),
                                'starttime': starttime,
                                'endtime': timestamp
                            }
                            if wrapper_version:
                                job_state['results'][machine]['wrapper_version'] = wrapper_version
                            if last_heartbeat_val:
                                job_state['results'][machine]['last_heartbeat'] = last_heartbeat_val
                            print(f"[JOB:{procname}] Machine {machine} completed with retcode {msg.get('retcode')}", flush=True)
                            
                        elif msg_type == 'error':
                            job_state['results'][machine] = {
                                'ret': f"Wrapper error: {msg.get('error')}",
                                'retcode': 255,
                                'starttime': timestamp,
                                'endtime': timestamp
                            }
                            print(f"[JOB:{procname}] Machine {machine} error: {msg.get('error')}", flush=True)
                            
                        elif msg_type == 'force_kill':
                            if machine in job_state['results']:
                                result = job_state['results'][machine]
                                if not result.get('endtime') or result.get('endtime') == '':
                                    result['endtime'] = datetime.now(timezone.utc)
                                    result['retcode'] = 143
                                    if 'ret' not in result:
                                        result['ret'] = ''
                                    result['ret'] += "\n[Job terminated by user request - grace period expired after 30s]\n"
                                    print(f"[JOB:{procname}] Forcefully killed {machine} after 30s grace period", flush=True)
                            
                        state[name] = job_state
                        readback = state.get(name)
                        if readback and 'results' in readback and machine in readback['results']:
                            debug_print(f"[JOB:{procname}][{machine}] State committed: type={msg_type}, has_results=True", flush=True)
                        else:
                            print(f"[JOB:{procname}][{machine}] ERROR - State NOT in Manager dict after commit! type={msg_type}", flush=True)
                    
                    except queue.Empty:
                        break  # No more messages
                        
            except queue.Empty:
                pass  # Timeout waiting for first message - continue to other checks
        except Exception as e:
            print(f"[JOB:{procname}] Error processing state update: {e}", flush=True)
            import traceback
            traceback.print_exc()
        
        # Check if stop_signal was set (job killed)
        if procname in running and running[procname].get('stop_signal', False):
            if kill_initiated_time is None:
                kill_initiated_time = time.time()
                print(f"[JOB:{procname}] Stop signal detected, waiting for machines to complete (30s grace period)", flush=True)
            
            # Wait up to 30 seconds for machines to complete after kill
            kill_elapsed = time.time() - kill_initiated_time
            if kill_elapsed > 30:
                print(f"[JOB:{procname}] Kill grace period expired, exiting result monitoring", flush=True)
                break
        
        # Check startup verification window
        elapsed = time.time() - job_start_time
        
        # Only fail fast if Salt returned with an actual error or non-zero retcode
        # Success (retcode=0) means wrapper launched - wait for WebSocket connection
        salt_failed = False
        if salt_result and salt_result.get('completed'):
            if salt_result.get('error'):
                salt_failed = True
            elif salt_result.get('result'):
                # Check if any target has non-zero retcode
                for tgt_name, tgt_result in salt_result['result'].items():
                    if isinstance(tgt_result, dict) and tgt_result.get('retcode') not in (None, 0, '0'):
                        salt_failed = True
                        break
        
        check_now = salt_failed
        
        if check_now or elapsed > startup_window:
            # After startup window, check for targets that never connected
            for tgt in list(pending_targets):
                if not startup_verified[tgt]:
                    # Target never connected - check if Salt has error info
                    salt_error = None
                    if salt_result and salt_result.get('completed'):
                        if salt_result.get('error'):
                            salt_error = salt_result['error']
                        elif salt_result.get('result') and tgt in salt_result['result']:
                            result = salt_result['result'][tgt]
                            if isinstance(result, dict) and result.get('retcode') not in (None, 0, '0'):
                                stderr = result.get('stderr', '').strip()
                                stdout = result.get('stdout', '').strip()
                                salt_error = f"Exit code {result['retcode']}"
                                if stderr:
                                    salt_error += f"\nSTDERR: {stderr}"
                                if stdout:
                                    salt_error += f"\nSTDOUT: {stdout}"
                    
                    now = datetime.now(timezone.utc)
                    error_msg = "Wrapper failed to start" if check_now else "Wrapper failed to connect within 30s"
                    if salt_error:
                        error_msg += f" - {salt_error}"
                    else:
                        error_msg += " (no response from Salt or wrapper)"
                    
                    print(f"[JOB:{procname}] {tgt}: {error_msg}", flush=True)
                    
                    job_state = state[name]
                    if 'results' not in job_state:
                        job_state['results'] = {}
                    job_state['results'][tgt] = {
                        'ret': error_msg,
                        'retcode': 254,  # Special code for startup failure
                        'starttime': job_state.get('last_run', now),
                        'endtime': now
                    }
                    state[name] = job_state
                    
                    log(what='machine_result', cron=name, group=group, instance=procname,
                        machine=tgt, code=254, out=error_msg, time=now)
                    
                    pending_targets.remove(tgt)
                    startup_verified[tgt] = True  # Mark as verified (failed)
        
        # Check if timeout exceeded (with 30s grace period for wrapper to terminate and report)
        # Skip check if timeout=0 (unlimited)
        if timeout > 0 and elapsed > timeout + 30:
            print(f"[JOB:{procname}] Timeout + grace period exceeded for {pending_targets} ({elapsed:.1f}s > {timeout + 30}s)", flush=True)
            
            # Mark remaining targets as timed out
            now = datetime.now(timezone.utc)
            job_state = state[name]
            if 'results' not in job_state:
                job_state['results'] = {}
                
            for tgt in pending_targets:
                if tgt in job_state['results']:
                    starttime = job_state['results'][tgt].get('starttime', now)
                    output = job_state['results'][tgt].get('ret', '')
                else:
                    starttime = job_state.get('last_run', now)
                    output = ''
                    
                output += "\n[SALTPETER ERROR: Job exceeded timeout]\n"
                
                # Preserve existing fields when marking timeout
                if tgt not in job_state['results']:
                    job_state['results'][tgt] = {}
                    job_state['results'][tgt]['starttime'] = starttime
                
                job_state['results'][tgt]['ret'] = output
                job_state['results'][tgt]['retcode'] = 124
                job_state['results'][tgt]['endtime'] = now
                # wrapper_version, last_heartbeat, starttime (if existed), etc. are preserved
                
            state[name] = job_state
            
            # Log timeout for remaining targets
            for tgt in pending_targets:
                output_for_log = ''
                if name in state and 'results' in state[name] and tgt in state[name]['results']:
                    output_for_log = state[name]['results'][tgt].get('ret', '')
                
                log(what='machine_result', cron=name, group=group, instance=procname,
                    machine=tgt, code=124, out=output_for_log, time=now)
                
                # Remove from running list (only copy this procname entry, not entire running dict)
                if procname in running and 'machines' in running[procname]:
                    tmprunning = dict(running[procname])
                    tmprunning['machines'] = [m for m in tmprunning['machines'] if m != tgt]
                    running[procname] = tmprunning
            
            break
        
        # Check which targets have completed or timed out
        now = datetime.now(timezone.utc)
        if name in state and 'results' in state[name]:
            for tgt in list(pending_targets):
                if tgt in state[name]['results']:
                    result = state[name]['results'][tgt]
                        
                    # Update last heartbeat time from state (set by WebSocket server)
                    if 'last_heartbeat' in result and result['last_heartbeat']:
                        # Convert ISO timestamp to Unix timestamp
                        try:
                            hb_time = result['last_heartbeat']
                            if isinstance(hb_time, str):
                                hb_time = datetime.fromisoformat(hb_time)
                            if isinstance(hb_time, datetime):
                                last_heartbeat[tgt] = hb_time.timestamp()
                            else:
                                # Assume it's already a timestamp
                                last_heartbeat[tgt] = float(hb_time)
                        except:
                            pass
                        
                    # Initialize heartbeat timer on first check if we have a starttime
                    if tgt not in last_heartbeat and result.get('starttime'):
                        try:
                            start = result['starttime']
                            if isinstance(start, str):
                                start = datetime.fromisoformat(start)
                            if isinstance(start, datetime):
                                last_heartbeat[tgt] = start.timestamp()
                        except:
                            last_heartbeat[tgt] = time.time()
                        
                    # Check if this target has completed (has endtime)
                    if result.get('endtime') and result['endtime'] != '':
                        debug_print(f"[JOB:{procname}] Target {tgt} completed, removing from pending (retcode: {result.get('retcode')})", flush=True)
                            
                        # Log the completion
                        log(what='machine_result', cron=name, group=group, instance=procname,
                            machine=tgt, code=result.get('retcode', 0), 
                            out=result.get('ret', ''), time=result['endtime'])
                            
                        # Remove from running list (only copy this procname entry, not entire running dict)
                        if procname in running and 'machines' in running[procname]:
                            if tgt in running[procname]['machines']:
                                tmprunning = dict(running[procname])
                                tmprunning['machines'] = [m for m in tmprunning['machines'] if m != tgt]
                                running[procname] = tmprunning
                            
                        pending_targets.remove(tgt)
                        continue
                        
                    # Check for heartbeat timeout
                    if tgt in last_heartbeat:
                        time_since_heartbeat = time.time() - last_heartbeat[tgt]
                        if time_since_heartbeat > heartbeat_timeout:
                            print(f"[JOB:{procname}] Heartbeat timeout for {tgt} ({time_since_heartbeat:.1f}s since last activity)", flush=True)
                                
                            # Mark as failed with heartbeat timeout
                            job_state = state[name]
                            starttime = result.get('starttime', now)
                            output = result.get('ret', '')
                            output += f"\n[SALTPETER ERROR: Job lost connection - no heartbeat for {time_since_heartbeat:.0f} seconds]\n"
                            
                            # Preserve existing fields when marking heartbeat timeout
                            if tgt in job_state['results']:
                                job_state['results'][tgt]['ret'] = output
                                job_state['results'][tgt]['retcode'] = 253
                                job_state['results'][tgt]['endtime'] = now
                                # wrapper_version, last_heartbeat, starttime, etc. are preserved
                            else:
                                # Shouldn't happen but handle gracefully
                                job_state['results'][tgt] = {
                                    'ret': output,
                                    'retcode': 253,
                                    'starttime': starttime,
                                    'endtime': now
                                }
                            state[name] = job_state
                                
                            # Log the failure
                            log(what='machine_result', cron=name, group=group, instance=procname,
                                machine=tgt, code=253, out=output, time=now)
                                
                            # Remove from pending
                            pending_targets.remove(tgt)
        
        # If all targets completed, exit
        if not pending_targets:
            print(f"[JOB:{procname}] All targets completed for {name}, exiting monitor loop", flush=True)
            # Signal Salt thread to abandon - no need to wait for it
            if salt_result:
                salt_result['abandoned'] = True
            break
        
        # Loop continues immediately - blocking get() with timeout handles the wait
    
    # Note: Don't clean up queue here - in batch mode, multiple batches share the same queue
    # Queue cleanup happens in run() after all batches complete
    # Note: Don't delete running[procname] here - the caller (run function) manages it
    # In batch mode, multiple batches use the same procname and running entry
def processresults(client,commands,job,name,group,procname,running,state,targets):
    """
    Legacy function for Salt-based job monitoring
    Kept for backwards compatibility or fallback
    """

    import salt.runner
    opts = salt.config.master_config('/etc/salt/master')
    runner = salt.runner.RunnerClient(opts)

    jid = job['jid']
    minions = job['minions']

    rets = client.get_iter_returns(jid, minions, block=False, expect_minions=True,timeout=1)
    failed_returns = False
    kill = False


    for i in rets:
        #process commands in the loop
        for cmd in commands:
            if 'killcron' in cmd:
                if cmd['killcron'] == name:
                    commands.remove(cmd)
                    client.run_job(minions, 'saltutil.term_job', [jid], tgt_type='list')
                    kill = True
        if kill:
            #print('break from kill in returns loop')
            break

        if i is not None:
            m = list(i)[0]
            print('[JOB]', name, i[m], flush=True)
            if 'failed' in i[m] and i[m]['failed'] == True:
                print(f"[JOB:{procname}] Getting info about job {name} jid: {jid} every 10 seconds", flush=True)
                failed_returns = True
                continue
            else:
                r = i[m]['retcode']
                o = i[m]['ret']
            result = { 'ret': o, 'retcode': r, 'starttime': state[name]['results'][m]['starttime'], 'endtime': datetime.now(timezone.utc) }
            job_state = state[name]
            if 'results' not in job_state:
                job_state['results'] = {}
            job_state['results'][m] = result
            state[name] = job_state

            if procname in running and m in running[procname]['machines']:
                tmprunning = running[procname]
                tmprunning['machines'].remove(m)
                running[procname] = tmprunning

            log(what='machine_result',cron=name, group=group, instance=procname, machine=m,
                code=r, out=o, time=result['endtime'])
        #time.sleep(1)

       
    if failed_returns:
        while True:
            #process commands in the loop
            for cmd in commands:
                if 'killcron' in cmd:
                    if cmd['killcron'] == name:
                        commands.remove(cmd)
                        client.run_job(minions, 'saltutil.term_job', [jid], tgt_type='list')
                        kill = True

            if kill:
                #print('break from kill in failed returns loop')
                break

            job_listing = runner.cmd("jobs.list_job",[jid])
            if len(job_listing['Minions']) == len(job_listing['Result'].keys()):
                for m in job_listing['Result'].keys():
                    o = job_listing['Result'][m]['return']
                    r = job_listing['Result'][m]['retcode']
                    result = { 'ret': o, 'retcode': r, 'starttime': state[name]['results'][m]['starttime'], 'endtime': datetime.now(timezone.utc) }
                    send_log = False
                    job_state = state[name]
                    if 'results' not in job_state:
                        job_state['results'] = {}

                    #print(f'state before check if m not in tmprresults: {state[name]}')
                    if m not in job_state['results'] or job_state['results'][m]['endtime'] =='':
                        job_state['results'][m] = result
                        state[name] = job_state

                        if procname in running and m in running[procname]['machines']:
                            tmprunning = running[procname]
                            tmprunning['machines'].remove(m)
                            running[procname] = tmprunning

                        send_log = True

                    if send_log:
                        log(what='machine_result',cron=name, group=group, instance=procname, machine=m,
                            code=r, out=o, time=result['endtime'])

                #print('break from failed returns loop')
                break
            time.sleep(10)


    #print(f'targets: {targets}\nminions: {minions}\nstate: {state[name]}')
    for tgt in targets:
        if tgt not in minions or tgt not in state[name]['results'] or state[name]['results'][tgt]['endtime'] == '':
            #print(f'machine {tgt} has no output, state: {state[name]}')
            now = datetime.now(timezone.utc)
            if tgt in state[name]['results'] and 'starttime' in state[name]['results'][tgt]:
                starttime = state[name]['results'][tgt]['starttime']
            else:
                starttime = state[name]['lastrun']

            log(what='machine_result',cron=name, group=group, instance=procname, machine=tgt,
                code=255, out="Target did not return anything", time=now)

            job_state = state[name]
            job_state['results'][tgt] = { 'ret': "Target did not return anything",
                    'retcode': 255,
                    'starttime': starttime,
                    'endtime': now }
            state[name] = job_state

            if procname in running and m in running[procname]['machines']:
                tmprunning = running[procname]
                tmprunning['machines'].remove(m)
                running[procname] = tmprunning



def run(name, data, procname, running, state, commands, maintenance, state_update_queues=None, manager=None):

    if maintenance['global']:
        log(cron=name, group=data['group'], what='maintenance', instance=procname, time=datetime.now(timezone.utc), out="Global maintenance mode active")
        return

    for instance in running.keys():
        if name == running[instance]['name']:
            log(what='overlap', cron=name, group=data['group'], instance=instance,
                 time=datetime.now(timezone.utc))
            job_state = state[name]
            job_state['overlap'] = True
            state[name] = job_state
            if 'allow_overlap' not in data or data['allow_overlap'] != 'i know what i am doing!':
                return

    # Clear results from previous run at the start
    job_state = state[name]
    job_state['results'] = {}
    state[name] = job_state

    import salt.client
    salt = salt.client.LocalClient()
    targets = data['targets']
    target_type = data['target_type']
    
    # Check if wrapper should be used (default: True)
    use_wrapper = data.get('use_wrapper', True)
    
    # Prepare command arguments based on wrapper usage
    if use_wrapper:
        # Get wrapper script path - use job-specific path if provided, otherwise use default
        wrapper_path = data.get('wrapper_path', params.default_wrapper_path)
        
        # Prepare environment variables for the wrapper
        saltpeter_server_host = data.get('saltpeter_server_host', params.default_saltpeter_server_host)
        websocket_url = f"ws://{saltpeter_server_host}:{params.machines_ws_port}"
        
        # Get wrapper logging configuration - use job-specific values if provided, otherwise use defaults
        wrapper_loglevel = data.get('wrapper_loglevel', params.default_wrapper_loglevel)
        wrapper_logdir = data.get('wrapper_logdir', params.default_wrapper_logdir)
        
        # Check if overlap is allowed (explicit opt-in required)
        allow_overlap = (data.get('allow_overlap') == 'i know what i am doing!')
        
        # Build environment variables to pass to the wrapper
        wrapper_env = {
            'SP_WEBSOCKET_URL': websocket_url,
            'SP_JOB_NAME': name,
            'SP_JOB_INSTANCE_NAME': procname,  # For backwards compatibility
            'SP_JOB_INSTANCE': procname,
            'SP_COMMAND': data['command'],
            'SP_WRAPPER_LOGLEVEL': wrapper_loglevel,
            'SP_WRAPPER_LOGDIR': wrapper_logdir,
            'SP_ALLOW_OVERLAP': 'true' if allow_overlap else 'false',
            'PYTHONUNBUFFERED': '1'  # Force Python subprocesses to be unbuffered
        }
        
        # Optional: custom lockfile path (per-job configuration)
        if 'lockfile' in data:
            wrapper_env['SP_LOCKFILE'] = data['lockfile']
        
        if 'cwd' in data:
            wrapper_env['SP_CWD'] = data['cwd']
        if 'user' in data:
            wrapper_env['SP_USER'] = data['user']
        
        # Always send timeout to wrapper (already has default applied in parsecron)
        wrapper_env['SP_TIMEOUT'] = str(data['timeout'])
        
        # Add custom environment variables from YAML config
        if 'env' in data:
            for key, value in data['env'].items():
                wrapper_env[key] = str(value)
        
        # Build the Salt command to run the wrapper
        cmdargs = [wrapper_path]
        cmdargs.append('env=' + str(wrapper_env))
        
        # Timeout is handled by WebSocket monitoring
        timeout = data.get('timeout', 3600)  # Default 1 hour
    else:
        # Legacy Salt-only mode (no wrapper)
        cmdargs = [data['command']]
        env = {'SP_JOB_NAME': name, 'SP_JOB_INSTANCE_NAME': procname}
        cmdargs.append('env=' + str(env))
        if 'cwd' in data:
            cmdargs.append('cwd=' + data['cwd'])
        if 'user' in data:
            cmdargs.append('runas=' + data['user'])
        if 'timeout' in data:
            cmdargs.append('timeout=' + str(data['timeout']))
            timeout = data['timeout']
        else:
            timeout = 3600

    now = datetime.now(timezone.utc)
    running[procname] = {'started': now, 'name': name, 'machines': []}
    job_state = state[name]
    job_state['last_run'] = now
    job_state['overlap'] = False
    job_state['group'] = data['group']  # Store group for WebSocket handler
    # Clear previous results when starting a fresh run
    if 'results' in job_state:
        del job_state['results']
    state[name] = job_state
    log(cron=name, group=data['group'], what='start', instance=procname, time=now)

    # Initialize running dict BEFORE test.ping so kill commands work during this phase
    running[procname] = {'started': now, 'name': name, 'machines': [], 'stop_signal': False}

    # ping the minions and parse the result
    try:
        minion_ret = salt.cmd(targets, 'test.ping', tgt_type=target_type, timeout=20)
        debug_print(f"[SALT DEBUG] test.ping response type: {type(minion_ret)}")
        debug_print(f"[SALT DEBUG] test.ping response: {minion_ret}")
    except Exception as e:
        error_msg = f"SALT-MASTER ERROR during test.ping: {str(e)}"
        print(f"[MAIN] {error_msg}", flush=True)
        log(cron=name, group=data['group'], what='test_ping_failed', instance=procname, 
            time=datetime.now(timezone.utc), out=error_msg)
        # Continue with empty results - let normal flow handle it
        minion_ret = {}

    # minion_ret is already a dict: {minion_id: True/False}
    targets_up = [m for m, ret in minion_ret.items() if ret is True]
    targets_down = [m for m, ret in minion_ret.items() if ret is not True]
    #print(name, minion_ret)
    #print(name, targets_up)
    ###

    dead_targets = []
    tmpstate = state[name]
    tmpstate['targets'] = targets_up.copy()
    tmpstate['results'] = {}

    # Record non-responsive targets
    for tgt in targets_down:
        tmpstate['results'][tgt] = {'ret': "Target did not respond",
                                    'retcode': 255,
                                    'starttime': now,
                                    'endtime': datetime.now(timezone.utc)}

    state[name] = tmpstate

    # Remove maintenance machines from targets_up and log a message
    for tgt in targets_up.copy():
        if tgt in maintenance['machines']:
            targets_up.remove(tgt)
            log(cron=name, group=data['group'], what='maintenance', instance=procname, time=datetime.now(timezone.utc), machine=tgt, out="Target under maintenance")

    if len(targets_up) == 0:
        log(cron=name, group=data['group'], what='no_machines', instance=procname, time=datetime.now(timezone.utc))
    else:
        # Create queue BEFORE launching wrappers to avoid race condition
        # Do this once here instead of in both batch/non-batch code paths
        if use_wrapper and manager is not None and state_update_queues is not None:
            if procname not in state_update_queues:
                update_queue = manager.Queue()
                state_update_queues[procname] = update_queue
                debug_print(f"[JOB:{procname}] Created state update queue (before wrapper launch)", flush=True)
        
        # Only execute if we have targets
        # Shuffle targets for random selection/distribution
        if ('number_of_targets' in data and data['number_of_targets'] != 0) or \
           ('batch_size' in data and data['batch_size'] != 0):
            import random
            random.shuffle(targets_up)
        
        if 'number_of_targets' in data and data['number_of_targets'] != 0:
            # Select subset of targets
            targets_up = targets_up[:data['number_of_targets']]

        if 'batch_size' in data and data['batch_size'] != 0:
            chunk = []
            for idx, t in enumerate(targets_up):
                # Check stop_signal before each batch iteration
                if procname in running and running[procname].get('stop_signal', False):
                    print(f"[JOB:{procname}] Stop signal detected during batch processing, aborting remaining batches", flush=True)
                    # Just break out of loop - let end of function evaluate success
                    break
                
                chunk.append(t)
                # Execute when chunk is full OR this is the last target
                if len(chunk) == data['batch_size'] or idx == len(targets_up) - 1:

                    try:
                        # Update running dict with current batch (preserve stop_signal)
                        # Check if procname still exists (might have been cleaned up by main loop)
                        if procname not in running:
                            print(f"[JOB:{procname}] Running entry was deleted, stopping batch processing", flush=True)
                            break
                        
                        tmprunning = dict(running[procname])
                        tmprunning['machines'] = chunk
                        running[procname] = tmprunning
                        processstart(chunk, name, data['group'], procname, state)
                        
                        # Check stop_signal before executing wrapper
                        if procname in running and running[procname].get('stop_signal', False):
                            print(f"[JOB:{procname}] Stop signal detected before wrapper execution, skipping batch", flush=True)
                            # Mark machines as killed
                            now = datetime.now(timezone.utc)
                            job_state = state[name]
                            if 'results' not in job_state:
                                job_state['results'] = {}
                            for machine in chunk:
                                if machine in job_state['results']:
                                    job_state['results'][machine]['endtime'] = now
                                    job_state['results'][machine]['retcode'] = 143
                                    job_state['results'][machine]['ret'] = '[Job killed before execution]'
                            state[name] = job_state
                            chunk = []
                            break
                        
                        # Run command via Salt
                        if use_wrapper:
                            # Run salt.cmd in background thread - don't block on Salt
                            # Wrapper WebSocket connection is the source of truth for success
                            debug_print(f"[SALT DEBUG] Starting async salt.cmd on chunk={chunk}, cmdargs={cmdargs}")
                            
                            salt_result = {'completed': False, 'result': None, 'abandoned': False}
                            def run_salt_cmd():
                                try:
                                    result = salt.cmd(chunk, 'cmd.run_all', cmdargs, tgt_type='list', timeout=30)
                                    # Only update if not abandoned (job might have finished)
                                    if not salt_result['abandoned']:
                                        salt_result['result'] = result
                                        salt_result['completed'] = True
                                        debug_print(f"[SALT DEBUG] salt.cmd completed: {result}")
                                except Exception as e:
                                    if not salt_result['abandoned']:
                                        salt_result['completed'] = True
                                        salt_result['error'] = f"SALT-MASTER ERROR: {str(e)}"
                                        print(f"[SALT DEBUG] SALT-MASTER ERROR in salt.cmd: {e}", flush=True)
                            
                            salt_thread = threading.Thread(target=run_salt_cmd, daemon=True)
                            salt_thread.start()
                            
                            # Assume all targets will start - WebSocket connection proves success
                            print(f"[JOB:{procname}] Launched wrapper via Salt on {len(chunk)} target(s), monitoring via WebSocket", flush=True)
                            
                            # Start WebSocket monitoring immediately (real-time streaming)
                            # Pass salt_result so startup failures can be detected and thread can be abandoned
                            processresults_websocket(name, data['group'], procname, running, state, 
                                                    chunk, timeout, state_update_queues, manager, salt_result)
                            
                            # After WebSocket monitoring completes, log Salt results if available
                            # Only if not abandoned and actually completed
                            if not salt_result['abandoned'] and salt_result['completed'] and salt_result.get('result'):
                                debug_print(f"[SALT DEBUG] Salt result (informational): {salt_result['result']}")
                        else:
                            # Legacy mode - use run_job for non-wrapper execution
                            job = salt.run_job(chunk, 'cmd.run', cmdargs, tgt_type='list', listen=False)
                            if chunk:
                                processresults(salt, commands, job, name, data['group'], procname, running, state, chunk)
                        
                        chunk = []
                    except Exception as e:
                        print(f'[MAIN] Exception in run() at "batch_size" for {procname}:', flush=True)
                        print(f'[MAIN] Exception type: {type(e).__name__}', flush=True)
                        print(f'[MAIN] Exception message: {str(e)}', flush=True)
                        print(f'[MAIN] Traceback:', flush=True)
                        traceback.print_exc()
                        chunk = []
        else:
            # Update running dict with all targets (preserve stop_signal)
            tmprunning = dict(running[procname])
            tmprunning['machines'] = targets_up
            running[procname] = tmprunning
            starttime = datetime.now(timezone.utc)
            
            # Initialize state structure BEFORE running Salt to prevent race condition
            # where wrappers send output before state is ready
            processstart(targets_up, name, data['group'], procname, state)

            # Check stop_signal before executing wrapper
            if procname in running and running[procname].get('stop_signal', False):
                print(f"[JOB:{procname}] Stop signal detected before wrapper execution, aborting", flush=True)
                # Mark all machines as killed
                now = datetime.now(timezone.utc)
                job_state = state[name]
                if 'results' not in job_state:
                    job_state['results'] = {}
                for machine in targets_up:
                    if machine in job_state['results']:
                        job_state['results'][machine]['endtime'] = now
                        job_state['results'][machine]['retcode'] = 143
                        job_state['results'][machine]['ret'] = '[Job killed before execution]'
                state[name] = job_state
                # Don't execute Salt - fall through to end for success evaluation
            else:
                try:
                    # Run command via Salt
                    if use_wrapper:
                        # Run salt.cmd in background thread - don't block on Salt
                        # Wrapper WebSocket connection is the source of truth for success
                        debug_print(f"[SALT DEBUG] Starting async salt.cmd on targets_up={targets_up}, cmdargs={cmdargs}")
                        
                        salt_result = {'completed': False, 'result': None, 'abandoned': False}
                        def run_salt_cmd():
                            try:
                                result = salt.cmd(targets_up, 'cmd.run_all', cmdargs, tgt_type='list', timeout=30)
                                # Only update if not abandoned (job might have finished)
                                if not salt_result['abandoned']:
                                    salt_result['result'] = result
                                    salt_result['completed'] = True
                                    debug_print(f"[SALT DEBUG] salt.cmd completed: {result}")
                            except Exception as e:
                                if not salt_result['abandoned']:
                                    salt_result['completed'] = True
                                    salt_result['error'] = f"SALT-MASTER ERROR: {str(e)}"
                                    print(f"[SALT DEBUG] SALT-MASTER ERROR in salt.cmd: {e}", flush=True)
                        
                        salt_thread = threading.Thread(target=run_salt_cmd, daemon=True)
                        salt_thread.start()
                        
                        # Assume all targets will start - WebSocket connection proves success
                        print(f"[JOB:{procname}] Launched wrapper via Salt on {len(targets_up)} target(s), monitoring via WebSocket", flush=True)
                        
                        # Start WebSocket monitoring immediately (real-time streaming)
                        # Pass salt_result so startup failures can be detected and thread can be abandoned
                        processresults_websocket(name, data['group'], procname, running, state, 
                                                targets_up, timeout, state_update_queues, manager, salt_result)
                        
                        # After WebSocket monitoring completes, log Salt results if available
                        # Only if not abandoned and actually completed
                        if not salt_result['abandoned'] and salt_result['completed'] and salt_result.get('result'):
                            debug_print(f"[SALT DEBUG] Salt result (informational): {salt_result['result']}")
                    else:
                        # Legacy mode - use run_job for non-wrapper execution
                        job = salt.run_job(targets_up, 'cmd.run', cmdargs, tgt_type='list', listen=False)
                        if targets_up:
                            processresults(salt, commands, job, name, data['group'], procname, running, state, targets_up)

                except Exception as e:
                    print(f'[MAIN] Exception in run() for {procname}:', flush=True)
                    print(f'[MAIN] Exception type: {type(e).__name__}', flush=True)
                    print(f'[MAIN] Exception message: {str(e)}', flush=True)
                    print(f'[MAIN] Traceback:', flush=True)
                    traceback.print_exc()

    # Clean up running state at the end of job execution
    if procname in running:
        del running[procname]
    
    # Clean up state update queue at the end of all batches
    if use_wrapper and state_update_queues and procname in state_update_queues:
        del state_update_queues[procname]
        debug_print(f"[JOB:{procname}] Cleaned up state update queue", flush=True)
    
    # Evaluate job success ONCE - single source of truth
    # Count failures from results
    job_state = state[name]
    results = job_state.get('results', {})
        
    debug_print(f"[SUCCESS EVAL DEBUG] Job {procname}: results keys = {list(results.keys())}")
    debug_print(f"[SUCCESS EVAL DEBUG] Job {procname}: len(results) = {len(results)}")
        
    failed_count = 0
    total_count = 0
        
    for target, result in results.items():
        debug_print(f"[SUCCESS EVAL DEBUG] Target {target}: retcode={result.get('retcode')}, ret={result.get('ret')}")
        # Only count targets that have completed (retcode is set and not empty)
        if 'retcode' in result and result['retcode'] != '' and result['retcode'] is not None:
            total_count += 1
            # Check if retcode is non-zero (handle both int and string)
            retcode = result['retcode']
            if retcode != 0 and retcode != '0':
                failed_count += 1
                debug_print(f"[SUCCESS EVAL DEBUG] Target {target}: FAILED with retcode={retcode}")
            else:
                debug_print(f"[SUCCESS EVAL DEBUG] Target {target}: SUCCESS with retcode={retcode}")
        else:
            debug_print(f"[SUCCESS EVAL DEBUG] Target {target}: SKIPPED (retcode not set or empty)")
        
    debug_print(f"[SUCCESS EVAL DEBUG] Final counts: total={total_count}, failed={failed_count}, success={total_count - failed_count}")
        
    # Job is successful if no targets failed
    success = (failed_count == 0) and (total_count > 0)
        
    # Store success status in state for UI/API/logs to read
    # Use job_state (already loaded above) instead of tmpstate (stale from start of run())
    job_state['last_success'] = success
    job_state['last_failed_count'] = failed_count
    job_state['last_total_count'] = total_count
        
    state[name] = job_state
    
    # Log end with proper status code from evaluated success
    status_code = 0 if success else 1
    log(cron=name, group=data['group'], what='end', instance=procname, 
        time=datetime.now(timezone.utc), code=status_code,
        out=f"Completed: {total_count - failed_count}/{total_count} targets successful")

def debuglog(content):
    logfile = open(params.logdir+'/'+'debug.log','a')
    logfile.write(content)
    logfile.flush()
    logfile.close()


def process_carriage_returns(text):
    """
    Process carriage returns (\r) in text by keeping only the last segment per line.
    This simulates terminal behavior where \r returns to line start and overwrites.
    Used to clean up progress bar output for storage (logs/OpenSearch).
    """
    if not text:
        return text
    
    lines = text.split('\n')
    processed = []
    for line in lines:
        if '\r' in line:
            segments = line.split('\r')
            # Filter out empty segments and take the last non-empty one
            non_empty = [s for s in segments if s]
            result = non_empty[-1] if non_empty else ''
            processed.append(result)
        else:
            processed.append(line)
    
    result_text = '\n'.join(processed)
    if debug_enabled and '\r' in text:
        debug_print(f"[CR DEBUG] Output length: {len(result_text)}, lines: {len(processed)}")
    
    return result_text


def log(what, cron, group, instance, time, machine='', code=0, out='', status=''):
    try:
        logfile_name = params.logdir+'/'+cron+'.log'
        logfile = open(logfile_name,'a')
    except Exception as e:
        print(f"[MAIN] Could not open logfile {logfile_name}: ", e, flush=True)
        return

    # Process carriage returns for clean output in logs/OpenSearch
    out_processed = process_carriage_returns(out)

    if what == 'start':
        content = "###### Starting %s at %s ################\n" % (instance, time)
    elif what == 'machine_start':
        content = "###### Starting %s on %s at %s ################\n" % (instance, machine, time)
    elif what == 'no_machines':
        content = "!!!!!! No targets matched for %s !!!!!!\n" % instance
    elif what == 'test_ping_failed':
        content = "!!!!!! SALT-MASTER ERROR during test.ping for %s at %s !!!!!!\n%s\n" % (instance, time, out_processed)
    elif what == 'wrapper_failed':
        content = "!!!!!! Wrapper failed for %s on %s at %s !!!!!!\n%s\n" % (instance, machine, time, out_processed)
    elif what == 'end':
        content = "###### Finished %s at %s ################\n" % (instance, time)
    elif what == 'overlap':
        content = "###### Overlap detected on %s at %s ################\n" % (instance, time)
    else:
        content = """########## %s from %s ################
**** Exit Code %d ******
%s
####### END %s from %s at %s #########
""" % (machine, instance, code, out_processed, machine, instance, time)

    logfile.write(content)
    logfile.flush()
    logfile.close()

    if use_es:
        doc = { 'job_name': cron, "group": group, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out_processed, 'msg_type': what } 
        index_name = '%s-%s' % (params.elasticsearch_index, date.today().strftime('%Y.%m.%d'))
        try:
            #es.indices.create(index=index_name, ignore=400)
            es.index(index=index_name, doc_type='_doc', body=doc, request_timeout=20)
        except Exception as e:
            #print("Can't write to elasticsearch", doc, flush=True)
            print('[MAIN]', e, flush=True)

    if use_opensearch:
        doc = { 'job_name': cron, "group": group, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out_processed, 'msg_type': what } 
        index_name = '%s-%s' % (params.opensearch_index, date.today().strftime('%Y.%m.%d'))
        try:
            #es.indices.create(index=index_name, ignore=400)
            opensearch.index(index=index_name, body=doc, request_timeout=20)
        except Exception as e:
            #print("Can't write to opensearch", doc)
            print('[MAIN]', e, flush=True)

def gettimeline(client, start_date, end_date, req_id, timeline, index_name):
    # Build the query with a date range filter
    query= {
        "query": {
            "bool" : {
                "must" : [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": start_date,
                                "lte": end_date
                            }
                        }
                    },
                    {
                        "terms": {
                            "msg_type": ["machine_start", "machine_result"]
                         }
                    }  
                    ]
                }
            }
        }
    result = client.search(index=index_name, body=query, scroll='1m')
    new_timeline_content = []
    scroll_id = None
    try:
        if 'hits' in result:
            # Use the scroll API to fetch all documents
            while True:
                scroll_id = result['_scroll_id']
                hits = result['hits']['hits']
                if not hits:
                    break  # Break out of the loop when no more documents are returned

                for hit in hits:
                    cron = hit['_source']['job_name']
                    job_instance = hit['_source']['job_instance']
                    timestamp = hit['_source']['@timestamp']
                    ret_code = hit['_source']['return_code']
                    msg_type = hit['_source']['msg_type']
                    new_timeline_content.append({'cron': cron, 'timestamp': timestamp, 'ret_code': ret_code, 'msg_type': msg_type, 'job_instance':job_instance })
                result = client.scroll(scroll_id=scroll_id, scroll='1m')

    except Exception as e:
        # Handle transport or other errors
        print(f"[MAIN] Error occurred during timeline search: {e}", flush=True)
    finally:
        if scroll_id:
            # Clear the scroll context when done
            try:
                client.clear_scroll(scroll_id=scroll_id)
            except:
                pass
    
    new_timeline_content = sorted(new_timeline_content, key=lambda x: x['timestamp'])

    if ('content' not in timeline) or (new_timeline_content != timeline['content']):
        timeline['content'] = new_timeline_content
        timeline['id'] = req_id
        timeline['serial'] = datetime.now(timezone.utc).timestamp()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--configdir', default='/etc/saltpeter', help='Configuration directory location')
    parser.add_argument('-v', '--version', action='store_true', help='Print version and exit')
    cli_args = parser.parse_args()

    if cli_args.version:
        print("[MAIN] Saltpeter version ", version.__version__)
        exit(0)
    
    # Initialize global variables before first use
    global bad_crons
    global bad_files
    global processlist
    global use_es
    global use_opensearch
    bad_files = []
    bad_crons = []  # Will be properly initialized with manager later
    
    # Load config from YAML (includes defaults in readconfig)
    global params
    params = type('Args', (), {'configdir': cli_args.configdir})()
    
    (_, _, yaml_config) = readconfig(params.configdir)
    
    # Apply YAML config to params object
    for key, value in yaml_config.items():
        setattr(params, key, value)
    
    # Set global debug flags
    global debug_enabled, debug_flag_shared
    debug_enabled = params.debug

    # Initialize remaining globals
    use_es = False
    use_opensearch = False
    last_run = {}
    processlist = {}

    manager = multiprocessing.Manager()
    running = manager.dict()
    config = manager.dict()
    state = manager.dict()
    commands = manager.list()
    bad_crons = manager.list()
    timeline = manager.dict()
    state_update_queues = manager.dict()  # job_instance → Queue for state updates from WebSocket
    debug_flag = manager.Value('b', params.debug)  # Shared boolean for debug flag
    debug_flag_shared = debug_flag  # Set global reference for debug_print
    last_maintenance_log = datetime.now(timezone.utc)

    #timeline['content'] = []
    #timeline['serial'] = datetime.now(timezone.utc).timestamp()
    #timeline['id'] = ''
    
    # Start the WebSocket server for machine communication (pass commands queue for bidirectional communication)
    ws_server = multiprocessing.Process(
        target=machines_endpoint.start_websocket_server,
        args=(params.machines_ws_bind_addr, params.machines_ws_port, state, running, log, commands, state_update_queues, debug_flag),
        name='machines_endpoint'
    )
    ws_server.start()
    print(f"[MAIN] Machines WebSocket server listening on {params.machines_ws_bind_addr}:{params.machines_ws_port}, advertising ws://{params.default_saltpeter_server_host}:{params.machines_ws_port}", flush=True)
    
    #start the UI endpoint
    if params.api_ws:
        a = multiprocessing.Process(target=ui_endpoint.start, args=(params.api_ws_bind_addr,params.api_ws_port,config,running,state,commands,bad_crons,timeline,debug_flag), name='ui_endpoint')
        a.start()

    if params.elasticsearch != '':
        from elasticsearch import Elasticsearch
        use_es = True
        global es
        es = Elasticsearch(params.elasticsearch, maxsize=50)

    if params.opensearch != '':
        from opensearchpy import OpenSearch
        use_opensearch = True
        global opensearch
        opensearch = OpenSearch(params.opensearch, maxsize=50, useSSL=False, verify_certs=False)


    #main loop
    prev = datetime.now(timezone.utc)
    while True:
        now = datetime.now(timezone.utc)
        
        newconfig = readconfig(params.configdir)
        
        # Always update crons and maintenance (readconfig handles parse errors per-file)
        crons_changed = 'crons' not in config or config['crons'] != newconfig[0]
        maintenance_changed = 'maintenance' not in config or config['maintenance'] != newconfig[1]
        
        if crons_changed or maintenance_changed:
            config['crons'] = newconfig[0]
            config['maintenance'] = newconfig[1]
            config['serial'] = now.timestamp()
        
        # Only update saltpeter_config if present in YAML (keep old if missing/empty)
        yaml_config = newconfig[2]
        if yaml_config:
            saltpeter_config_changed = 'saltpeter_config' not in config or config['saltpeter_config'] != yaml_config
            if saltpeter_config_changed:
                config['saltpeter_config'] = yaml_config
                # Update params from YAML config
                for key, value in yaml_config.items():
                    setattr(params, key, value)
                # Update global debug flag if changed
                old_debug = debug_flag.value
                debug_enabled = params.debug
                # Update shared debug flag for all processes
                debug_flag.value = params.debug
                if old_debug != params.debug:
                    print(f"[MAIN] Debug mode changed: {old_debug} -> {params.debug}", flush=True)
        
        # timeline
        for cmd in commands:
            if 'get_timeline' in cmd:
                timeline_start_date = cmd['get_timeline']['start_date']
                timeline_end_date = cmd['get_timeline']['end_date']
                timeline_id = cmd['get_timeline']['id']
                procname = 'timeline'
                if use_es:
                    index_name = '%s*' % params.elasticsearch_index
                    p_timeline = multiprocessing.Process(target=gettimeline,\
                            args=(es,timeline_start_date, timeline_end_date, timeline_id, timeline, index_name), name=procname)
                    p_timeline.start()
                if use_opensearch:
                    index_name = '%s*' % params.opensearch_index
                    p_timeline = multiprocessing.Process(target=gettimeline,\
                            args=(opensearch,timeline_start_date, timeline_end_date, timeline_id, timeline, index_name), name=procname)
                    p_timeline.start()
                commands.remove(cmd)

        maintenance = config['maintenance']
        if maintenance['global']:
            now = datetime.now(timezone.utc)
            if (now - last_maintenance_log).total_seconds() >= 20:
                print("[MAIN] Maintenance mode active, no crons will be started.", flush=True)
                last_maintenance_log = now
        else:
            for name in config['crons'].copy():
                #determine next run based on the the last time the loop ran, not the current time
                result = parsecron(name, config['crons'][name], prev)
                if name not in state:
                    state[name] = {}
                nextrun = prev + timedelta(seconds=result['nextrun'])
                job_state = state[name]
                job_state['next_run'] = nextrun
                state[name] = job_state
                #check if there are any start commands
                runnow = False
                for cmd in commands:
                    #print('COMMAND: ',cmd)
                    if 'runnow' in cmd:
                        if cmd['runnow'] == name:
                            runnow = True
                            commands.remove(cmd)
                if (result != False and now >= nextrun) or runnow:
                    if name not in last_run or last_run[name] < prev:
                        last_run[name] = now 
                        procname = name+'_'+str(int(time.time()))
                        print('[MAIN] Firing %s!' % procname, flush=True)

                        #running[procname] = {'empty': True}
                        p = multiprocessing.Process(target=run,\
                                args=(name,config['crons'][name],procname,running, state, commands, maintenance, state_update_queues, manager), name=procname)

                        processlist[procname] = {}
                        processlist[procname]['cron_name'] = name
                        processlist[procname]['cron_group'] = config['crons'][name]['group']

                        p.start()
        prev = now
        time.sleep(0.05)

        #process cleanup
        processes = multiprocessing.active_children()
        for entry in list(processlist):
            found = False
            for process in processes:
                if entry == process.name:
                    found = True
            if found == False:
                print('[MAIN] Deleting process %s as it must have finished' % entry, flush=True)
                del(processlist[entry])
                if entry in running:
                    del(running[entry])


if __name__ == "__main__":
    main()
