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

class RunningHandler(tornado.web.RequestHandler):
    def initialize(self, running):
        self.running = running
    def get(self):
        response = self.running.copy()
        self.write(response)
 
class GetGameByIdHandler(tornado.web.RequestHandler):
    def get(self, id):
        response = { 'id': int(id),
                     'name': 'Crazy Game',
                     'release_date': date.today().isoformat() }
        self.write(response)
 
def start(port, sh, running):
    application = tornado.web.Application([
        (r"/getgamebyid/([0-9]+)", GetGameByIdHandler),
        (r"/version", VersionHandler),
        (r"/running", RunningHandler, dict(running=running))
    ])
    application.listen(port)
    print "api started"
    tornado.ioloop.IOLoop.instance().start()
