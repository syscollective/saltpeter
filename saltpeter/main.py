#!/usr/bin/env python3

from saltpeter import api, version
import json
import os
import argparse
import re
import yaml
import time
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
                print('Could not parse file %s: %s' % (f,e), flush=True)
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
            print('Missing required %s property from "%s"' % (e,name), flush=True)
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
            print('Could not parse execution time in "%s":' % name, flush=True)
            print(e, flush=True)
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



def processresults(client,commands,job,name,group,procname,running,state,targets):

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
            print(name, i[m], flush=True)
            if 'failed' in i[m] and i[m]['failed'] == True:
                print(f"Getting info about job {name} jid: {jid} every 10 seconds", flush=True)
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
    import salt.client
    salt = salt.client.LocalClient()
    targets = data['targets']
    target_type = data['target_type']
    cmdargs = [data['command']]
    env = {'SP_JOB_NAME': name, 'SP_JOB_INSTANCE_NAME': procname}
    cmdargs.append('env=' + str(env))
    if 'cwd' in data:
        cmdargs.append('cwd=' + data['cwd'])
    if 'user' in data:
        cmdargs.append('runas=' + data['user'])
    if 'timeout' in data:
        cmdargs.append('timeout=' + str(data['timeout']))

    now = datetime.now(timezone.utc)
    running[procname] = {'started': now, 'name': name, 'machines': []}
    with statelocks[name]:
        tmpstate = state[name].copy()
        tmpstate['last_run'] = now
        tmpstate['overlap'] = False
        state[name] = tmpstate
    log(cron=name, group=data['group'], what='start', instance=procname, time=now)

    # ping the minions and parse the result
    ret_job = salt.run_job(targets, 'test.ping', tgt_type=target_type)
    jid = ret_job['jid']
    jid_targets = ret_job['minions']

    poll_interval = 2
    poll_count = 0
    targets_up = []
    targets_down = []
    minion_ret = {}
    while True:
        minion_ret_raw = list(salt.get_cli_returns(jid, targets))
        if minion_ret_raw:
            minion_ret = {key: value['ret'] for m in minion_ret_raw for key, value in m.items()}
            targets_up = list(minion_ret)
            break
        if poll_count == 10:
            break
        # Wait before polling again
        time.sleep(poll_interval)
        poll_count = poll_count + 1

    targets_down = list(set(jid_targets) - set(targets_up))
    for item in targets_down:
        minion_ret[item] = False

    targets_list = jid_targets.copy()
    #print(name, minion_ret)
    #print(name, targets_list)
    ###

    dead_targets = []
    with statelocks[name]:
        tmpstate = state[name]
        tmpstate['targets'] = targets_list.copy()
        tmpstate['results'] = {}

        for tgt in targets_list.copy():
            if minion_ret[tgt] == False:
                targets_list.remove(tgt)
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
    if 'number_of_targets' in data and data['number_of_targets'] != 0:
        import random
        # targets chosen at random
        random.shuffle(targets_list)
        targets_list = targets_list[:data['number_of_targets']]

    if 'batch_size' in data and data['batch_size'] != 0:
        chunk = []
        count = 0
        for t in targets_list:
            count += 1
            chunk.append(t)
            if len(chunk) == data['batch_size'] or count == len(targets_list):

                try:
                    # this should be nonblocking
                    job = salt.run_job(chunk, 'cmd.run', cmdargs,
                                       tgt_type='list', listen=True)

                    # update running list and state
                    running[procname] = {'started': now, 'name': name, 'machines': chunk}
                    processstart(chunk, name, data['group'], procname, state)
                    # this should be blocking
                    processresults(salt, commands, job, name, data['group'], procname, running, state, chunk)
                    chunk = []
                except Exception as e:
                    print('Exception triggered in run() at "batch_size" condition', e, flush=True)
                    chunk = []
    else:
        running[procname] = {'started': now, 'name': name, 'machines': targets_list}
        starttime = datetime.now(timezone.utc)

        try:
            job = salt.run_job(targets_list, 'cmd.run', cmdargs,
                               tgt_type='list', listen=True)
            processstart(targets_list, name, data['group'], procname, state)
            # this should be blocking
            processresults(salt, commands, job, name, data['group'], procname, running, state, targets_list)

        except Exception as e:
            print('Exception triggered in run()', e, flush=True)

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
        print(f"Could not open logfile {logfile_name}: ", e, flush=True)
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
            print(e, flush=True)

    if use_opensearch:
        doc = { 'job_name': cron, "group": group, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out, 'msg_type': what } 
        index_name = 'saltpeter-%s' % date.today().strftime('%Y.%m.%d')
        try:
            #es.indices.create(index=index_name, ignore=400)
            opensearch.index(index=index_name, body=doc, request_timeout=20)
        except Exception as e:
            #print("Can't write to opensearch", doc)
            print(e, flush=True)

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

    except TransportError as e:
        # Handle the transport error
        print(f"TransportError occurred: {e}", flush=True)
    finally:
        if scroll_id:
            # Clear the scroll context when done
            client.clear_scroll(scroll_id=scroll_id)
    
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
        print("Saltpeter version ", version.__version__)
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
    statelocks = {}
    commands = manager.list()
    bad_crons = manager.list()
    timeline = manager.dict()
    last_maintenance_log = datetime.now(timezone.utc)

    #timeline['content'] = []
    #timeline['serial'] = datetime.now(timezone.utc).timestamp()
    #timeline['id'] = ''
    
    #start the api
    if args.api:
        a = multiprocessing.Process(target=api.start, args=(args.port,config,running,state,commands,bad_crons,timeline), name='api')
        a.start()

    if args.elasticsearch != '':
        from elasticsearch import Elasticsearch
        use_es = True
        global es
        es = Elasticsearch(args.elasticsearch,maxsize=50)

    if args.opensearch != '':
        from opensearchpy import OpenSearch
        use_opensearch = True
        global opensearch
        opensearch = OpenSearch(args.opensearch,maxsize=50,useSSL=False,verify_certs=False)


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
                print("Maintenance mode active, no crons will be started.", flush=True)
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
                        print('Firing %s!' % procname, flush=True)

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
                print('Deleting process %s as it must have finished' % entry, flush=True)
                del(processlist[entry])
                if entry in running:
                    del(running[entry])


if __name__ == "__main__":
    main()
