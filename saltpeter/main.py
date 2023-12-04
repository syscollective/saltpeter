Updated code to handle log file error

except Exception as e:
    print('Exception triggered in run()', e)

log(cron=name, what='end', instance=procname, time=datetime.now(timezone.utc))

def debuglog(content):
    try:
        logfile = open(args.logdir+'/'+'debug.log','a')
    except Exception as e:
        print(f"Could not open debug logfile: {args.logdir+'/'+'debug.log'}", e)
        return
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
