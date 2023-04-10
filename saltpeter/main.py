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
    config = {}
    for f in os.listdir(configdir):
        if not re.match('^.+\.yaml$',f):
            continue
        try:
            config_string = open(configdir+'/'+f,'r').read()
            group = f[0:-5]
            loaded_config = yaml.load(config_string, Loader=yaml.FullLoader)
            add_config = {}
            for cron in loaded_config:
                if parsecron(cron,loaded_config[cron]) is not False:
                    add_config[cron] = loaded_config[cron]
                    add_config[cron]['group'] = group 
            config.update(add_config)
            if f in bad_files:
                bad_files.remove(f)
        except Exception as e:
            if f not in bad_files:
                print('Could not parse file %s: %s' % (f,e))
                bad_files.append(f)
    return config


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
            print('Missing required %s property from "%s"' % (e,name))
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
    if 'soft_timeout' in data:
        ret['soft_timeout'] = data['soft_timeout']
    if 'hard_timeout' in data:
        ret['hard_timeout'] = data['hard_timeout']

    try:
        entry = CronTab('%s %s %s %s %s %s %s' % (sec, minute, hour, dom, mon, dow, year))
    except Exception as e:
        if name not in bad_crons:
            print('Could not parse execution time in "%s":' % name)
            print(e)
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

def processstart(chunk,name,procname,state):
    results = {}

    for target in chunk:
        starttime = datetime.now(timezone.utc)
        result = { 'ret': '', 'retcode': '',
            'starttime': starttime, 'endtime': ''}
        #do this crap to propagate changes; this is somewhat acceptable since this object is not modified anywhere else
        if 'results' in state[name]:
            tmpresults = state[name]['results'].copy()
        else:
            tmpresults = {}
        tmpresults[target] = result
        tmpstate = state[name].copy()
        tmpstate['results'] = tmpresults
        state[name] = tmpstate

        log(cron=name, what='machine_start', instance=procname,
                time=starttime, machine=target)


def processresults(client,commands,job,name,procname,running,state,targets):



    jid = job['jid']
    minions = job['minions']


    rets = client.get_iter_returns(jid, minions, block=False, expect_minions=True,timeout=1)
    keepgoing = True

    for i in rets:
        if i is not None:
            m = list(i)[0]
            if 'failed' in i[m] and i[m]['failed'] == True:
                r = 255
                o = "Target did not return data" 
            else:
                r = i[m]['retcode']
                o = i[m]['ret']
            result = { 'ret': o, 'retcode': r, 'starttime': state[name]['results'][m]['starttime'], 'endtime': datetime.now(timezone.utc) }
            if 'results' in state[name]:
                tmpresults = state[name]['results'].copy()
            else:
                tmpresults = {}
            tmpresults[m] = result
            tmpstate = state[name].copy()
            tmpstate['results'] = tmpresults
            state[name] = tmpstate
            tmprunning = running[procname]
            tmprunning['machines'].remove(m)
            running[procname] = tmprunning

            log(what='machine_result',cron=name, instance=procname, machine=m,
                code=r, out=o, time=result['endtime'])
        #time.sleep(1)
        for cmd in commands:
            print('COMMAND: ',cmd)
            if 'killcron' in cmd:
                if cmd['killcron'] == name:
                    commands.remove(cmd)
                    client.run_job(minions, 'saltutil.term_job', [jid], tgt_type='list')



    for tgt in targets:
        if tgt not in minions:
            now = datetime.now(timezone.utc)
            log(what='machine_result',cron=name, instance=procname, machine=tgt,
                code=255, out="Target did not return anything", time=now)

            tmpresults = state[name]['results'].copy()
            tmpresults[tgt] = { 'ret': "Target did not return anything",
                    'retcode': 255,
                    'starttime': state[name]['results'][tgt]['starttime'],
                    'endtime': now }

            tmpstate = state[name].copy()
            tmpstate['results'] = tmpresults
            state[name] = tmpstate
            tmprunning = running[procname]
            tmprunning['machines'].remove(m)
            running[procname] = tmprunning



def run(name,data,procname,running,state,commands):
    #do this check here for the purpose of avoiding sync logging in the main program
    for instance in running.keys():
        if name == running[instance]['name']:
            log(what='overlap', cron=name, instance=instance,
                 time=datetime.now(timezone.utc))
            tmpstate = state[name]
            tmpstate['overlap'] = True
            state[name] = tmpstate
            if 'allow_overlap' not in data or data['allow_overlap'] != 'i know what i am doing!':
                return

    import salt.client
    salt = salt.client.LocalClient()
    targets = data['targets']
    target_type = data['target_type']
    cmdargs = [data['command']]
    if 'cwd' in data:
        cmdargs.append('cwd='+data['cwd'])
    if 'user' in data:
        cmdargs.append('runas='+data['user'])
    if 'hard_timeout' in data:
        cmdargs.append('timeout='+str(data['hard_timeout']))

    now = datetime.now(timezone.utc)
    running[procname]=  { 'started': now, 'name': name , 'machines': []}
    tmpstate = state[name].copy()
    tmpstate['last_run'] = now
    tmpstate['overlap'] = False
    state[name] = tmpstate
    log(cron=name, what='start', instance=procname, time=now)
    minion_ret = salt.cmd(targets, 'test.ping', tgt_type=target_type)
    targets_list = list(minion_ret)
    dead_targets = []
    tmpstate = state[name]
    tmpstate['targets'] = targets_list.copy()
    tmpstate['results'] = {}

    for tgt in targets_list.copy():
        if minion_ret[tgt] == False:
            targets_list.remove(tgt)
            tmpstate['results'][tgt] = { 'ret': "Target did not respond",
                    'retcode': 255,
                    'starttime': now,
                    'endtime': datetime.now(timezone.utc) }

    state[name] = tmpstate
    if len(targets_list) == 0:
        log(cron=name, what='no_machines', instance=procname, time=datetime.now(timezone.utc))
        log(cron=name, what='end', instance=procname, time=datetime.now(timezone.utc))
        return
    if 'number_of_targets' in data and data['number_of_targets'] != 0:
        import random
        #targets chosen at random
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
                    running[procname]=  { 'started': now, 'name': name, 'machines': chunk }
                    processstart(chunk,name,procname,state)
                    #this should be blocking
                    processresults(salt,commands,job,name,procname,running,state,chunk)
                    chunk = []
                except Exception as e:
                    print('Exception triggered in run() at "batch_size" condition', e)
                    chunk = []
    else:
        running[procname]=  { 'started': now, 'name': name, 'machines': targets_list }
        starttime = datetime.now(timezone.utc)

        try:
            job = salt.run_job(targets_list, 'cmd.run', cmdargs,
                    tgt_type='list', listen=True)
            processstart(targets_list,name,procname,state)
            #this should be blocking
            processresults(salt,commands,job,name,procname,running,state,targets_list)

        except Exception as e:
            print('Exception triggered in run()', e)

    log(cron=name, what='end', instance=procname, time=datetime.now(timezone.utc))

