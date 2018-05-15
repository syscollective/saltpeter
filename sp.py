#!/usr/bin/env python

import json
import os
import argparse
import re
import yaml
import time
from datetime import datetime,timedelta
from crontab import CronTab
import multiprocessing


def readconfig():
    config = ''
    for f in os.listdir(args.configdir):
        if not re.match('^.+\.yaml$',f):
            continue
        config += open(args.configdir+'/'+f,'r').read()
    return yaml.load(config)

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
    ret['nextrun'] = entry.next(now=datetime.now()-timedelta(seconds=1),default_utc=True)
    return ret

def run(name,data,instance):
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

    log(cron=name, what='start', instance=procname, time=datetime.now())
    if 'number_of_targets' in data and data['number_of_targets'] != 0:
        results = salt.cmd_subset(targets, 'cmd.run', cmdargs,\
                tgt_type=target_type, sub=data['number_of_targets'], full_return=True)
    elif 'batch_size' in data and data['batch_size'] != 0:
        generator = salt.cmd_batch(targets, 'cmd.run', cmdargs,\
                tgt_type=target_type, batch=str(data['batch_size']), raw=True)
        results = {}
        for i in generator:
            results[i['data']['id']] = { 'ret': i['data']['return'], 'retcode': i['data']['retcode'] }
    else:
        results = salt.cmd(targets, 'cmd.run', cmdargs,\
                tgt_type=target_type, full_return=True)

    if len(results) > 0:
        for machine in results:
            log(what='machine_result',cron=name, instance=procname, machine=machine,\
                    code=results[machine]['retcode'], out=results[machine]['ret'],\
                    time=datetime.now())
    else:
        log(cron=name, what='no_machines', instance=procname, time=datetime.now())

def log(what, cron, instance, time, machine='', code='', out='', status=''):
    logfile = open(args.logdir+'/'+cron+'.log','a')
    if what == 'start':
        content = "###### Starting %s at %s ################\n" % (instance, time)
    elif what == 'no_machines':
        content = "!!!!!! No targets matched for %s !!!!!!\n" % instance
    else:
        content = """########## %s from %s ################
**** Exit Code %d ******
%s
####### END %s from %s at %s #########
""" % (machine, instance, code, out, machine, instance, time)

    logfile.write(content)
    logfile.flush()
    logfile.close()

def timeout(which, process):
    if which == 'hard':
        print('Process %s is about to reach hard timeout! It will be killed soon!' % process.name)
        processlist[process.name]['hard_timeout'] += timedelta(minutes=5)
    if which == 'soft':
        print('Process %s reached soft timeout!' % process.name)
        processlist[process.name]['soft_timeout'] += timedelta(minutes=5)

parser = argparse.ArgumentParser()

parser.add_argument('-c', '--configdir', default='/etc/saltpeter',\
        help='Configuration directory location')

parser.add_argument('-l', '--logdir', default='/var/log/saltpeter',\
        help='Log directory location')


args = parser.parse_args()


bad_crons = []
last_run = {}
processlist = {}
while True:

    crons = readconfig()
    for name in crons:
        result = parsecron(name,crons[name])
        if result == False:
            continue
        if result['nextrun'] < 1:
            if name not in last_run or datetime.utcnow() - last_run[name] > timedelta(seconds=1):
                last_run[name] = datetime.utcnow()
                procname = name+'_'+str(int(time.time()))
                print('Firing %s!' % procname)
                p = multiprocessing.Process(target=run, args=(name,crons[name],procname), name=procname)
                processlist[procname] = {}
                if result.has_key('soft_timeout'):
                    processlist[procname]['soft_timeout'] = datetime.now()+timedelta(seconds = result['soft_timeout'])
                if result.has_key('hard_timeout'):
                    processlist[procname]['hard_timeout'] = datetime.now()+timedelta(seconds = result['hard_timeout']-1)
                p.start()

    time.sleep(0.5)
    processes = multiprocessing.active_children()
    for entry in list(processlist):
        found = False
        for process in processes:
            if entry == process.name:
                found = True
                if processlist[entry].has_key('soft_timeout') and processlist[entry]['soft_timeout'] < datetime.now():
                    timeout('soft',process)
                if processlist[entry].has_key('hard_timeout') and processlist[entry]['hard_timeout'] < datetime.now():
                    timeout('hard',process)
        if found == False:
            print('Deleting process %s as it must have finished' % entry)
            del(processlist[entry])
