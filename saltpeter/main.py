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
#from pprint import pprint

def readconfig(configdir):
    global bad_files
    crons = {}
    saltpeter_maintenance = {'global': False, 'machines': []}
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
                elif parsecron(cron,loaded_config[cron]) is not False:
                    add_config[cron] = loaded_config[cron]
                    add_config[cron]['group'] = group 
            crons.update(add_config)
            if f in bad_files:
                bad_files.remove(f)
        except Exception as e:
            if f not in bad_files:
                print('[MAIN] Could not parse file %s: %s' % (f,e), flush=True)
                bad_files.append(f)
    return (crons, saltpeter_maintenance)


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
    if 'hard_timeout' in data and data['hard_timeout'] != 0:
        ret['timeout'] = data['hard_timeout']
    if 'timeout' in data and data['timeout'] != 0:
        ret['timeout'] = data['timeout']


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
        with statelocks[name]:
            if 'results' in state[name]:
                tmpresults = state[name]['results'].copy()
            else:
                tmpresults = {}
            tmpresults[target] = result
            tmpstate = state[name].copy()
            tmpstate['results'] = tmpresults
            state[name] = tmpstate

        log(cron=name, group=group, what='machine_start', instance=procname,
                time=starttime, machine=target)


def handle_wrapper_failure(machine, retcode, output, name, group, procname, running, state):
    """
    Handle wrapper startup failure: update state, log, and remove from running list
    """
    now = datetime.now(timezone.utc)
    
    # Update state with failure
    with statelocks[name]:
        tmpstate = state[name].copy()
        if 'results' not in tmpstate:
            tmpstate['results'] = {}
        if machine in tmpstate['results']:
            tmpstate['results'][machine]['endtime'] = now
            tmpstate['results'][machine]['retcode'] = retcode
            tmpstate['results'][machine]['ret'] = f"Wrapper execution failed:\n{output}"
        state[name] = tmpstate
    
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


def process_wrapper_results(wrapper_results, name, group, procname, running, state):
    """
    Process salt.cmd() results for wrapper execution
    Returns list of successfully started targets
    """
    targets_confirmed_started = []
    
    for machine, result in wrapper_results.items():
        if isinstance(result, dict) and 'retcode' in result:
            retcode = result['retcode']
            output = result.get('ret', '')
            
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
            print(f"[JOB:{procname}] No response from {machine}", flush=True)
            handle_wrapper_failure(machine, 255, "Target did not respond", name, group, procname, running, state)
    
    return targets_confirmed_started


