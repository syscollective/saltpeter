#!/usr/bin/env python3

from saltpeter import api
import json
import os
import argparse
import re
import yaml
import time
from datetime import datetime,timedelta,date
from crontab import CronTab
import multiprocessing

def readconfig(configdir):
    global bad_files
    config = {}
    for f in os.listdir(configdir):
        if not re.match('^.+\.yaml$',f):
            continue
        try:
            config_string = open(configdir+'/'+f,'r').read()
            config.update(yaml.load(config_string))
            if f in bad_crons:
                bad_files.remove(f)
        except Exception as e:
            if f not in bad_files:
                print('Could not parse file %s: %s' % (f,e))
                bad_files.append(f)
    return config


def parsecron(name,data):
    try:
        dow = data['dow']
        dom = data['dom']
        mon = data['mon']
        hour = data['hour']
        minute = data['min']
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
            print('Could not parse executin time in "%s":' % name)
            print(e)
            bad_crons.append(name)
        return False

    if name in bad_crons:
        bad_crons.remove(name)
    ret['nextrun'] = entry.next(now=datetime.utcnow()-timedelta(seconds=1),default_utc=True)
    return ret


def run(name,data,procname,running):
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

    now = datetime.utcnow()
    log(cron=name, what='start', instance=procname, time=now)
    minion_ret = salt.cmd(targets, 'test.ping', tgt_type=target_type)
    minions = list(minion_ret)
    targets_list = minions

    if 'number_of_targets' in data and data['number_of_targets'] != 0:
        import random
        random.shuffle(minions)
        targets_list = minions[:data['number_of_targets']]

    if 'batch_size' in data and data['batch_size'] != 0:
        chunk = []
        count = 0
        results = {}
        for t in targets_list:
            count += 1
            chunk.append(t)
            if len(chunk) == data['batch_size'] or count == len(targets_list):
                running[procname]=  { 'started': str(now), 'name': name, 'machines': chunk }
                try:
                    generator = salt.cmd_iter(chunk, 'cmd.run', cmdargs,
                            tgt_type='list', full_return=True)
                    for i in generator:
                        #print "Generator item: ", chunk, i
                        m = i.keys()[0]
                        r = i[m]['retcode']
                        o = i[m]['ret']
                        results[m] = { 'ret': o, 'retcode': r, 'endtime': datetime.utcnow() }
                        running[procname]['machines'].remove(m)
                except Exception as e:
                    print('Exception triggered in run() at "batch_size" condition', e)
                    chunk = []
    else:
        running[procname]=  { 'started': str(now), 'name': name, 'machines': targets_list }
        try:
            generator = salt.cmd_iter(targets_list, 'cmd.run', cmdargs,
                    tgt_type='list', full_return=True)
            results = {}
            for i in generator:
                m = i.keys()[0]
                r = i[m]['retcode']
                o = i[m]['ret']
                results[m] = { 'ret': o, 'retcode': r, 'endtime': datetime.utcnow() }
                running[procname]['machines'].remove(m)
        except Exception as e:
            print('Exception triggered in run()', e)

    if len(results) > 0:
        for machine in results:
            #check if result is Bool in a probably retarded way
            if type(results[machine]) == type(True):
                log(what='machine_result',cron=name, instance=procname, machine=machine,
                        code=1, out='Bool output: %r' % results[machine],
                        time=results[machine]['endtime'])
            else:
                log(what='machine_result',cron=name, instance=procname, machine=machine,
                        code=results[machine]['retcode'], out=results[machine]['ret'],
                        time=results[machine]['endtime'])
    else:
        log(cron=name, what='no_machines', instance=procname, time=datetime.now())

def debuglog(content):
    logfile = open(args.logdir+'/'+'debug.log','a')
    logfile.write(content)
    logfile.flush()
    logfile.close()


def log(what, cron, instance, time, machine='', code=0, out='', status=''):
    logfile = open(args.logdir+'/'+cron+'.log','a')
    if what == 'start':
        content = "###### Starting %s at %s ################\n" % (instance, time)
    elif what == 'no_machines':
        content = "!!!!!! No targets matched for %s !!!!!!\n" % instance
    elif what == 'end':
        content = "###### Finished %s at %s ################\n" % (instance, time)
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
            es.index(index=index_name, doc_type='saltpeter', body=doc, request_timeout=20)
        except Exception as e:
            print("Can't write to elasticsearch", doc)
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

    parser.add_argument('-i', '--index', default='saltpeter',\
            help='Elasticsearch index name')


    global args
    args = parser.parse_args()

    global bad_crons
    global bad_files
    global processlist
    global use_es
    use_es = False
    bad_crons = []
    bad_files = []
    last_run = {}
    processlist = {}

    manager = multiprocessing.Manager()
    running = manager.dict()
    config = manager.dict()
    
    #start the api
    if args.api:
        a = multiprocessing.Process(target=api.start, args=(args.port,config,running,), name='api')
        a.start()

    if args.elasticsearch != '':
        from elasticsearch import Elasticsearch
        use_es = True
        global es
        es = Elasticsearch(args.elasticsearch,maxsize=50)

    #main loop
    while True:
        
        config['crons'] = readconfig(args.configdir)
        for name in config['crons']:
            result = parsecron(name,config['crons'][name])
            if result == False:
                continue
            if result['nextrun'] < 1:
                if name not in last_run or \
                        datetime.utcnow() - last_run[name] > timedelta(seconds=1):
                    last_run[name] = datetime.utcnow()
                    procname = name+'_'+str(int(time.time()))
                    print('Firing %s!' % procname)

                    #running[procname] = {'empty': True}
                    p = multiprocessing.Process(target=run,\
                            args=(name,config['crons'][name],procname,running), name=procname)
                    processlist[procname] = {}
                    if 'soft_timeout' in result:
                        processlist[procname]['soft_timeout'] = \
                                datetime.utcnow()+timedelta(seconds = result['soft_timeout'])
                    if 'hard_timeout' in result:
                        processlist[procname]['hard_timeout'] = \
                                datetime.utcnow()+timedelta(seconds = result['hard_timeout']-1)
                    p.start()
        time.sleep(0.5)

        #process cleanup and timeout enforcing
        processes = multiprocessing.active_children()
        for entry in list(processlist):
            found = False
            for process in processes:
                if entry == process.name:
                    found = True
                    if 'soft_timeout' in processlist[entry]  and \
                            processlist[entry]['soft_timeout'] < datetime.utcnow():
                        timeout('soft',process)
                    if 'hard_timeout' in processlist[entry] and \
                            processlist[entry]['hard_timeout'] < datetime.utcnow():
                        timeout('hard',process)
            if found == False:
                print('Deleting process %s as it must have finished' % entry)
                name = entry.split('_')[0]
                log(cron=name, what='end', instance=entry, time=datetime.utcnow())

                del(processlist[entry])
                if entry in running:
                    del(running[entry])
        #print running


if __name__ == "__main__":
    main()
