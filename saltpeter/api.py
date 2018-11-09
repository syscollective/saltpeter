from datetime import date
import tornado.escape
import tornado.ioloop
import tornado.web
from multiprocessing import Manager
 
class VersionHandler(tornado.web.RequestHandler):
    def get(self):
        response = { 'version': '3.5.1',
                     'last_build':  date.today().isoformat() }
        self.write(response)

class SharedHandler(tornado.web.RequestHandler):
    def initialize(self, shared):
        self.shared = shared
    def get(self):
        print type(self.shared)
        response = self.shared.copy()
        print response
        self.write(response)
 
class GetGameByIdHandler(tornado.web.RequestHandler):
    def get(self, id):
        response = { 'id': int(id),
                     'name': 'Crazy Game',
                     'release_date': date.today().isoformat() }
        self.write(response)
 
def start(port, sh):
    application = tornado.web.Application([
        (r"/getgamebyid/([0-9]+)", GetGameByIdHandler),
        (r"/version", VersionHandler),
        (r"/shared", SharedHandler, dict(shared=sh))
    ])
    application.listen(port)
    print "api started"
    tornado.ioloop.IOLoop.instance().start()
