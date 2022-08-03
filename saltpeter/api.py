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
    def initialize(self, cfg, cmds):
        self.config = cfg
        self.cmds = cmds
        self.subscriptions = []

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
        print('new connection')
        wsconnections.append(self)
        self.write_message(json.dumps(dict({'config': dict(self.config)})))

    def on_message(self, message):
        print('message received %s' % message)
        #self.write_message('received: ' % message)
        try:
            msg = json.loads(message)
        except Exception as e:
            print('could not parse messageas json')
            print(e)
            return

        if 'subscribe' in msg:
            cron = msg['subscribe']
            self.subscriptions.append(cron)
        if 'unsubscribe' in msg:
            cron = msg['unsubscribe']
            self.subscriptions.remove(cron)
        if 'run' in msg:
            cron = msg['run']
            self.cmds.append(dict({'runnow': cron}))


    def on_close(self):
        print('connection closed')
        wsconnections.remove(self)


def ws_update():

    tornado.ioloop.IOLoop.current().add_timeout(timedelta(seconds=2), ws_update)

    global cfgserial
    cfgupdate = False
    if cfgserial != cfg['serial']:
        cfgserial = cfg['serial']
        cfgupdate = True

    if len(wsconnections) > 0:
        for con in wsconnections:
            srrng = rng.copy()
            for cron in srrng:
                if 'started' in srrng[cron]:
                    srrng[cron]['started'] = srrng[cron]['started'].isoformat()
            srst = st.copy()
            lastst = {}
            for cron in srst:
                if 'last_run' in srst[cron]:
                    lastst[cron] = {}
                    lastst[cron]['last_run'] = srst[cron]['last_run'].isoformat()
                    for tgt_key in st[cron]['results']:
                        tgt = srst[cron]['results'][tgt_key]
                        if 'retcode' in tgt and tgt['retcode'] == 0:
                            lastst[cron]['result_ok'] = True
                        else:
                            lastst[cron]['result_ok'] = False
            con.write_message((json.dumps(dict({'running': srrng, 'last_state': lastst}))))
            if cfgupdate:
                con.write_message(json.dumps(dict({'config': dict(cfg), 'sp_version': __version__})))
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



def start(port, config, running, state, commands):
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

    application = tornado.web.Application([
        (r"/ws", WSHandler, dict(cfg=config,cmds=commands)),
        (r"/version", VersionHandler),
        (r"/config", DictReturner, dict(content=config)),
        (r"/running", DictReturner, dict(content=running))
    ])

    application.listen(port)
    ioloop =  tornado.ioloop.IOLoop.current()
    ioloop.add_timeout(timedelta(seconds=2), ws_update)
    ioloop.start()
