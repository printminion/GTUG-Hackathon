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

  @decorator.oauth_aware
  def get(self):
    if decorator.has_credentials():
      service = build('tasks', 'v1', http=decorator.http())
      result = service.tasks().list(tasklist='@default').execute()
      tasks = result.get('items', [])
      for task in tasks:
        task['title_short'] = truncate(task['title'], 26)
      self.response.out.write(template.render('templates/index.html',
                                              {'tasks': tasks}))
    else:
      url = decorator.authorize_url()
      self.response.out.write(template.render('templates/index.html',
                                              {'tasks': [],
                                               'authorize_url': url}))


def truncate(string, length):
  return string[:length] + '...' if len(string) > length else string

application = webapp.WSGIApplication([('/', MainHandler)], debug=True)


def main():
  run_wsgi_app(application)