def processresults_websocket(name, group, procname, running, state, targets, timeout=None):
    """
    Wait for WebSocket-based job results with optional timeout
    Monitors state updates from WebSocket server for job completion
    """
    start_time = time.time()
    check_interval = 1  # Check every second
    
    # Default timeout of 1 hour if not specified
    if timeout is None:
        timeout = 3600
    
    # Heartbeat timeout should be the same as job timeout
    # Communication is retried until the job timeout is reached
    heartbeat_timeout = timeout
    
    # Monitor WebSocket results for job completion
    # Wrappers have already been confirmed started by salt.cmd()
    print(f"[JOB:{procname}] Monitoring WebSocket results for {len(targets)} target(s)...", flush=True)
    pending_targets = set(targets)
    last_heartbeat = {}  # Track last activity time for each target
    job_start_time = time.time()
    
    # Initialize heartbeat tracking for all targets
    for tgt in targets:
        last_heartbeat[tgt] = time.time()
    
    while pending_targets:
        # Check if stop_signal was set (job killed)
        if procname in running and running[procname].get('stop_signal', False):
            print(f"[JOB:{procname}] Stop signal detected, exiting result monitoring", flush=True)
            break
        
        # Check if timeout exceeded (from when jobs actually started, not Salt submission)
        if time.time() - job_start_time > timeout:
            print(f"[JOB:{procname}] Timeout waiting for results from {pending_targets}", flush=True)
            
            # Mark remaining targets as timed out
            now = datetime.now(timezone.utc)
            with statelocks[name]:
                tmpstate = state[name].copy()
                if 'results' not in tmpstate:
                    tmpstate['results'] = {}
                
                for tgt in pending_targets:
                    if tgt in tmpstate['results']:
                        starttime = tmpstate['results'][tgt].get('starttime', now)
                        output = tmpstate['results'][tgt].get('ret', '')
                    else:
                        starttime = tmpstate.get('last_run', now)
                        output = ''
                    
                    output += "\n[SALTPETER ERROR: Job exceeded timeout]\n"
                    
                    tmpstate['results'][tgt] = {
                        'ret': output,
                        'retcode': 124,  # Timeout exit code
                        'starttime': starttime,
                        'endtime': now
                    }
                
                state[name] = tmpstate
            
            # Log timeout for remaining targets
            for tgt in pending_targets:
                output_for_log = ''
                if name in state and 'results' in state[name] and tgt in state[name]['results']:
                    output_for_log = state[name]['results'][tgt].get('ret', '')
                
                log(what='machine_result', cron=name, group=group, instance=procname,
                    machine=tgt, code=124, out=output_for_log, time=now)
            
            break
        
        # Check which targets have completed or timed out
        now = datetime.now(timezone.utc)
        with statelocks[name]:
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
                            print(f"[JOB:{procname}] Target {tgt} completed, removing from pending (retcode: {result.get('retcode')})", flush=True)
                            
                            # Log the completion
                            log(what='machine_result', cron=name, group=group, instance=procname,
                                machine=tgt, code=result.get('retcode', 0), 
                                out=result.get('ret', ''), time=result['endtime'])
                            
                            # Remove from running list
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
                                tmpstate = state[name].copy()
                                starttime = result.get('starttime', now)
                                output = result.get('ret', '')
                                output += f"\n[SALTPETER ERROR: Job lost connection - no heartbeat for {time_since_heartbeat:.0f} seconds]\n"
                                
                                tmpstate['results'][tgt] = {
                                    'ret': output,
                                    'retcode': 253,  # Special code for heartbeat timeout
                                    'starttime': starttime,
                                    'endtime': now
                                }
                                state[name] = tmpstate
                                
                                # Log the failure
                                log(what='machine_result', cron=name, group=group, instance=procname,
                                    machine=tgt, code=253, out=output, time=now)
                                
                                # Remove from pending
                                pending_targets.remove(tgt)
        
        # If all targets completed, exit
        if not pending_targets:
            print(f"[JOB:{procname}] All targets completed for {name}, exiting monitor loop", flush=True)
            break
        
        # Wait before next check
        time.sleep(check_interval)
    
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
            with statelocks[name]:
                tmpstate = state[name].copy()
                if 'results' not in tmpstate:
                    tmpstate['results'] = {}
                tmpstate['results'][m] = result
                state[name] = tmpstate

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
                    with statelocks[name]:
                        tmpstate = state[name].copy()
                        if 'results' not in tmpstate:
                            tmpstate['results'] = {}

                        #print(f'state before check if m not in tmprresults: {state[name]}')
                        if m not in tmpstate['results'] or tmpstate['results'][m]['endtime'] =='':
                            tmpstate['results'][m] = result
                            state[name] = tmpstate

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

            with statelocks[name]:
                tmpstate = state[name].copy()
                tmpstate['results'][tgt] = { 'ret': "Target did not return anything",
                        'retcode': 255,
                        'starttime': starttime,
                        'endtime': now }
                state[name] = tmpstate

                if procname in running and m in running[procname]['machines']:
                    tmprunning = running[procname]
                    tmprunning['machines'].remove(m)
                    running[procname] = tmprunning



