from datetime import date
import tornado.escape
import tornado.ioloop
import tornado.web
import tornado.websocket
import json
from datetime import timedelta
from multiprocessing import Manager
from .version import __version__

class VersionHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range")
        self.set_header('Access-Control-Allow-Methods', 'GET, OPTIONS')

    def get(self):
        response = { 'version': '3.5.1',
                     'last_build':  date.today().isoformat() }
        self.write(response)

    def options(self):
        # no body
        self.set_status(204)
        self.finish()

class DictReturner(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range")
        self.set_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
    def options(self):
        # no body
        self.set_status(204)
        self.finish()

    def initialize(self, content):
        self.content = content
    def get(self):
        response = self.content.copy()
        self.write(response)

class WSHandler(tornado.websocket.WebSocketHandler):
    def initialize(self, cfg, cmds, tml):
        self.config = cfg
        self.cmds = cmds
        self.subscriptions = []
        self.tml = tml

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range")
        self.set_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
    def options(self):
        # no body
        self.set_status(204)
        self.finish()
    def check_origin(self, origin):
        return True


    def open(self):
        print('New WS connection')
        wsconnections.append(self)
        send_data(self,True,True)

    def on_message(self, message):
        print('Message received %s' % message)
        #self.write_message('received: ' % message)
        try:
            msg = json.loads(message)
        except Exception as e:
            print('Could not parse message as json')
            print(e)
            return

        if 'subscribe' in msg:
            cron = msg['subscribe']
            self.subscriptions.append(cron)
            send_data(self,False,False)
        if 'unsubscribe' in msg:
            cron = msg['unsubscribe']
            self.subscriptions.remove(cron)
        if 'run' in msg:
            cron = msg['run']
            self.cmds.append(dict({'runnow': cron}))
        if 'killCron' in msg:
            cron = msg['killCron']
            self.cmds.append(dict({'killcron': cron}))
        if 'getTimeline' in msg:
            timeline_params = msg['getTimeline']
            self.cmds.append(dict({'get_timeline': timeline_params}))



    def on_close(self):
        print('WS connection closed')
        wsconnections.remove(self)

def send_data(con, cfgupdate, tmlupdate):
    if cfgupdate:
        con.write_message(json.dumps(dict({'config': dict(cfg), 'sp_version': __version__})))
    srrng = rng.copy()
    rng_names = []
    for cron in srrng:
        rng_names.append(srrng[cron]['name'])
        if 'started' in srrng[cron]:
            srrng[cron]['started'] = srrng[cron]['started'].isoformat()
    srst = st.copy()
    lastst = {}
    for cron in srst:
        if 'last_run' in srst[cron] and srst[cron]['last_run'] != '':
            lastst[cron] = {}
            lastst[cron]['last_run'] = srst[cron]['last_run'].isoformat()
            if 'results' in srst[cron] and len(srst[cron]['results']) > 0:
                lastst[cron]['result_ok'] = True
                false_result_number = 0
                for tgt_key in srst[cron]['results']:
                    tgt = srst[cron]['results'][tgt_key]
                    if cron not in rng_names:
                        if 'retcode' not in tgt or (tgt['retcode'] != 0 and tgt['retcode'] != "0"):
                            false_result_number += 1
                    if false_result_number == len(srst[cron]['results']):
                        lastst[cron]['result_ok'] = False
            else:
                lastst[cron]['result_ok'] = False

    con.write_message((json.dumps(dict({'running': srrng, 'last_state': lastst}))))
    for cron in cfg['crons']:
        if cron in con.subscriptions:
            srcron = st[cron].copy()
            if 'next_run' in srcron:
                srcron['next_run'] = srcron['next_run'].isoformat()
            if 'last_run' in srcron:
                srcron['last_run'] = srcron['last_run'].isoformat()

            if 'results' in srcron:
                for m in srcron['results']:
                    if 'starttime' in srcron['results'][m] and srcron['results'][m]['starttime'] != '':
                        srcron['results'][m]['starttime'] = srcron['results'][m]['starttime'].isoformat()
                    if 'endtime' in srcron['results'][m] and srcron['results'][m]['endtime'] != '':
                        srcron['results'][m]['endtime'] = srcron['results'][m]['endtime'].isoformat()

            con.write_message(json.dumps(dict({cron: srcron})))

    if tmlupdate:
        con.write_message((json.dumps(dict({'timeline': tml.copy()}))))


def ws_update():

    tornado.ioloop.IOLoop.current().add_timeout(timedelta(seconds=2), ws_update)

    global cfgserial
    cfgupdate = False
    if cfgserial != cfg['serial']:
        cfgserial = cfg['serial']
        cfgupdate = True


    global tmlserial
    tmlupdate = False
    if 'id' in tml:
        if tmlserial != tml['id']:
            tmlserial = tml['id']
            tmlupdate = True

    if len(wsconnections) > 0:
        for con in wsconnections:
            send_data(con, cfgupdate, tmlupdate)


def start(port, config, running, state, commands, bad_crons, timeline ):
    global cfg
    cfg = config
    global wsconnections
    wsconnections = []
    global rng
    rng = running
    global cfgserial
    cfgserial = ''
    global st
    st = state
    
    global tmlserial
    tmlserial = ''
    global tml
    tml = timeline

    application = tornado.web.Application([
        (r"/ws", WSHandler, dict(cfg=config,cmds=commands,tml=timeline)),
        (r"/version", VersionHandler),
        (r"/config", DictReturner, dict(content=config)),
        (r"/running", DictReturner, dict(content=running)),
        (r"/timeline", DictReturner, dict(content=timeline))
    ])

    application.listen(port)
    ioloop =  tornado.ioloop.IOLoop.current()
    ioloop.add_timeout(timedelta(seconds=2), ws_update)
    ioloop.start()