def debuglog(content):
    logfile = open(args.logdir+'/'+'debug.log','a')
    logfile.write(content)
    logfile.flush()
    logfile.close()


def log(what, cron, instance, time, machine='', code=0, out='', status=''):
    try:
        logfile_name = args.logdir+'/'+cron+'.log'
        logfile = open(logfile_name,'a')
    except Exception as e:
        print(f"Could not open logfile {logfile_name}: ", e)
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
        doc = { 'job_name': cron, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out, 'msg_type': what } 
        index_name = 'saltpeter-%s' % date.today().strftime('%Y.%m.%d')
        try:
            #es.indices.create(index=index_name, ignore=400)
            es.index(index=index_name, doc_type='_doc', body=doc, request_timeout=20)
        except Exception as e:
            print("Can't write to elasticsearch", doc)
            print(e)

    if use_opensearch:
        doc = { 'job_name': cron, "job_instance": instance, '@timestamp': time,
                'return_code': code, 'machine': machine, 'output': out, 'msg_type': what } 
        index_name = 'saltpeter-%s' % date.today().strftime('%Y.%m.%d')
        try:
            #es.indices.create(index=index_name, ignore=400)
            opensearch.index(index=index_name, body=doc, request_timeout=20)
        except Exception as e:
            #print("Can't write to opensearch", doc)
            print(e)



def timeout(which, process):
    global processlist
    if which == 'hard':
        print('Process %s is about to reach hard timeout! It will be killed soon!'\
                % process.name)
        processlist[process.name]['hard_timeout'] += timedelta(minutes=5)
    if which == 'soft':
        print('Process %s reached soft timeout!' % process.name)
        processlist[process.name]['soft_timeout'] += timedelta(minutes=5)


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
    commands = manager.list()
    bad_crons = manager.list()
    
    #start the api
    if args.api:
        a = multiprocessing.Process(target=api.start, args=(args.port,config,running,state,commands,bad_crons,), name='api')
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
        if 'crons' not in config or config['crons'] != newconfig:
            config['crons'] = newconfig
            config['serial'] = now.timestamp()

        for name in config['crons'].copy():
            #determine next run based on the the last time the loop ran, not the current time
            result = parsecron(name, config['crons'][name], prev)
            if name not in state:
                state[name] = {}
            nextrun = prev + timedelta(seconds=result['nextrun'])
            tmpstate = state[name].copy()
            tmpstate['next_run'] = nextrun
            state[name] = tmpstate
            #check if there are any start commands
            runnow = False
            for cmd in commands:
                print('COMMAND: ',cmd)
                if 'runnow' in cmd:
                    if cmd['runnow'] == name:
                        runnow = True
                        commands.remove(cmd)

            if (result != False and now >= nextrun) or runnow:
                if name not in last_run or last_run[name] < prev:
                    last_run[name] = now 
                    procname = name+'_'+str(int(time.time()))
                    print('Firing %s!' % procname)

                    #running[procname] = {'empty': True}
                    p = multiprocessing.Process(target=run,\
                            args=(name,config['crons'][name],procname,running, state, commands), name=procname)

                    processlist[procname] = {}

                    # this is wrong on multiple levels, to be fixed
                    if 'soft_timeout' in result:
                        processlist[procname]['soft_timeout'] = \
                                now+timedelta(seconds = result['soft_timeout'])
                    if 'hard_timeout' in result:
                        processlist[procname]['hard_timeout'] = \
                                now+timedelta(seconds = result['hard_timeout']-1)
                    p.start()
        prev = now
        time.sleep(0.05)

        #process cleanup and timeout enforcing
        processes = multiprocessing.active_children()
        for entry in list(processlist):
            found = False
            for process in processes:
                if entry == process.name:
                    found = True
                    # this is wrong on multiple levels, to be fixed:
                    if 'soft_timeout' in processlist[entry]  and \
                            processlist[entry]['soft_timeout'] < datetime.now(timezone.utc):
                        timeout('soft',process)
                    if 'hard_timeout' in processlist[entry] and \
                            processlist[entry]['hard_timeout'] < datetime.now(timezone.utc):
                        timeout('hard',process)
            if found == False:
                print('Deleting process %s as it must have finished' % entry)
                del(processlist[entry])
                if entry in running:
                    del(running[entry])


if __name__ == "__main__":
    main()
