from datetime import date
import tornado.escape
import tornado.ioloop
import tornado.web
import tornado.websocket
import json
from datetime import timedelta
from multiprocessing import Manager
 
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
    def initialize(self, cfg):
        self.config = cfg
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
        try:
            msg = json.loads(message)
        except Exception as e:
            print('could not parse messageas json')
            print(e)
            return

        if 'get_details' in msg:
            cron = msg['get_details']
            self.write_message(json.dumps(get_details(cron)))
 
    def on_close(self):
        print('connection closed')
        wsconnections.remove(self)


def get_details(cron):
    try:
        return cfg['crons'][cron]
    except:
        return {}

def ws_update():
    if len(wsconnections) > 0:
        for con in wsconnections:
            con.write_message((json.dumps(dict({'running': dict(rng)}))))
    tornado.ioloop.IOLoop.instance().add_timeout(timedelta(seconds=2), ws_update)

def start(port, config, running):
    global cfg
    cfg = config
    global wsconnections
    global rng
    rng = running

    wsconnections = []
    application = tornado.web.Application([
        (r"/ws", WSHandler, dict(cfg=config)),
        (r"/version", VersionHandler),
        (r"/config", DictReturner, dict(content=config)),
        (r"/running", DictReturner, dict(content=running))
    ])
    application.listen(port)
    tornado.ioloop.IOLoop.instance().add_timeout(timedelta(seconds=2),
                                                 ws_update)
    tornado.ioloop.IOLoop.instance().start()
