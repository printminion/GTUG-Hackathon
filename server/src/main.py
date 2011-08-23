#!/usr/bin/env python
from google.appengine.dist import use_library
use_library('django', '1.2')
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from apiclient.discovery import build
import httplib2
from oauth2client.appengine import OAuth2Decorator
import settings

decorator = OAuth2Decorator(client_id=settings.CLIENT_ID,
                            client_secret=settings.CLIENT_SECRET,
                            scope=settings.SCOPE,
                            user_agent='mk-tasks')

class MainHandler(webapp.RequestHandler):
  @decorator.oauth_required
  def get(self):
      service = build('tasks', 'v1', http=decorator.http())
      result = service.tasks().list(tasklist='@default').execute()
      tasks = result.get('items', [])
      self.response.out.write(template.render('templates/index.html',
                                              {'tasks': tasks}))

class TasksHandler(webapp.RequestHandler):
  def add_task(self, title):
      service = build('tasks', 'v1', http=decorator.http())
      result = service.tasks().insert(tasklist='@default', body = { 'title' : title }).execute()

  def post(self):
    self.add_task(self.request.get('key', ''))

class CompositeViewHandler(webapp.RequestHandler):
  def get_tasks(self):
      service = build('tasks', 'v1', http=decorator.http())
      result = service.tasks().list(tasklist='@default').execute()
      tasks = result.get('items', [])
      return tasks

  def get_books(self):
      service = build('books', 'v1', http=decorator.http())
      result = service.volumes().list(q='search term').execute()
      books = result.get('items', [])
      return books
  
  @decorator.oauth_required
  def get(self):
      tasks = self.get_tasks()
      books = self.get_books()
      
      self.response.out.write(template.render('templates/compositeview.html',
                                              {'books': books, 'tasks' : tasks}))
                                                  
application = webapp.WSGIApplication([('/login', MainHandler), ('/', CompositeViewHandler), ('/tasks', TasksHandler)], debug=True)


def main():
  run_wsgi_app(application)