def run(name, data, procname, running, state, commands, maintenance):

    if maintenance['global']:
        log(cron=name, group=data['group'], what='maintenance', instance=procname, time=datetime.now(timezone.utc), out="Global maintenance mode active")
        return

    for instance in running.keys():
        if name == running[instance]['name']:
            log(what='overlap', cron=name, group=data['group'], instance=instance,
                 time=datetime.now(timezone.utc))
            with statelocks[name]:
                tmpstate = state[name].copy()
                tmpstate['overlap'] = True
                state[name] = tmpstate
            if 'allow_overlap' not in data or data['allow_overlap'] != 'i know what i am doing!':
                return

    import salt.client
    salt = salt.client.LocalClient()
    targets = data['targets']
    target_type = data['target_type']
    
    # Check if wrapper should be used (default: True)
    use_wrapper = data.get('use_wrapper', True)
    
    # Prepare command arguments based on wrapper usage
    if use_wrapper:
        # Get wrapper script path - use job-specific path if provided, otherwise use default
        wrapper_path = data.get('wrapper_path', args.wrapper_path)
        
        # Prepare environment variables for the wrapper
        websocket_url = f"ws://{args.websocket_host}:{args.websocket_port}"
        
        # Build environment variables to pass to the wrapper
        wrapper_env = {
            'SP_WEBSOCKET_URL': websocket_url,
            'SP_JOB_NAME': name,
            'SP_JOB_INSTANCE_NAME': procname,  # For backwards compatibility
            'SP_JOB_INSTANCE': procname,
            'SP_COMMAND': data['command']
        }
        
        if 'cwd' in data:
            wrapper_env['SP_CWD'] = data['cwd']
        if 'user' in data:
            wrapper_env['SP_USER'] = data['user']
        if 'timeout' in data:
            wrapper_env['SP_TIMEOUT'] = str(data['timeout'])
        
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
    with statelocks[name]:
        tmpstate = state[name].copy()
        tmpstate['last_run'] = now
        tmpstate['overlap'] = False
        tmpstate['group'] = data['group']  # Store group for WebSocket handler
        # Clear previous results when starting a fresh run
        if 'results' in tmpstate:
            del tmpstate['results']
        state[name] = tmpstate
    log(cron=name, group=data['group'], what='start', instance=procname, time=now)

    # Initialize running dict BEFORE test.ping so kill commands work during this phase
    running[procname] = {'started': now, 'name': name, 'machines': [], 'stop_signal': False}

    # ping the minions and parse the result
    minion_ret = salt.cmd(targets, 'test.ping', tgt_type=target_type, timeout=20)

    # minion_ret is already a dict: {minion_id: True/False}
    targets_up = [m for m, ret in minion_ret.items() if ret is True]
    targets_down = [m for m in targets if m not in targets_up]

    targets_list = targets_up.copy()
    #print(name, minion_ret)
    #print(name, targets_list)
    ###

    dead_targets = []
    with statelocks[name]:
        tmpstate = state[name]
        tmpstate['targets'] = targets_list.copy()
        tmpstate['results'] = {}

        # Record non-responsive targets
        for tgt in targets_down:
            tmpstate['results'][tgt] = {'ret': "Target did not respond",
                                        'retcode': 255,
                                        'starttime': now,
                                        'endtime': datetime.now(timezone.utc)}

        state[name] = tmpstate

    # Remove maintenance machines from targets_list and log a message
    for tgt in targets_list.copy():
        if tgt in maintenance['machines']:
            targets_list.remove(tgt)
            log(cron=name, group=data['group'], what='maintenance', instance=procname, time=datetime.now(timezone.utc), machine=tgt, out="Target under maintenance")

    if len(targets_list) == 0:
        log(cron=name, group=data['group'], what='no_machines', instance=procname, time=datetime.now(timezone.utc))
        log(cron=name, group=data['group'], what='end', instance=procname, time=datetime.now(timezone.utc))
        return
    
    # Shuffle targets for random selection/distribution
    if ('number_of_targets' in data and data['number_of_targets'] != 0) or \
       ('batch_size' in data and data['batch_size'] != 0):
        import random
        random.shuffle(targets_list)
    
    if 'number_of_targets' in data and data['number_of_targets'] != 0:
        # Select subset of targets
        targets_list = targets_list[:data['number_of_targets']]

    if 'batch_size' in data and data['batch_size'] != 0:
        chunk = []
        for t in targets_list:
            # Check stop_signal before each batch iteration
            if procname in running and running[procname].get('stop_signal', False):
                print(f"[JOB:{procname}] Stop signal detected during batch processing, aborting remaining batches", flush=True)
                log(cron=name, group=data['group'], what='end', instance=procname, time=datetime.now(timezone.utc))
                del running[procname]
                return
            
            chunk.append(t)
            if len(chunk) == data['batch_size'] or len(chunk) == len(targets_list):

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
                        with statelocks[name]:
                            tmpstate = state[name].copy()
                            if 'results' not in tmpstate:
                                tmpstate['results'] = {}
                            for machine in chunk:
                                if machine in tmpstate['results']:
                                    tmpstate['results'][machine]['endtime'] = now
                                    tmpstate['results'][machine]['retcode'] = 143
                                    tmpstate['results'][machine]['ret'] = '[Job killed before execution]'
                            state[name] = tmpstate
                        chunk = []
                        break
                    
                    # Run command via Salt
                    if use_wrapper:
                        # Use blocking call for wrapper - returns immediately with startup status
                        wrapper_results = salt.cmd(chunk, 'cmd.run', cmdargs, tgt_type='list', timeout=timeout)
                        targets_confirmed_started = process_wrapper_results(wrapper_results, name, data['group'], 
                                                                            procname, running, state)
                        
                        # Monitor WebSocket results for successfully started wrappers
                        if targets_confirmed_started:
                            processresults_websocket(name, data['group'], procname, running, state, 
                                                    targets_confirmed_started, timeout)
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
        tmprunning['machines'] = targets_list
        running[procname] = tmprunning
        starttime = datetime.now(timezone.utc)
        
        # Initialize state structure BEFORE running Salt to prevent race condition
        # where wrappers send output before state is ready
        processstart(targets_list, name, data['group'], procname, state)

        # Check stop_signal before executing wrapper
        if procname in running and running[procname].get('stop_signal', False):
            print(f"[JOB:{procname}] Stop signal detected before wrapper execution, aborting", flush=True)
            # Mark all machines as killed
            now = datetime.now(timezone.utc)
            with statelocks[name]:
                tmpstate = state[name].copy()
                if 'results' not in tmpstate:
                    tmpstate['results'] = {}
                for machine in targets_list:
                    if machine in tmpstate['results']:
                        tmpstate['results'][machine]['endtime'] = now
                        tmpstate['results'][machine]['retcode'] = 143
                        tmpstate['results'][machine]['ret'] = '[Job killed before execution]'
                state[name] = tmpstate
            log(cron=name, group=data['group'], what='end', instance=procname, time=datetime.now(timezone.utc))
            del running[procname]
            return

        try:
            # Run command via Salt
            if use_wrapper:
                # Use blocking call for wrapper - returns immediately with startup status
                wrapper_results = salt.cmd(targets_list, 'cmd.run', cmdargs, tgt_type='list', timeout=timeout)
                targets_confirmed_started = process_wrapper_results(wrapper_results, name, data['group'], 
                                                                    procname, running, state)
                
                # Monitor WebSocket results for successfully started wrappers
                if targets_confirmed_started:
                    processresults_websocket(name, data['group'], procname, running, state, 
                                            targets_confirmed_started, timeout)
            else:
                # Legacy mode - use run_job for non-wrapper execution
                job = salt.run_job(targets_list, 'cmd.run', cmdargs, tgt_type='list', listen=False)
                if targets_list:
                    processresults(salt, commands, job, name, data['group'], procname, running, state, targets_list)

        except Exception as e:
            print(f'[MAIN] Exception in run() for {procname}:', flush=True)
            print(f'[MAIN] Exception type: {type(e).__name__}', flush=True)
            print(f'[MAIN] Exception message: {str(e)}', flush=True)
            print(f'[MAIN] Traceback:', flush=True)
            traceback.print_exc()

    # Clean up running state at the end of job execution
    if procname in running:
        del running[procname]
    
    log(cron=name, group=data['group'], what='end', instance=procname, time=datetime.now(timezone.utc))

