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
        config += open(f,'r').read()
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
    if 'sec' in data:
        sec = data['sec']
    else:
        sec = 0
    if 'year' in data:
        year = data['year']
    else:
        year = '*'

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
    return {'nextrun': entry.next(default_utc=True) }

def run(name,data):
    import salt.client
    salt = salt.client.LocalClient()
    targets = data['targets']
    target_type = data['target_type']
    cmdargs = [data['command']]
    if 'cwd' in data:
        cmdargs.append(data['cwd'])
    #except Exception as e:
    #    print(e)
    #    return

    if 'number_of_targets' in data and data['number_of_trgets'] != 0:
        results = salt.cmd_subset(targets, 'cmd.run', cmdargs, tgt_type=target_type,\
                sub=data['number_of_targets'])
    else:
        results = salt.cmd(targets, 'cmd.run', cmdargs, tgt_type=target_type)

    print(results)

parser = argparse.ArgumentParser()

parser.add_argument('-c', '--configdir', default='/etc/saltpeter',\
        help='Configuration directory location')

args = parser.parse_args()

bad_crons = []
last_run = {}
while True:

    crons = readconfig()
    for name in crons:
        result = parsecron(name,crons[name])
        if result == False:
            continue
        print(result)
        if result['nextrun'] < 1:
            if name not in last_run or datetime.utcnow() - last_run[name] > timedelta(seconds=1):
                last_run[name] = datetime.utcnow()
                print('Firing %s!' % name)
                p = multiprocessing.Process(target=run, args=(name,crons[name]))
                p.start()


    print(multiprocessing.active_children())
    time.sleep(0.5)
