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

# this is working