def debuglog(content):
    logfile = open(args.logdir+'/'+'debug.log','a')
    logfile.write(content)
    logfile.flush()
    logfile.close()


def log(what, cron, group, instance, time, machine='', code=0, out='', status=''):
    try:
        logfile_name = args.logdir+'/'+cron+'.log'
        logfile = open(logfile_name,'a')
    except Exception as e:
        print(f"[MAIN] Could not open logfile {logfile_name}: ", e, flush=True)
        return

    if what == 'start':
        content = "###### Starting %s at %s ################\n" % (instance, time)
    elif what == 'machine_start':
        content = "###### Starting %s on %s at %s ################\n" % (instance, machine, time)
    elif what == 'no_machines':
        content = "!!!!!! No targets matched for %s !!!!!!\n" % instance
    elif what == 'end':
        content = "###### Finished %s at %s ################\n" % (instance, time)
    elif what == 'overlap':
        content = "###### Overlap detected on %s at %s ################\n" % (instance, time)
    else:
        content = """########## %s from %s ################
**** Exit Code %d ******
%s
####### END %s from %s at %s #########
""" % (machine, instance, code, out, machine, instance, time)

    logfile.write(content)
    logfile.flush()
    logfile.close()

    if use_es:
        doc = { 'job_name': cron, "group": group, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out, 'msg_type': what } 
        index_name = 'saltpeter-%s' % date.today().strftime('%Y.%m.%d')
        try:
            #es.indices.create(index=index_name, ignore=400)
            es.index(index=index_name, doc_type='_doc', body=doc, request_timeout=20)
        except Exception as e:
            #print("Can't write to elasticsearch", doc, flush=True)
            print('[MAIN]', e, flush=True)

    if use_opensearch:
        doc = { 'job_name': cron, "group": group, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out, 'msg_type': what } 
        index_name = 'saltpeter-%s' % date.today().strftime('%Y.%m.%d')
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

    parser.add_argument('-c', '--configdir', default='/etc/saltpeter',\
            help='Configuration directory location')

    parser.add_argument('-l', '--logdir', default='/var/log/saltpeter',\
            help='Log directory location')

    parser.add_argument('-a', '--api', action='store_true' ,\
            help='Start the http api')

    parser.add_argument('-p', '--port', type=int, default=8888,\
            help='HTTP api port')

    parser.add_argument('-w', '--websocket-port', type=int, default=8889,\
            help='WebSocket server port for job communication')

    parser.add_argument('--websocket-host', default='0.0.0.0',\
            help='WebSocket server host')

    parser.add_argument('--wrapper-path', default='/usr/local/bin/sp_wrapper.py',\
            help='Default path to wrapper script on minions (default: /usr/local/bin/sp_wrapper.py)')

    parser.add_argument('-e', '--elasticsearch', default='',\
            help='Elasticsearch host')

    parser.add_argument('-o', '--opensearch', default='',\
            help='Opensearch host')

    parser.add_argument('-i', '--index', default='saltpeter',\
            help='Elasticsearch/Opensearch index name')

    parser.add_argument('-v', '--version', action='store_true' ,\
            help='Print version and exit')

    global args
    args = parser.parse_args()

    if args.version:
        print("[MAIN] Saltpeter version ", version.__version__)
        exit(0)


    global bad_crons
    global bad_files
    global processlist
    global use_es
    use_es = False
    global use_opensearch
    use_opensearch = False
    bad_files = []
    last_run = {}
    processlist = {}

    manager = multiprocessing.Manager()
    running = manager.dict()
    config = manager.dict()
    state = manager.dict()
    global statelocks
    statelocks = manager.dict()
    commands = manager.list()
    bad_crons = manager.list()
    timeline = manager.dict()
    last_maintenance_log = datetime.now(timezone.utc)

    #timeline['content'] = []
    #timeline['serial'] = datetime.now(timezone.utc).timestamp()
    #timeline['id'] = ''
    
    # Start the WebSocket server for machine communication (pass commands queue for bidirectional communication)
    ws_server = multiprocessing.Process(
        target=machines_endpoint.start_websocket_server,
        args=('0.0.0.0', args.websocket_port, state, running, statelocks, log, commands),
        name='machines_endpoint'
    )
    ws_server.start()
    print(f"[MAIN] WebSocket server started on ws://{args.websocket_host}:{args.websocket_port}", flush=True)
    
    #start the UI endpoint
    if args.api:
        a = multiprocessing.Process(target=ui_endpoint.start, args=(args.port,config,running,state,commands,bad_crons,timeline), name='ui_endpoint')
        a.start()

    if args.elasticsearch != '':
        from elasticsearch import Elasticsearch
        use_es = True
        global es
        es = Elasticsearch(args.elasticsearch, maxsize=50)

    if args.opensearch != '':
        from opensearchpy import OpenSearch
        use_opensearch = True
        global opensearch
        opensearch = OpenSearch(args.opensearch, maxsize=50, useSSL=False, verify_certs=False)


    #main loop
    prev = datetime.now(timezone.utc)
    
    while True:
        now = datetime.now(timezone.utc)
        
        newconfig = readconfig(args.configdir)
        if ('crons' not in config or config['crons'] != newconfig[0]) or ('maintenance' not in config or config['maintenance'] != newconfig[1]):
            (config['crons'], config['maintenance']) = newconfig
            config['serial'] = now.timestamp()
        
        # timeline
        for cmd in commands:
            if 'get_timeline' in cmd:
                timeline_start_date = cmd['get_timeline']['start_date']
                timeline_end_date = cmd['get_timeline']['end_date']
                timeline_id = cmd['get_timeline']['id']
                index_name = 'saltpeter*'
                procname = 'timeline'
                if use_es:
                    p_timeline = multiprocessing.Process(target=gettimeline,\
                            args=(es,timeline_start_date, timeline_end_date, timeline_id, timeline, index_name), name=procname)
                    p_timeline.start()
                if use_opensearch:
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
                if name not in statelocks:
                    statelocks[name] = manager.Lock()
                nextrun = prev + timedelta(seconds=result['nextrun'])
                with statelocks[name]:
                    tmpstate = state[name].copy()
                    tmpstate['next_run'] = nextrun
                    state[name] = tmpstate
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
                                args=(name,config['crons'][name],procname,running, state, commands, maintenance), name=procname)

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
