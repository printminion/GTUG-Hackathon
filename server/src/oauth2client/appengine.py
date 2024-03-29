# Copyright (C) 2010 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Utilities for Google App Engine

Utilities for making it easier to use OAuth 2.0 on Google App Engine.
"""

__author__ = 'jcgregorio@google.com (Joe Gregorio)'

import httplib2
import pickle

from client import AccessTokenRefreshError
from client import Credentials
from client import Flow
from client import OAuth2WebServerFlow
from client import Storage
from google.appengine.api import memcache
from google.appengine.api import users
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import login_required
from google.appengine.ext.webapp.util import run_wsgi_app

OAUTH2CLIENT_NAMESPACE = 'oauth2client#ns'

class FlowProperty(db.Property):
  """App Engine datastore Property for Flow.

  Utility property that allows easy storage and retreival of an
  oauth2client.Flow"""

  # Tell what the user type is.
  data_type = Flow

  # For writing to datastore.
  def get_value_for_datastore(self, model_instance):
    flow = super(FlowProperty,
                 self).get_value_for_datastore(model_instance)
    return db.Blob(pickle.dumps(flow))

  # For reading from datastore.
  def make_value_from_datastore(self, value):
    if value is None:
      return None
    return pickle.loads(value)

  def validate(self, value):
    if value is not None and not isinstance(value, Flow):
      raise BadValueError('Property %s must be convertible '
                          'to a FlowThreeLegged instance (%s)' %
                          (self.name, value))
    return super(FlowProperty, self).validate(value)

  def empty(self, value):
    return not value


class CredentialsProperty(db.Property):
  """App Engine datastore Property for Credentials.

  Utility property that allows easy storage and retrieval of
  oath2client.Credentials
  """

  # Tell what the user type is.
  data_type = Credentials

  # For writing to datastore.
  def get_value_for_datastore(self, model_instance):
    cred = super(CredentialsProperty,
                 self).get_value_for_datastore(model_instance)
    return db.Blob(pickle.dumps(cred))

  # For reading from datastore.
  def make_value_from_datastore(self, value):
    if value is None:
      return None
    return pickle.loads(value)

  def validate(self, value):
    if value is not None and not isinstance(value, Credentials):
      raise BadValueError('Property %s must be convertible '
                          'to an Credentials instance (%s)' %
                          (self.name, value))
    return super(CredentialsProperty, self).validate(value)

  def empty(self, value):
    return not value


class StorageByKeyName(Storage):
  """Store and retrieve a single credential to and from
  the App Engine datastore.

  This Storage helper presumes the Credentials
  have been stored as a CredenialsProperty
  on a datastore model class, and that entities
  are stored by key_name.
  """

  def __init__(self, model, key_name, property_name, cache=None):
    """Constructor for Storage.

    Args:
      model: db.Model, model class
      key_name: string, key name for the entity that has the credentials
      property_name: string, name of the property that is an CredentialsProperty
      cache: memcache, a write-through cache to put in front of the datastore
    """
    self._model = model
    self._key_name = key_name
    self._property_name = property_name
    self._cache = cache

  def get(self):
    """Retrieve Credential from datastore.

    Returns:
      oauth2client.Credentials
    """
    if self._cache:
      credential = self._cache.get(self._key_name)
      if credential:
        return pickle.loads(credential)
    entity = self._model.get_or_insert(self._key_name)
    credential = getattr(entity, self._property_name)
    if credential and hasattr(credential, 'set_store'):
      credential.set_store(self.put)
      if self._cache:
        self._cache.set(self._key_name, pickle.dumps(credentials))

    return credential

  def put(self, credentials):
    """Write a Credentials to the datastore.

    Args:
      credentials: Credentials, the credentials to store.
    """
    entity = self._model.get_or_insert(self._key_name)
    setattr(entity, self._property_name, credentials)
    entity.put()
    if self._cache:
      self._cache.set(self._key_name, pickle.dumps(credentials))


class CredentialsModel(db.Model):
  """Storage for OAuth 2.0 Credentials

  Storage of the model is keyed by the user.user_id().
  """
  credentials = CredentialsProperty()


class OAuth2Decorator(object):
  """Utility for making OAuth 2.0 easier.

  Instantiate and then use with oauth_required or oauth_aware
  as decorators on webapp.RequestHandler methods.

  Example:

    decorator = OAuth2Decorator(
        client_id='837...ent.com',
        client_secret='Qh...wwI',
        scope='https://www.googleapis.com/auth/buzz',
        user_agent='my-sample-app/1.0')


    class MainHandler(webapp.RequestHandler):

      @decorator.oauth_required
      def get(self):
        http = decorator.http()
        # http is authorized with the user's Credentials and can be used
        # in API calls

  """
  def __init__(self, client_id, client_secret, scope, user_agent,
               auth_uri='https://accounts.google.com/o/oauth2/auth',
               token_uri='https://accounts.google.com/o/oauth2/token'):

    """Constructor for OAuth2Decorator

    Args:
      client_id: string, client identifier.
      client_secret: string client secret.
      scope: string, scope of the credentials being requested.
      user_agent: string, HTTP User-Agent to provide for this application.
      auth_uri: string, URI for authorization endpoint. For convenience
        defaults to Google's endpoints but any OAuth 2.0 provider can be used.
      token_uri: string, URI for token endpoint. For convenience
        defaults to Google's endpoints but any OAuth 2.0 provider can be used.
    """
    self.flow = OAuth2WebServerFlow(client_id, client_secret, scope, user_agent,
      auth_uri, token_uri)
    self.credentials = None
    self._request_handler = None

  def oauth_required(self, method):
    """Decorator that starts the OAuth 2.0 dance.

    Starts the OAuth dance for the logged in user if they haven't already
    granted access for this application.

    Args:
      method: callable, to be decorated method of a webapp.RequestHandler
        instance.
    """
    def check_oauth(request_handler, *args):
      user = users.get_current_user()
      # Don't use @login_decorator as this could be used in a POST request.
      if not user:
        request_handler.redirect(users.create_login_url(
            request_handler.request.uri))
        return
      # Store the request URI in 'state' so we can use it later
      self.flow.params['state'] = request_handler.request.url
      self._request_handler = request_handler
      self.credentials = StorageByKeyName(
          CredentialsModel, user.user_id(), 'credentials').get()

      if not self.has_credentials():
        return request_handler.redirect(self.authorize_url())
      try:
        method(request_handler, *args)
      except AccessTokenRefreshError:
        return request_handler.redirect(self.authorize_url())

    return check_oauth

  def oauth_aware(self, method):
    """Decorator that sets up for OAuth 2.0 dance, but doesn't do it.

    Does all the setup for the OAuth dance, but doesn't initiate it.
    This decorator is useful if you want to create a page that knows
    whether or not the user has granted access to this application.
    From within a method decorated with @oauth_aware the has_credentials()
    and authorize_url() methods can be called.

    Args:
      method: callable, to be decorated method of a webapp.RequestHandler
        instance.
    """
    def setup_oauth(request_handler, *args):
      user = users.get_current_user()
      # Don't use @login_decorator as this could be used in a POST request.
      if not user:
        request_handler.redirect(users.create_login_url(
            request_handler.request.uri))
        return
      self.flow.params['state'] = request_handler.request.url
      self._request_handler = request_handler
      self.credentials = StorageByKeyName(
          CredentialsModel, user.user_id(), 'credentials').get()
      method(request_handler, *args)
    return setup_oauth

  def has_credentials(self):
    """True if for the logged in user there are valid access Credentials.

    Must only be called from with a webapp.RequestHandler subclassed method
    that had been decorated with either @oauth_required or @oauth_aware.
    """
    return self.credentials is not None and not self.credentials.invalid

  def authorize_url(self):
    """Returns the URL to start the OAuth dance.

    Must only be called from with a webapp.RequestHandler subclassed method
    that had been decorated with either @oauth_required or @oauth_aware.
    """
    callback = self._request_handler.request.relative_url('/oauth2callback')
    url = self.flow.step1_get_authorize_url(callback)
    user = users.get_current_user()
    memcache.set(user.user_id(), pickle.dumps(self.flow),
                 namespace=OAUTH2CLIENT_NAMESPACE)
    return url

  def http(self):
    """Returns an authorized http instance.

    Must only be called from within an @oauth_required decorated method, or
    from within an @oauth_aware decorated method where has_credentials()
    returns True.
    """
    return self.credentials.authorize(httplib2.Http())


class OAuth2Handler(webapp.RequestHandler):
  """Handler for the redirect_uri of the OAuth 2.0 dance."""

  @login_required
  def get(self):
    error = self.request.get('error')
    if error:
      errormsg = self.request.get('error_description', error)
      self.response.out.write('The authorization request failed: %s' % errormsg)
    else:
      user = users.get_current_user()
      flow = pickle.loads(memcache.get(user.user_id(), namespace=OAUTH2CLIENT_NAMESPACE))
      # This code should be ammended with application specific error
      # handling. The following cases should be considered:
      # 1. What if the flow doesn't exist in memcache? Or is corrupt?
      # 2. What if the step2_exchange fails?
      if flow:
        credentials = flow.step2_exchange(self.request.params)
        StorageByKeyName(
            CredentialsModel, user.user_id(), 'credentials').put(credentials)
        self.redirect(self.request.get('state'))
      else:
        # TODO Add error handling here.
        pass


application = webapp.WSGIApplication([('/oauth2callback', OAuth2Handler)])

def main():
  run_wsgi_app(application)

#
